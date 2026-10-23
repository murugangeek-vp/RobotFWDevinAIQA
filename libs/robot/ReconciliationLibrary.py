"""Robot Framework library for contract-driven data reconciliation.

Wraps libs/engine + libs/adapters so .robot suites stay declarative:
  Load Environment -> Load Contract -> Read Source -> run checks ->
  Load Source Into Target -> post-load checks -> Write Run Summary.

Credentials come from environment variables, falling back to a git-ignored
.env file in the repo root (see .env.example):
  RECON_RO_USER / RECON_RO_PASSWORD  (verification, SELECT-only)
  RECON_RW_USER / RECON_RW_PASSWORD  (loader)
"""

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml
from robot.api import SkipExecution, logger
from robot.api.deco import keyword

from libs.adapters.base import TargetAdapter
from libs.adapters.registry import create_source, create_target, create_writer
from libs.engine import reconcile, rules, schema
from libs.excelrules import parser, report, runner
from libs.excelrules.models import FAILING_SEVERITIES
from libs.loader import load_expected_rows


def load_credentials(path: Path) -> None:
    """Load local database credentials when they are absent from the environment."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if (
            key
            not in {
                "RECON_RO_USER",
                "RECON_RO_PASSWORD",
                "RECON_RW_USER",
                "RECON_RW_PASSWORD",
                "RECON_SKIP_LOAD",
                "SNOWFLAKE_ACCOUNT",
                "SNOWFLAKE_USER",
                "SNOWFLAKE_PASSWORD",
                "SNOWFLAKE_WAREHOUSE",
                "SNOWFLAKE_DATABASE",
                "SNOWFLAKE_SCHEMA",
                "SNOWFLAKE_ROLE",
                "SOURCE_PG_HOST",
                "SOURCE_PG_PORT",
                "SOURCE_PG_DATABASE",
                "SOURCE_PG_CA_FILE",
                "MIGRATION_SNAPSHOT_ID",
            }
            or key in os.environ
        ):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        os.environ[key] = value


_ENV_REF = re.compile(r"^\$\{([A-Z0-9_]+)\}$")


_LOADER_CRED_KEYS = {"load_user", "load_password"}


def _strip_loader_credentials(node):
    """Remove loader-only credential keys before env expansion.

    Verification never writes to a target, so a missing RECON_RW_* env var
    must not fail the run — and write credentials should never even be
    loaded into the verifier's process. Only `load_phase1_hop.py` consumes
    these keys, and it expands the raw env file itself.
    """
    if isinstance(node, dict):
        return {
            k: _strip_loader_credentials(v) for k, v in node.items() if k not in _LOADER_CRED_KEYS
        }
    if isinstance(node, list):
        return [_strip_loader_credentials(v) for v in node]
    return node


def _expand_env(obj):
    """Resolve whole-value environment references; missing values fail closed."""
    if isinstance(obj, dict):
        return {k: _expand_env(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_env(v) for v in obj]
    if isinstance(obj, str):
        m = _ENV_REF.match(obj)
        if m:
            value = os.environ.get(m.group(1))
            if not value or not value.strip():
                raise ValueError(f"Required environment variable {m.group(1)} is not set")
            return value
    return obj


def resolve_credentials(role: str, target: dict):
    """(user, password) for RECON_<role>_* — RO verifies, RW loads."""
    user = os.environ.get(f"RECON_{role}_USER")
    pw = os.environ.get(f"RECON_{role}_PASSWORD")
    if not user or not pw:
        raise RuntimeError(
            f"Set RECON_{role}_USER / RECON_{role}_PASSWORD "
            "in the environment or the repo-root .env file"
        )
    return user, pw


class ReconciliationLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_DOC_FORMAT = "REST"

    def __init__(self):
        self.env = None
        self.env_name = None
        self.contract = None
        self.source_df = None
        self.expected_df = None
        self.target = None  # read-only adapter
        self.header_errors = []
        self.metadata_errors = []
        self.rule_failures = []
        self.schema_errors = []
        self.recon_result = None
        self.loaded_count = 0
        self.run_status = "UNKNOWN"
        self.excel_rules = []  # parsed workbook rules (enabled + disabled)
        self.excel_mappings = {}  # Mapping sheet code tables
        self.rules_workbook_path = None  # workbook file, for audit metadata
        self.rule_results = []  # RuleResult list from the last validation run
        self.uncovered_requirements = []  # Requirement IDs with no executed check
        self.mapping_workbook = None  # Excel mapping workbook (migration mode)
        self.table_mappings = None  # parsed TableMapping list
        self.code_mappings = {}  # Mapping sheet code tables
        self.table_mapping = None  # the selected TableMapping
        self.stage_results = []  # per-stage evidence rows for the report

    # ----- setup -----------------------------------------------------------

    @keyword("Load Environment")
    def load_environment(self, env_file: str):
        """Load configuration and optional local database credentials."""
        load_credentials(Path(_REPO_ROOT) / ".env")
        raw = yaml.safe_load(Path(env_file).read_text(encoding="utf-8"))
        self.env = _expand_env(_strip_loader_credentials(raw))
        self.env_name = self.env["environment"]
        logger.info(f"environment: {self.env_name}")
        return self.env_name

    @keyword("Load Contract")
    def load_contract(self, contract_path: "str | None" = None):
        path = contract_path or self.env["source"]["contract"]
        self.contract = schema.load_contract(path)
        logger.info(f"contract: {self.contract.name} v{self.contract.version}")
        return self.contract.name

    @keyword("Load Mapping Workbook")
    def load_mapping_workbook(self, path: str, mapping_id: "str | None" = None):
        """Parse the Excel mapping workbook (Tables + FieldMap + Mapping
        sheets), select a Mapping ID (first enabled when omitted), and compile
        it into a validated contract — the dynamic alternative to Load Contract."""
        from libs.mapping import builder, parser

        self.mapping_workbook = str(Path(path).resolve())
        self.table_mappings, self.code_mappings = parser.load_workbook(path)
        enabled = [t for t in self.table_mappings if t.enabled]
        if not enabled:
            raise ValueError(f"{path}: no enabled mappings on the Tables sheet")
        if mapping_id:
            sel = [t for t in enabled if t.mapping_id == mapping_id]
            if not sel:
                raise ValueError(
                    f"no enabled mapping {mapping_id!r} in {path}; enabled: "
                    f"{[t.mapping_id for t in enabled]}"
                )
            tm = sel[0]
        else:
            tm = enabled[0]
        self.table_mapping = tm
        self.contract = builder.build_contract(
            tm,
            self.code_mappings,
            self.env["source"],
            self.env["target"],
            self.mapping_workbook,
        )
        logger.info(
            f"mapping {tm.mapping_id!r}: {tm.source_schema}.{tm.source_table} -> "
            f"{tm.target_schema}.{tm.target_table} ({len(tm.fields)} fields)"
        )
        return tm.mapping_id

    @keyword("List Mapping Ids")
    def list_mapping_ids(self, path: "str | None" = None, enabled_only: bool = True):
        """All Mapping IDs in the workbook (enabled ones by default) — for
        coverage tests and per-table iteration."""
        from libs.mapping import parser

        if path:
            self.mapping_workbook = str(Path(path).resolve())
            self.table_mappings, self.code_mappings = parser.load_workbook(path)
        if self.table_mappings is None:
            raise RuntimeError("Load Mapping Workbook must run first (or pass a path)")
        return [t.mapping_id for t in self.table_mappings if t.enabled or not enabled_only]

    @keyword("Select Mapping")
    def select_mapping(self, mapping_id: str):
        """Recompile the contract for another Mapping ID in the loaded
        workbook — lets one suite reconcile every table."""
        if self.table_mappings is None:
            raise RuntimeError("Load Mapping Workbook must run first")
        from libs.mapping import builder

        sel = [t for t in self.table_mappings if t.mapping_id == mapping_id]
        if not sel:
            raise ValueError(f"unknown Mapping ID {mapping_id!r}")
        self.table_mapping = sel[0]
        self.contract = builder.build_contract(
            self.table_mapping,
            self.code_mappings,
            self.env["source"],
            self.env["target"],
            self.mapping_workbook or "",
        )
        return self.table_mapping.mapping_id

    @keyword("Read Source")
    def read_source(self, source_file: "str | None" = None, join_path: "str | None" = None):
        cfg = dict(self.contract.raw["source"])
        if source_file:
            cfg["path"] = source_file
        if join_path is not None:
            joins = cfg.get("joins") or []
            if not joins:
                raise ValueError("contract has no joins to override")
            joins = [dict(j) for j in joins]
            joins[0]["path"] = join_path
            cfg["joins"] = joins
        adapter = create_source(_expand_env(cfg))
        try:
            self.source_df = adapter.read_batch()
        finally:
            adapter.close()
        self.expected_df = reconcile.expected_target_rows(self.source_df, self.contract)
        logger.info(f"source rows: {len(self.source_df)}")
        return len(self.source_df)

    # ----- credential helpers ---------------------------------------------

    def _creds(self, role: str):
        return resolve_credentials(role, (self.env or {}).get("target", {}))

    def _target_auth(self, role: str):
        """(user, password) — explicit user/password in the env target block
        win (snowflake-style config); otherwise RECON_<role>_* env vars."""
        t = (self.env or {}).get("target", {})
        user = t.get("user") or os.environ.get(f"RECON_{role}_USER")
        pw = t.get("password") or os.environ.get(f"RECON_{role}_PASSWORD")
        if not user or not pw:
            raise RuntimeError(
                "Set user/password in the env target block or "
                f"RECON_{role}_USER / RECON_{role}_PASSWORD"
            )
        return user, pw

    def _target_kwargs(self):
        t = self.env["target"]
        return dict(
            host=t.get("host"),
            port=t.get("port"),
            database=t.get("database"),
            schema=t.get("schema", "public"),
        )

    # ----- Pilot-01: header / metadata ---------------------------------------

    @keyword("Get Header Errors")
    def get_header_errors(self, source_file: "str | None" = None, join_index: "str | None" = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.header_errors = reconcile.validate_csv_header(
            path, self.contract, join_index=join_index
        )
        return self.header_errors

    @keyword("Get Metadata Errors")
    def get_metadata_errors(self, source_file: "str | None" = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.metadata_errors = reconcile.validate_csv_metadata(path, self.contract)
        return self.metadata_errors

    # ----- Pilot-02: data quality ---------------------------------------------

    @keyword("Run Data Quality Rules")
    def run_data_quality_rules(self):
        source = rules.evaluate_source_rules(self.source_df, self.contract)
        derived = (
            rules.evaluate_derived_rules(self.expected_df, self.contract)
            if self.expected_df is not None
            else []
        )
        self.rule_failures = rules.merge_failures(source, derived)
        for f in self.rule_failures:
            logger.warn(f"rule failure {f.rule_id}: {f.failing_rows} rows, " f"samples {f.samples}")
        return len(self.rule_failures)

    @keyword("Get Contract Rule Types")
    def get_contract_rule_types(self):
        """Rule types the loaded contract exercises (sorted)."""
        return rules.contract_rule_types(self.contract)

    @keyword("Get Rule Failures")
    def get_rule_failures(self, rule_type: "str | None" = None):
        """All rule failures, or filtered to one rule type (e.g. not_null)."""
        if rule_type is not None and rule_type not in rules.RULE_TYPES:
            raise ValueError(
                f"unknown rule type {rule_type!r}; expected one of {sorted(rules.RULE_TYPES)}"
            )
        return [
            {
                "rule_id": f.rule_id,
                "column": f.column,
                "failing_rows": f.failing_rows,
                "samples": f.samples,
            }
            for f in self.rule_failures
            if rule_type is None or f.rule_id.split(":", 1)[0] == rule_type
        ]

    # ----- Pilot-08: loader (recon_rw) ----------------------------------------

    @keyword("Load Source Into Target")
    def load_source_into_target(self):
        # Verify-only mode (RECON_SKIP_LOAD): leave the target untouched so the
        # suite validates whatever is already there — production semantics.
        # Verification then needs only the read-only role; a missing/broken
        # RECON_RW_* credential must not fail it.
        if self._verify_only():
            logger.warn("RECON_SKIP_LOAD set - target left untouched (verify-only)")
            self.loaded_count = 0
            return 0
        user, pw = self._target_auth("RW")
        t = self._target_kwargs()
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            self.loaded_count = load_expected_rows(
                conn,
                self.contract,
                self.expected_df,
                dialect=self.env["target"].get("type", "postgres"),
            )
        finally:
            conn.close()
        logger.info(f"loaded {self.loaded_count} rows into " f"{t['schema']}.{self.contract.table}")
        return self.loaded_count

    # ----- read-only target access (recon_ro) --------------------------------

    @keyword("Connect Target Read Only")
    def connect_target_read_only(self):
        self.close_target()
        user, pw = self._target_auth("RO")
        self.target = create_target(self.env["target"], user=user, password=pw)
        return True

    def _ensure_target(self) -> TargetAdapter:
        if self.target is None or self.target.is_closed():
            self.connect_target_read_only()
        assert self.target is not None
        return self.target

    @keyword("Get Target Count")
    def get_target_count(self):
        return self._ensure_target().row_count(self.contract.table)

    @keyword("Get Expected Row Count")
    def get_expected_row_count(self):
        """Expected target count = rows the source actually delivered (after
        row_filter scoping and transforms) — never a hardcoded number."""
        if self.expected_df is None:
            raise RuntimeError("Read Source must run before the row count check")
        return len(self.expected_df)

    @keyword("Get Source Filter Stats")
    def get_source_filter_stats(self):
        """{total, included, excluded, filter} — scope accounting for
        source.row_filter contracts (migration-scope filters)."""
        if self.source_df is None:
            raise RuntimeError("Read Source must run before filter stats")
        return reconcile.row_filter_stats(self.source_df, self.contract)

    @keyword("Execute Read Only Sql")
    def execute_read_only_sql(self, sql: str):
        """Runs SQL on the read-only connection — write attempts must fail."""
        target = self._ensure_target()
        cur = target.conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def _verify_only(self) -> bool:
        if self.contract and self.contract.raw["source"].get("type") in ("postgres", "snowflake"):
            return True
        v = os.environ.get("RECON_SKIP_LOAD", "").strip().lower()
        return v in ("1", "true", "yes")

    @keyword("Require Loader Mode")
    def require_loader_mode(self):
        """Skip this test when verify-only mode (RECON_SKIP_LOAD) is active —
        the test must write to the target to seed a defect, which verify-only
        intentionally rejects."""
        if self._verify_only():
            raise SkipExecution(
                "requires loader mode: unset RECON_SKIP_LOAD to run defect-seeding tests"
            )

    @keyword("Seed Target Defect")
    def seed_target_defect(self, kind: str):
        """Seed one defect into the target so negative tests can prove
        detection (loader mode only). kind: missing | extra | tamper.
        Returns {key, column} identifying what was broken — assertions check
        the engine reports exactly that."""
        if self._verify_only():
            raise SkipExecution("requires loader mode: defect seeding needs target writes")
        keys = self.contract.keys
        if len(keys) != 1:
            raise ValueError("defect seeding supports single-key tables")
        dialect = self.env["target"].get("type", "postgres")
        qname = (
            f"{schema.ident(self.contract.schema, dialect)}."
            f"{schema.ident(self.contract.table, dialect)}"
        )
        kq = schema.ident(keys[0], dialect)
        user, pw = self._target_auth("RW")
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            cur = conn.cursor()
            cur.execute(f"SELECT MIN({kq}), MAX({kq}) FROM {qname}")
            mn, mx = cur.fetchone()
            if kind == "missing":
                cur.execute(f"DELETE FROM {qname} WHERE {kq} = (SELECT MIN({kq}) FROM {qname})")
                detail = {"kind": kind, "key": mn, "column": keys[0]}
            elif kind == "extra":
                cols = [c["name"] for c in self.contract.columns]
                key_spec = self.contract.column(keys[0])
                bump = (
                    f"(SELECT MAX({kq}) FROM {qname}) + 900000001"
                    if key_spec["type"] == "integer"
                    else f"(SELECT MAX({kq}) FROM {qname}) || '_EXTRA'"
                )
                sel = ", ".join(bump if c == keys[0] else schema.ident(c, dialect) for c in cols)
                cols_sql = ", ".join(schema.ident(c, dialect) for c in cols)
                cur.execute(
                    f"INSERT INTO {qname} ({cols_sql}) SELECT {sel} FROM {qname} "
                    f"WHERE {kq} = (SELECT MAX({kq}) FROM {qname})"
                )
                detail = {"kind": kind, "key": mx, "column": keys[0]}
            elif kind == "tamper":
                col = next((c for c in self.contract.columns if "transform" in c), None)
                if col is None:
                    raise ValueError("contract has no transformed column to tamper")
                wrong = {
                    "integer": "-999999",
                    "decimal": "-999999.99",
                    "date": "'1900-01-01'",
                    "timestamp": "'1900-01-01 00:00:00'",
                    "boolean": "false",
                }.get(col["type"], "'ZZZ_TAMPERED'")
                cur.execute(
                    f"UPDATE {qname} SET {schema.ident(col['name'], dialect)} = {wrong} "
                    f"WHERE {kq} = (SELECT MIN({kq}) FROM {qname})"
                )
                detail = {"kind": kind, "key": mn, "column": col["name"]}
            else:
                raise ValueError(f"unknown defect kind {kind!r} (missing|extra|tamper)")
            conn.commit()
            cur.close()
        finally:
            conn.close()
        return detail

    @keyword("Check Where Safety")
    def check_where_safety(self, fragment: str):
        """Vet a workbook WHERE fragment: single-statement read-only only."""
        from libs.excelrules.validators import where_is_safe

        if not where_is_safe(fragment):
            raise ValueError(f"unsafe WHERE fragment {fragment!r}")
        return True

    @keyword("Execute Write Sql")
    def execute_write_sql(self, sql: str):
        """Write path for negative-path seeding ONLY — recon_rw in loader mode.
        In verify-only mode (RECON_SKIP_LOAD) it runs as recon_ro instead, so
        the engine itself must reject the mutation — no write credential used."""
        role = "RO" if self._verify_only() else "RW"
        user, pw = self._target_auth(role)
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    # ----- Pilot-03: schema validation -----------------------------------------

    @keyword("Get Schema Errors")
    def get_schema_errors(self, category: "str | None" = None):
        """All schema errors, or just one aspect: columns|types|nullability|primary_key."""
        by_cat = schema.validate_target_schema_by_category(self._ensure_target(), self.contract)
        self.schema_errors = []
        for errs in by_cat.values():
            for e in errs:
                if e not in self.schema_errors:
                    self.schema_errors.append(e)
        if category is None:
            return self.schema_errors
        if category not in by_cat:
            raise ValueError(
                f"unknown schema aspect {category!r}; expected one of {sorted(by_cat)}"
            )
        return by_cat[category]

    # ----- Pilot-04 / Pilot-05: comparison ----------------------------------------

    def _expected_chunks(self, batch_size: int):
        """Yield expected-target frames one source chunk at a time — the
        streaming side of chunked/hash comparison for big tables."""
        cfg = _expand_env(dict(self.contract.raw["source"]))
        adapter = create_source(cfg)
        try:
            for chunk in adapter.read_chunks(batch_size):
                yield reconcile.expected_target_rows(chunk, self.contract)
        finally:
            adapter.close()

    @keyword("Compare Records")
    def compare_records(self, columns: "list | None" = None):
        """Key-based comparison. Small tables compare in memory; when the
        contract recon block sets mode=hash or batch_size is exceeded, the
        engine streams chunks — target-side memory stays bounded."""
        recon_cfg = self.contract.raw.get("recon") or {}
        mode = recon_cfg.get("mode", "full")
        batch = int(recon_cfg.get("batch_size") or 50000)
        cols = columns or [c["name"] for c in self.contract.columns]
        target = self._ensure_target()
        expected_n = 0 if self.expected_df is None else len(self.expected_df)
        if (mode == "hash" or expected_n > batch) and columns is None:
            from contextlib import closing

            from libs.adapters.staging import ordered_chunks

            expected = ordered_chunks(self._expected_chunks(batch), self.contract, batch)
            actual_chunks = target.read_chunks(
                self.contract.table, columns=cols, keys=self.contract.keys, size=batch
            )
            actual = ordered_chunks(actual_chunks, self.contract, batch)
            with closing(expected), closing(actual):
                fn = reconcile.hash_compare if mode == "hash" else reconcile.compare_chunked
                self.recon_result = fn(expected, actual, self.contract)
        else:
            actual = target.read_table(self.contract.table, columns=cols)
            self.recon_result = reconcile.compare(
                self.expected_df, actual, self.contract, columns=columns
            )
        return self.recon_result.mismatch_count

    @keyword("Get Mismatch Detail")
    def get_mismatch_detail(self):
        r = self.recon_result
        return {
            "source_count": r.source_count,
            "target_count": r.target_count,
            "missing_in_target": r.missing_in_target[:10],
            "extra_in_target": r.extra_in_target[:10],
            "diffs": [
                {
                    "key": d.key,
                    "column": d.column,
                    "expected": str(d.expected),
                    "actual": str(d.actual),
                }
                for d in r.diffs[:10]
            ],
        }

    @keyword("Get Transform Columns")
    def get_transform_columns(self):
        """Contract columns whose values are derived by a transform."""
        return [c["name"] for c in self.contract.columns if "transform" in c]

    @keyword("Get Transform Diffs")
    def get_transform_diffs(self, column: "str | None" = None):
        """Pilot-05: diffs on derived/transformed columns -> transform layer."""
        transform_cols = set(self.get_transform_columns())
        return [
            {"key": d.key, "column": d.column, "expected": str(d.expected), "actual": str(d.actual)}
            for d in self.recon_result.diffs
            if d.column in transform_cols and (column is None or d.column == column)
        ]

    # ----- Pilot-07: run summary ------------------------------------------------

    @keyword("Write Run Summary")
    def write_run_summary(self, status: str, out_dir: str = "results"):
        Path(out_dir).mkdir(parents=True, exist_ok=True)
        r = self.recon_result
        summary = {
            "contract": self.contract.name,
            "contract_version": self.contract.version,
            "environment": self.env_name,
            "target_count": r.target_count if r else (self.loaded_count or 0),
            "source_count": r.source_count
            if r
            else (0 if self.source_df is None else len(self.source_df)),
            "mismatch_count": r.mismatch_count if r else 0,
            "validation_rule_failures": sum(f.failing_rows for f in self.rule_failures),
            "schema_errors": len(self.schema_errors),
            "final_status": status,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
        path = Path(out_dir) / "run_summary.json"
        path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        logger.info(f"run summary written to {path}")
        return str(path)

    @keyword("Get Run Summary")
    def get_run_summary(self, out_dir: str = "results"):
        """Read back the run_summary.json audit artifact."""
        path = Path(out_dir) / "run_summary.json"
        return json.loads(path.read_text(encoding="utf-8"))

    # ----- migration evidence report -----------------------------------------

    @keyword("Record Stage")
    def record_stage(self, stage: str, status: str, detail: str = ""):
        """Append one stage-result row for the migration evidence report."""
        self.stage_results.append(
            {
                "stage": stage,
                "status": status,
                "detail": detail,
                "mapping": self.contract.name if self.contract else "",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            }
        )
        return len(self.stage_results)

    @keyword("Write Migration Report")
    def write_migration_report(self, out_dir: str = "results"):
        """Write the migration evidence artifacts: migration_summary.xlsx
        (Run Info / Stage Results / Diffs / Schema Errors / Insights) plus
        migration_report.md with improvement suggestions."""
        from libs.reporting import report as mig_report

        meta = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "environment": self.env_name or "",
            "contract": self.contract.name if self.contract else "",
            "contract_version": self.contract.version if self.contract else "",
            "mapping_workbook": self.mapping_workbook or "",
            "executed_by": (
                os.environ.get("GITHUB_ACTOR")
                or os.environ.get("USERNAME")
                or os.environ.get("USER")
                or ""
            ),
            "git_commit": os.environ.get("GITHUB_SHA", "") or self._git_head(),
        }
        paths = mig_report.write_migration_report(
            stage_results=self.stage_results,
            recon_result=self.recon_result,
            schema_errors=self.schema_errors,
            rule_failures=self.rule_failures,
            meta=meta,
            out_dir=out_dir,
        )
        logger.info(f"migration report written: {paths}")
        return paths

    @keyword("Close Target")
    def close_target(self):
        if self.target:
            if not self.target.is_closed():
                self.target.close()
            self.target = None

    # ----- Excel-driven rule layer (business_rules.xlsx) --------------------

    @keyword("Load Rule Workbook")
    def load_rule_workbook(self, path: str):
        """Parse business_rules.xlsx — Rules sheet rows and the Mapping sheet
        code tables. Fail-closed on unknown rule types / missing columns.
        A workbook without a Rules sheet is the merged mapping template:
        it simply declares no business rules (0 returned)."""
        from contextlib import closing

        import openpyxl

        self.excel_rules = []
        self.excel_mappings = {}
        self.rules_workbook_path = str(Path(path).resolve())
        with closing(openpyxl.load_workbook(path, read_only=True)) as wb:
            if "Rules" not in wb.sheetnames:
                logger.info(f"rule workbook: no Rules sheet in {path} — 0 rules")
                return 0
        self.excel_rules = parser.load_rules(path)
        self.excel_mappings = parser.load_mappings(path)
        self.rules_workbook_path = str(Path(path).resolve())
        enabled = sum(1 for r in self.excel_rules if r.enabled)
        logger.info(f"rule workbook: {enabled}/{len(self.excel_rules)} rules enabled")
        return len(self.excel_rules)

    @keyword("Check Rules Apply To Environment")
    def check_rules_apply(self):
        """'' when the loaded rules workbook can run against this env's
        target schema; a skip reason when NONE of its rule target tables
        exist there (workbook belongs to a different hop — e.g. stage-schema
        rules under the consumption env). Partial presence returns '' so
        genuinely broken rules still ERROR loudly, never silently pass."""
        if not self.excel_rules:
            return ""
        tables = sorted({r.target_table for r in self.excel_rules if r.enabled and r.target_table})
        if not tables:
            return ""
        schema_name = (self.env or {}).get("target", {}).get("schema", "public")
        for name in [schema_name, *tables]:
            if not re.fullmatch(r"[A-Za-z_]\w*", name):
                raise ValueError(f"unsafe identifier in rules target check: {name!r}")
        names = ", ".join(f"'{t}'" for t in tables)
        try:
            df = self._ensure_target().query(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = '{schema_name}' AND table_name IN ({names})"
            )
        except Exception:
            # Targets without information_schema (e.g. emulators) keep the
            # legacy behavior — each rule reports its own error.
            return ""
        present = set(df.iloc[:, 0]) if len(df) else set()
        missing = [t for t in tables if t not in present]
        if len(missing) == len(tables):
            return (
                f"rules target tables {tables} do not exist in target schema "
                f"{schema_name!r} — this rules workbook belongs to a different "
                "hop; run it with the environment its tables live in"
            )
        return ""

    @keyword("Run Business Rules Validation")
    def run_business_rules_validation(
        self, test_id: "str | None" = None, source_dir: "str | None" = None
    ):
        """Dispatch every enabled rule (or one `test_id`) to its rule-type
        validator. Target rows are fetched via dynamic read-only SQL; source
        tables resolve to <source_dir>/<table>.csv. Returns RuleResult list."""
        rules = [r for r in self.excel_rules if r.enabled]
        if test_id:
            rules = [r for r in rules if r.test_id == test_id]
            if not rules:
                raise ValueError(f"no enabled rule with Test ID {test_id!r}")
        src_dir = Path(source_dir) if source_dir else Path(_REPO_ROOT) / "data" / "samples"
        schema_name = (self.env or {}).get("target", {}).get("schema", "public")
        target = self._ensure_target()
        self.rule_results = runner.run_rules(
            rules, self.excel_mappings, src_dir, target.query, schema=schema_name
        )
        logger.info(
            f"business rules validation: {len(self.rule_results)} rules, "
            f"{sum(len(r.violations) for r in self.rule_results)} violations"
        )
        return self.rule_results

    @keyword("Get Rule Violations")
    def get_rule_violations(self, test_id: "str | None" = None):
        """Violation dicts {test_id, column, key, expected, actual} — filtered
        to one rule or all failing-severity rules (LOW/WARN stay report-only)."""
        out: list = []
        for r in self.rule_results:
            if test_id and r.rule.test_id != test_id:
                continue
            if not test_id and r.rule.severity.upper() not in FAILING_SEVERITIES:
                continue
            out.extend(v.as_dict() for v in r.violations)
        return out

    @keyword("Get Workbook Rule Types")
    def get_workbook_rule_types(self):
        """Sorted unique rule types present in the loaded workbook (enabled
        or not) — used by the coverage guard test."""
        return sorted({r.rule_type for r in self.excel_rules})

    @keyword("Get Rule Summary")
    def get_rule_summary(self):
        """Per-rule status rows: {test_id, rule_type, severity, status,
        violations, error}."""
        return [
            {
                "test_id": r.rule.test_id,
                "rule_type": r.rule.rule_type,
                "severity": r.rule.severity,
                "status": r.status,
                "violations": len(r.violations),
                "error": r.error,
            }
            for r in self.rule_results
        ]

    @staticmethod
    def _git_head() -> str:
        """Short commit SHA for audit traceability; empty outside a repo."""
        try:
            out = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                cwd=_REPO_ROOT,
            )
            return out.stdout.strip() if out.returncode == 0 else ""
        except Exception:  # noqa: BLE001 — audit metadata must never break a run
            return ""

    @keyword("Write Rules Summary")
    def write_rules_summary(self, out_path: "str | None" = None):
        """Write the Excel audit workbook (results/business_rules_summary.xlsx
        by default): Run Info sheet carries the audit trail — timestamp,
        environment, contract version, executor, commit — plus Summary and a
        full Violations detail sheet."""
        path = out_path or str(Path("results") / "business_rules_summary.xlsx")
        meta = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "environment": self.env_name or "",
            "contract": self.contract.name if self.contract else "",
            "contract_version": self.contract.version if self.contract else "",
            "rules_workbook": self.rules_workbook_path or "",
            "executed_by": (
                os.environ.get("GITHUB_ACTOR")
                or os.environ.get("USERNAME")
                or os.environ.get("USER")
                or ""
            ),
            "git_commit": os.environ.get("GITHUB_SHA", "") or self._git_head(),
        }
        written = report.write_summary(self.rule_results, path, meta=meta)
        logger.info(f"rules summary written to {written}")
        return str(written)

    @keyword("Write Consolidated Evidence")
    def write_consolidated_evidence(
        self, out_dir: str = "results", mapping_workbook: str = "", test_outcomes=None
    ):
        """Consolidated lifecycle report — merges the migration stage evidence
        already written to <out_dir>/run_summary.json with the business-rule
        results into end_to_end_summary.xlsx + end_to_end_report.md and a plain-English
        end_to_end_report.pdf. When mapping_workbook
        is supplied, also emits the Requirement Traceability Matrix (rtm.xlsx),
        the DC/DA/DI/DG compliance_scorecard.xlsx and, on failures,
        defect_report.md — and augments run_summary.json with rtm/compliance
        blocks."""
        from libs.reporting import consolidated_report, traceability

        summary_path = Path(out_dir) / "run_summary.json"
        run = json.loads(summary_path.read_text(encoding="utf-8")) if summary_path.is_file() else {}
        trace = None
        if mapping_workbook:
            from libs.mapping import parser as mapping_parser

            tables, _ = mapping_parser.load_workbook(mapping_workbook)
            trace = traceability.write_trace(
                tables, self.excel_rules, self.rule_results, run, out_dir
            )
            self.uncovered_requirements = trace["uncovered"]
            run["rtm"] = {
                "requirements": len({r["req"] for r in trace["rtm_rows"]}),
                "uncovered": trace["uncovered"],
            }
            run["compliance"] = trace["verdicts"]
            if summary_path.is_file():
                summary_path.write_text(json.dumps(run, indent=2, default=str), encoding="utf-8")
        meta = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "environment": self.env_name or "",
            "hop": (self.env or {}).get("run", {}).get("hop", ""),
            "rules_workbook": self.rules_workbook_path or "",
            "executed_by": (
                os.environ.get("GITHUB_ACTOR")
                or os.environ.get("USERNAME")
                or os.environ.get("USER")
                or ""
            ),
            "git_commit": os.environ.get("GITHUB_SHA", "") or self._git_head(),
        }
        written = consolidated_report.write_consolidated(
            run,
            self.rule_results,
            out_dir,
            meta=meta,
            trace=trace,
            declared_rules=self.excel_rules,
            test_outcomes=test_outcomes,
        )
        logger.info(f"e2e evidence written to {written}")
        return str(written)

    @keyword("Validate Requirements Coverage")
    def validate_requirements_coverage(self):
        """RTM gate: when env `run.require_rtm_coverage` is true, every
        Requirement ID declared in the workbook must have produced at least
        one executed check — an uncovered requirement fails the run.
        No-op without the flag or without a workbook that declares req ids."""
        if not (self.env or {}).get("run", {}).get("require_rtm_coverage"):
            return
        uncovered = getattr(self, "uncovered_requirements", None)
        if uncovered:
            raise AssertionError(f"requirements with no executed check: {', '.join(uncovered)}")
