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
import socket
import subprocess
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml
from robot.api import logger
from robot.api.deco import keyword

from libs.adapters import conformance
from libs.adapters.base import TargetAdapter
from libs.adapters.registry import create_source, create_target, create_writer
from libs.engine import reconcile, rules, schema
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
    """(user, password) for RECON_<role>_*; Snowflake key-pair needs only the user."""
    user = os.environ.get(f"RECON_{role}_USER")
    pw = os.environ.get(f"RECON_{role}_PASSWORD")
    if target.get("type") == "snowflake" and target.get("authenticator") == "SNOWFLAKE_JWT":
        if not user:
            raise ValueError(f"Set RECON_{role}_USER for Snowflake key-pair authentication")
        return user, ""
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
        self._api_proc = None  # test-support stub server process
        self._s3_server = None  # test-support moto server

    # ----- setup -----------------------------------------------------------

    @keyword("Load Environment")
    def load_environment(self, env_file: str):
        """Load configuration and optional local database credentials."""
        load_credentials(Path(_REPO_ROOT) / ".env")
        self.env = _expand_env(yaml.safe_load(Path(env_file).read_text(encoding="utf-8")))
        self.env_name = self.env["environment"]
        logger.info(f"environment: {self.env_name}")
        return self.env_name

    @keyword("Load Contract")
    def load_contract(self, contract_path: "str | None" = None):
        path = contract_path or self.env["source"]["contract"]
        self.contract = schema.load_contract(path)
        logger.info(f"contract: {self.contract.name} v{self.contract.version}")
        return self.contract.name

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
        adapter = create_source(cfg)
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

    def _target_kwargs(self):
        t = self.env["target"]
        return dict(
            host=t["host"], port=t["port"], database=t["database"], schema=t.get("schema", "public")
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
        if self.env["target"]["type"] == "snowflake":
            raise PermissionError("Snowflake loading is disabled; PROD-05 is verification-only")
        user, pw = self._creds("RW")
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
        target = self.env["target"]
        if target["type"] == "snowflake":
            if self.contract.raw["target"]["type"] != "snowflake":
                raise ValueError("Snowflake environment requires a Snowflake contract")
            if target["schema"].upper() != self.contract.schema.upper():
                raise ValueError("Snowflake environment and contract schemas differ")
        user, pw = self._creds("RO")
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
        n = self.contract.metadata.get("expected_row_count")
        if n is None:
            raise RuntimeError("contract has no metadata.expected_row_count")
        return n

    @keyword("Execute Read Only Sql")
    def execute_read_only_sql(self, sql: str):
        """Runs SQL on the read-only connection — write attempts must fail."""
        target = self._ensure_target()
        if target.dialect == "snowflake":
            raise PermissionError(
                "Snowflake arbitrary SQL is disabled; use verification adapter methods"
            )
        cur = target.conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def _verify_only(self) -> bool:
        v = os.environ.get("RECON_SKIP_LOAD", "").strip().lower()
        return v in ("1", "true", "yes")

    @keyword("Execute Write Sql")
    def execute_write_sql(self, sql: str):
        """Write path for negative-path seeding ONLY — recon_rw in loader mode.
        In verify-only mode (RECON_SKIP_LOAD) it runs as recon_ro instead, so
        the engine itself must reject the mutation — no write credential used."""
        if self.env["target"]["type"] == "snowflake":
            raise PermissionError("Snowflake loading is disabled; PROD-05 is verification-only")
        role = "RO" if self._verify_only() else "RW"
        user, pw = self._creds(role)
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

    @keyword("Compare Records")
    def compare_records(self, columns: "list | None" = None):
        actual = self._ensure_target().read_table(
            self.contract.table, columns=[c["name"] for c in self.contract.columns]
        )
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

    # ----- PROD-01: adapter conformance --------------------------------------

    @keyword("Check Source Adapter")
    def check_source_adapter(self, source_file: "str | None" = None):
        """Conformance-check the source adapter resolved from the contract."""
        cfg = dict(self.contract.raw["source"])
        if source_file:
            cfg["path"] = source_file
        return conformance.check_source(create_source(cfg))

    @keyword("Check Target Adapter")
    def check_target_adapter(self):
        """Conformance-check the read-only target adapter from env config."""
        return conformance.check_target(self._ensure_target(), self.contract.table)

    # ----- test support ------------------------------------------------------

    @keyword("Check Source Config")
    def check_source_config(self, **cfg):
        """Conformance-check a source built from arbitrary config — e.g. a stub
        endpoint. Robot args arrive as strings; adapters coerce. Returns [] or
        a list of violation strings (init failures included)."""
        try:
            adapter = create_source(dict(cfg))
        except Exception as e:
            return [f"adapter init raised {e!r}"]
        return conformance.check_source(adapter)

    @keyword("Start Api Stub")
    def start_api_stub(self, port: int = 8080, timeout: float = 15.0):
        """Launch scripts/serve_api.py and wait for it to open `port`."""
        script = Path(_REPO_ROOT) / "scripts" / "serve_api.py"
        self._api_proc = subprocess.Popen([sys.executable, str(script), "--port", str(int(port))])
        deadline = time.time() + float(timeout)
        while time.time() < deadline:
            if self._api_proc.poll() is not None:
                raise RuntimeError("api stub exited early")
            try:
                socket.create_connection(("127.0.0.1", int(port)), timeout=1).close()
                return
            except OSError:
                time.sleep(0.3)
        raise RuntimeError(f"api stub did not open port {port} within {timeout}s")

    @keyword("Stop Api Stub")
    def stop_api_stub(self):
        """Terminate the stub server started by `Start Api Stub`."""
        if self._api_proc:
            self._api_proc.terminate()
            try:
                self._api_proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._api_proc.kill()
            self._api_proc = None

    @keyword("Read Source Config Rows")
    def read_source_config_rows(self, **cfg):
        """Read a source built from arbitrary config; return the row count.
        Used to pin specific object versions, prefixes, or endpoints."""
        adapter = create_source(dict(cfg))
        try:
            return len(adapter.read_batch())
        finally:
            adapter.close()

    @keyword("Start S3 Stub")
    def start_s3_stub(self, port: int = 5001):
        """Launch an in-process moto S3 for PROD-02 tests; returns endpoint URL."""
        from moto.server import ThreadedMotoServer

        self._s3_server = ThreadedMotoServer(port=int(port), verbose=False)
        self._s3_server.start()
        os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
        os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
        return f"http://127.0.0.1:{int(port)}"

    @keyword("Stop S3 Stub")
    def stop_s3_stub(self):
        """Stop the moto server started by `Start S3 Stub`."""
        if self._s3_server:
            self._s3_server.stop()
            self._s3_server = None

    @keyword("Put S3 Object")
    def put_s3_object(
        self, endpoint_url: str, bucket: str, key: str, file_path: str, versioning=False
    ):
        """Upload `file_path` to `bucket`/`key` (creating the bucket); returns
        the VersionId when `versioning` is enabled on the bucket."""
        import boto3

        s3 = boto3.client("s3", endpoint_url=endpoint_url, region_name="us-east-1")
        try:
            s3.head_bucket(Bucket=bucket)
        except Exception:
            s3.create_bucket(Bucket=bucket)
        if str(versioning).lower() in ("true", "yes", "1"):
            s3.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={"Status": "Enabled"})
        resp = s3.put_object(Bucket=bucket, Key=key, Body=Path(file_path).read_bytes())
        return resp.get("VersionId")

    @keyword("Close Target")
    def close_target(self):
        if self.target:
            if not self.target.is_closed():
                self.target.close()
            self.target = None
