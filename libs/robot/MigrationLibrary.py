"""Robot Framework library for manifest-driven bank migration verification.

Verification-only: the source extract is read with S3 read permissions and the target
with a SELECT-only identity. Every check records its masked outcome into the run's
audit report and then fails the Robot test when it did not pass. Raw values of
``pii: true`` columns never reach logs, failure messages, or the report.

Test-support keywords under "local fixture" seed a synthetic S3 stub and a loopback
PostgreSQL stand-in target; they refuse to run unless the environment, endpoints, and
manifest classification are all local/synthetic.
"""

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import yaml
from robot.api import logger
from robot.api.deco import keyword

from libs.adapters.registry import create_source, create_target, create_writer
from libs.engine import reconcile, rules, schema
from libs.engine.models import Contract
from libs.loader import load_expected_rows
from libs.migration import controls, integrity
from libs.migration.audit import MigrationRun
from libs.migration.manifest import load_manifest
from libs.migration.masking import Masker
from libs.robot.ReconciliationLibrary import _expand_env, load_credentials, resolve_credentials

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}
_MAX_LISTED = 10


@dataclass
class TableContext:
    contract: Contract
    masker: Masker
    source_df: Any = None  # pd.DataFrame once the extract is read
    expected_df: Any = None
    source_columns: list = field(default_factory=list)
    lineage: list = field(default_factory=list)
    error: "Exception | None" = None


class MigrationLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_DOC_FORMAT = "REST"

    def __init__(self):
        self.manifest = None
        self.env: dict = {}
        self.run: MigrationRun | None = None
        self.target = None
        self._tables: dict = {}
        self._s3_server = None

    # ----- setup -------------------------------------------------------------

    @keyword("Load Migration")
    def load_migration(self, manifest_path: str, env_file: str):
        """Load and validate the manifest and environment; start an audited run."""
        load_credentials(_REPO_ROOT / ".env")
        self.manifest = load_manifest(manifest_path)
        self.env = _expand_env(yaml.safe_load(Path(env_file).read_text(encoding="utf-8")))
        allowed = self.env.get("allowed_data_classifications") or []
        if self.manifest.classification not in allowed:
            raise PermissionError(
                f"environment {self.env['environment']!r} does not allow "
                f"{self.manifest.classification!r} data (allowed: {allowed})"
            )
        masking = "hmac" if os.environ.get("RECON_MASK_KEY") else "redacted"
        self.run = MigrationRun(
            self.manifest, self.env["environment"], self.env["target"]["type"], masking, _REPO_ROOT
        )
        self._tables = {}
        logger.info(
            f"migration {self.manifest.name} v{self.manifest.version} run {self.run.run_id}: "
            f"{len(self.manifest.enabled_tables())} tables, masking={masking}"
        )
        return self.run.run_id

    @property
    def _audit(self) -> MigrationRun:
        if self.run is None:
            raise RuntimeError("no migration loaded; call Load Migration first")
        return self.run

    @keyword("Get Migration Tables")
    def get_migration_tables(self):
        return self.manifest.enabled_tables()

    @keyword("Connect Migration Target")
    def connect_migration_target(self):
        self.close_migration_target()
        user, pw = resolve_credentials("RO", self.env["target"])
        self.target = create_target(self.env["target"], user=user, password=pw)

    @keyword("Close Migration Target")
    def close_migration_target(self):
        if self.target is not None and not self.target.is_closed():
            self.target.close()
        self.target = None

    def _target(self):
        if self.target is None or self.target.is_closed():
            self.connect_migration_target()
        return self.target

    # ----- per-table source context -----------------------------------------

    def _source_cfg(self, contract: Contract) -> dict:
        # Connection coordinates come from the environment so one contract serves
        # every environment; the contract owns layout (key/prefix/suffix/format).
        return {**contract.raw["source"], **(self.env.get("source_connection") or {})}

    def _ctx(self, table: str) -> TableContext:
        if table not in self.manifest.tables or not self.manifest.tables[table].enabled:
            raise ValueError(f"table {table!r} is not an enabled manifest table")
        ctx = self._tables.get(table)
        if ctx is None:
            contract = self.manifest.tables[table].contract
            ctx = TableContext(contract=contract, masker=Masker.for_contract(contract))
            self._tables[table] = ctx
            try:
                adapter = create_source(self._source_cfg(contract))
                try:
                    ctx.source_df = adapter.read_batch()
                    ctx.source_columns = adapter.schema()
                    ctx.lineage = adapter.lineage() if hasattr(adapter, "lineage") else []
                finally:
                    adapter.close()
                ctx.expected_df = reconcile.expected_target_rows(ctx.source_df, contract)
                self._audit.table(table)["source_rows"] = len(ctx.source_df)
                self._audit.table(table)["source_lineage"] = ctx.lineage
            except Exception as e:  # cached so every check reports the same root cause
                ctx.error = e
        if ctx.error is not None:
            raise RuntimeError(f"source for {table!r} unavailable: {ctx.error}") from ctx.error
        return ctx

    @keyword("Reset Migration Table")
    def reset_migration_table(self, table: str):
        """Drop cached source data so the next check re-reads the extract."""
        self._tables.pop(table, None)

    def _finish(self, table: str, check: str, problems: list, detail=None):
        status = "FAIL" if problems else "PASS"
        self._audit.record(
            table, check, status, {"problems": problems[:_MAX_LISTED], **(detail or {})}
        )
        if problems:
            more = f" (+{len(problems) - _MAX_LISTED} more)" if len(problems) > _MAX_LISTED else ""
            raise AssertionError(f"{table} {check}: {problems[:_MAX_LISTED]}{more}")

    def _guard(self, table: str, check: str, fn):
        try:
            return fn()
        except AssertionError:
            raise
        except Exception as e:
            self._audit.record(table, check, "FAIL", {"error": f"{type(e).__name__}: {e}"})
            raise

    # ----- L0: lineage / header ----------------------------------------------

    @keyword("Verify Source Lineage")
    def verify_source_lineage(self, table: str):
        """Every object read is identified by key + ETag (+ VersionId when required)."""

        def run():
            ctx = self._ctx(table)
            problems = []
            if not ctx.lineage:
                problems.append("source adapter reported no object lineage")
            need_version = bool(self.env.get("require_object_versioning"))
            for obj in ctx.lineage:
                if not obj.get("etag"):
                    problems.append(f"{obj['key']}: missing ETag")
                if need_version and obj.get("version_id") in (None, "", "null"):
                    problems.append(f"{obj['key']}: no VersionId (bucket versioning required)")
            self._finish(table, "source_lineage", problems, {"objects": len(ctx.lineage)})

        self._guard(table, "source_lineage", run)

    @keyword("Verify Source Header")
    def verify_source_header(self, table: str):
        def run():
            ctx = self._ctx(table)
            expected = ctx.contract.source_columns
            problems = []
            if not expected:
                problems.append("contract declares no source.columns")
            elif ctx.source_columns != expected:
                problems.append(f"header mismatch: expected {expected}, got {ctx.source_columns}")
            self._finish(table, "source_header", problems)

        self._guard(table, "source_header", run)

    # ----- L1: data quality ----------------------------------------------------

    @keyword("Verify Source Data Quality")
    def verify_source_data_quality(self, table: str):
        def run():
            ctx = self._ctx(table)
            found = rules.merge_failures(
                rules.evaluate_source_rules(ctx.source_df, ctx.contract),
                rules.evaluate_derived_rules(ctx.expected_df, ctx.contract),
            )
            sample_cols = {ctx.source_df.columns[0], ctx.expected_df.columns[0]}
            pii_samples = bool(sample_cols & ctx.masker.pii_columns)
            problems = [
                {
                    "rule": f.rule_id,
                    "failing_rows": f.failing_rows,
                    "samples": [ctx.masker.token(s) if pii_samples else str(s) for s in f.samples],
                }
                for f in found
            ]
            self._finish(table, "source_data_quality", problems)

        self._guard(table, "source_data_quality", run)

    # ----- L2: target schema -----------------------------------------------------

    @keyword("Verify Target Schema")
    def verify_target_schema(self, table: str):
        def run():
            contract = self.manifest.tables[table].contract
            by_cat = schema.validate_target_schema_by_category(self._target(), contract)
            problems = [f"{aspect}: {e}" for aspect, errs in by_cat.items() for e in errs]
            self._finish(table, "target_schema", problems)

        self._guard(table, "target_schema", run)

    # ----- L3: counts and control totals -----------------------------------------

    @keyword("Verify Row Count")
    def verify_row_count(self, table: str):
        def run():
            ctx = self._ctx(table)
            target_rows = self._target().row_count(ctx.contract.table)
            self._audit.table(table)["target_rows"] = target_rows
            source_rows = len(ctx.source_df)
            problems = []
            if target_rows != source_rows:
                problems.append(f"source rows {source_rows} != target rows {target_rows}")
            expected = ctx.contract.metadata.get("expected_row_count")
            if expected is not None and source_rows != expected:
                problems.append(f"source rows {source_rows} != control count {expected}")
            self._finish(
                table, "row_count", problems, {"source": source_rows, "target": target_rows}
            )

        self._guard(table, "row_count", run)

    @keyword("Verify Control Totals")
    def verify_control_totals(self, table: str):
        """Contract `controls:` — source (exact Decimal) vs target (pushdown aggregate)."""

        def run():
            ctx = self._ctx(table)
            spec = self.manifest.tables[table]
            declared = ctx.contract.raw.get("controls") or []
            problems: list = []
            if not declared and spec.tier == "critical":
                problems.append("critical table declares no control totals")
            for ctl in declared:
                exp = controls.source_values(ctx.expected_df, ctx.contract, ctl)
                act = controls.target_values(self._target(), ctx.contract, ctl)
                for d in controls.compare_control(exp, act, ctl):
                    group_cols = ctl.get("group_by") or []
                    value_col = ctl.get("column") or ""
                    problems.append(
                        {
                            "control": d["control"],
                            "group": [
                                ctx.masker.value(c, v)
                                for c, v in zip(group_cols, d["group"], strict=True)
                            ],
                            "expected": ctx.masker.value(value_col, d["expected"]),
                            "actual": ctx.masker.value(value_col, d["actual"]),
                        }
                    )
            self._finish(table, "control_totals", problems, {"controls": len(declared)})

        self._guard(table, "control_totals", run)

    # ----- L5: record comparison -----------------------------------------------------

    @keyword("Verify Records")
    def verify_records(self, table: str):
        """Full key-based comparison incl. transforms; bounded, fails closed above it."""
        spec = self.manifest.tables[table]
        if not spec.full_compare:
            self._audit.record(table, "records", "SKIPPED", {"reason": "full_compare: false"})
            from robot.libraries.BuiltIn import BuiltIn

            BuiltIn().skip(f"{table}: full compare disabled in manifest; verified at L0-L4 only")

        def run():
            ctx = self._ctx(table)
            target = self._target()
            bound = int(self.env["target"].get("max_rows", 100000))
            rows = target.row_count(ctx.contract.table)
            if rows > bound:
                self._finish(
                    table,
                    "records",
                    [
                        f"target has {rows} rows > full-compare bound {bound}; chunked compare required"
                    ],
                )
            names = [c["name"] for c in ctx.contract.columns]
            actual = target.read_table(ctx.contract.table, columns=names)
            result = reconcile.compare(ctx.expected_df, actual, ctx.contract)
            keys, m = ctx.contract.keys, ctx.masker
            problems = (
                [
                    {"missing_in_target": m.key(keys, k)}
                    for k in result.missing_in_target[:_MAX_LISTED]
                ]
                + [
                    {"extra_in_target": m.key(keys, k)}
                    for k in result.extra_in_target[:_MAX_LISTED]
                ]
                + [
                    {
                        "key": m.key(keys, d.key),
                        "column": d.column,
                        "expected": m.value(d.column, d.expected),
                        "actual": m.value(d.column, d.actual),
                    }
                    for d in result.diffs[:_MAX_LISTED]
                ]
            )
            counts = {
                "missing_in_target": len(result.missing_in_target),
                "extra_in_target": len(result.extra_in_target),
                "column_diffs": len(result.diffs),
            }
            if result.mismatch_count and not problems:
                problems = [counts]
            self._finish(table, "records", problems if result.mismatch_count else [], counts)

        self._guard(table, "records", run)

    # ----- L4: referential integrity ----------------------------------------------

    @keyword("Verify Relationship")
    def verify_relationship(self, rel_id: str):
        rel = next((r for r in self.manifest.relationships if r["id"] == rel_id), None)
        if rel is None:
            raise ValueError(f"unknown relationship {rel_id!r}")
        try:
            child, parent = self._ctx(rel["child"]), self._ctx(rel["parent"])
            src_count, src_samples = integrity.source_orphans(
                child.expected_df, child.contract, parent.expected_df, parent.contract, rel
            )
            tgt_count, tgt_samples = self._target().orphans(
                child.contract.table, rel["column"], parent.contract.table, rel["parent_column"]
            )
        except Exception as e:
            self._audit.record_relationship(rel_id, "FAIL", {"error": f"{type(e).__name__}: {e}"})
            raise
        mask = child.masker
        detail = {
            "source_orphans": src_count,
            "source_samples": mask.values(rel["column"], src_samples),
            "target_orphans": tgt_count,
            "target_samples": mask.values(rel["column"], tgt_samples),
        }
        status = "FAIL" if src_count or tgt_count else "PASS"
        self._audit.record_relationship(rel_id, status, detail)
        if status == "FAIL":
            raise AssertionError(f"relationship {rel_id}: {detail}")

    # ----- evidence ------------------------------------------------------------------

    @keyword("Write Migration Report")
    def write_migration_report(self, out_dir: str):
        """Write migration_report.json + .sha256; returns the overall status."""
        if self.run is None:
            logger.warn("migration run never started; no report written")
            return "NOT_STARTED"
        path = self.run.write(out_dir)
        status = self.run.data["overall_status"]
        logger.info(f"migration report {path}: {status}")
        return status

    @keyword("Get Migration Report")
    def get_migration_report(self):
        return self._audit.data

    # ----- local fixture (synthetic only) ----------------------------------------------

    def _require_local(self):
        env, target = self.env, self.env.get("target", {})
        conn = env.get("source_connection") or {}
        endpoint = urlparse(conn.get("endpoint_url") or "")
        if not (
            str(env.get("environment", "")).startswith("local")
            and env.get("local_fixture")
            and self.manifest.classification == "synthetic"
            and target.get("type") == "postgres"
            and target.get("host") in _LOOPBACK
            and endpoint.hostname in _LOOPBACK
        ):
            raise PermissionError(
                "local fixture keywords require a local environment, loopback endpoints, "
                "and a synthetic manifest"
            )

    def _s3(self):
        import boto3

        conn = self.env["source_connection"]
        return boto3.client(
            "s3",
            endpoint_url=conn["endpoint_url"],
            region_name=conn.get("region_name", "us-east-1"),
        )

    @keyword("Uses Local Migration Fixture")
    def uses_local_migration_fixture(self):
        return bool(self.env.get("local_fixture"))

    @keyword("Start Local Migration Fixture")
    def start_local_migration_fixture(self):
        """Start an S3 stub, upload synthetic extracts, seed the stand-in target."""
        self._require_local()
        from moto.server import ThreadedMotoServer

        fixture = self.env["local_fixture"]
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        self._s3_server = ThreadedMotoServer(port=int(fixture["s3_port"]), verbose=False)
        self._s3_server.start()
        s3, bucket = self._s3(), self.env["source_connection"]["bucket"]
        s3.create_bucket(Bucket=bucket)
        s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        data_dir = _REPO_ROOT / fixture["data_dir"]
        for name in self.manifest.enabled_tables():
            prefix = self.manifest.tables[name].contract.raw["source"]["prefix"]
            for part in sorted((data_dir / name).glob("*.csv")):
                s3.put_object(Bucket=bucket, Key=prefix + part.name, Body=part.read_bytes())
        self.seed_local_target()

    @keyword("Seed Local Target")
    def seed_local_target(self):
        """Stand-in for the bank ETL: load expected rows into loopback PostgreSQL."""
        self._require_local()
        user, pw = resolve_credentials("RW", self.env["target"])
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            for name in self.manifest.enabled_tables():
                self.reset_migration_table(name)
                ctx = self._ctx(name)
                raw = {**ctx.contract.raw, "target": {**ctx.contract.raw["target"]}}
                raw["target"]["schema"] = self.env["target"].get("schema", "public")
                load_expected_rows(conn, Contract(raw=raw, path=ctx.contract.path), ctx.expected_df)
                self.reset_migration_table(name)
        finally:
            conn.close()

    @keyword("Execute Local Target Sql")
    def execute_local_target_sql(self, sql: str):
        """Negative-path tampering of the local stand-in only (recon_rw)."""
        self._require_local()
        user, pw = resolve_credentials("RW", self.env["target"])
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    @keyword("Put Local Source Object")
    def put_local_source_object(self, table: str, file_path: str, name: str):
        """Add an extra part file under the table's prefix in the S3 stub."""
        self._require_local()
        prefix = self.manifest.tables[table].contract.raw["source"]["prefix"]
        self._s3().put_object(
            Bucket=self.env["source_connection"]["bucket"],
            Key=prefix + name,
            Body=Path(file_path).read_bytes(),
        )
        self.reset_migration_table(table)

    @keyword("Delete Local Source Object")
    def delete_local_source_object(self, table: str, name: str):
        self._require_local()
        prefix = self.manifest.tables[table].contract.raw["source"]["prefix"]
        self._s3().delete_object(Bucket=self.env["source_connection"]["bucket"], Key=prefix + name)
        self.reset_migration_table(table)

    @keyword("Get Local Fixture Value")
    def get_local_fixture_value(self, table: str, column: str, row: int = 0):
        """Raw synthetic value, used only to assert it never appears in output."""
        self._require_local()
        return str(self._ctx(table).expected_df[column].iloc[int(row)])

    @keyword("Stop Local Migration Fixture")
    def stop_local_migration_fixture(self):
        if self._s3_server is not None:
            self._s3_server.stop()
            self._s3_server = None
