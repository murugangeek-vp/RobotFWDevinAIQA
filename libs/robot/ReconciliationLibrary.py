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

    # ----- setup -----------------------------------------------------------

    @keyword("Load Environment")
    def load_environment(self, env_file: str):
        """Load configuration and optional local database credentials."""
        load_credentials(Path(_REPO_ROOT) / ".env")
        self.env = yaml.safe_load(Path(env_file).read_text(encoding="utf-8"))
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
    def read_source(self, source_file: "str | None" = None):
        cfg = dict(self.contract.raw["source"])
        if source_file:
            cfg["path"] = source_file
        self.source_df = create_source(cfg).read_batch()
        self.expected_df = reconcile.expected_target_rows(self.source_df, self.contract)
        logger.info(f"source rows: {len(self.source_df)}")
        return len(self.source_df)

    # ----- credential helpers ---------------------------------------------

    def _creds(self, role: str):
        user = os.environ.get(f"RECON_{role}_USER")
        pw = os.environ.get(f"RECON_{role}_PASSWORD")
        if not user or not pw:
            raise RuntimeError(
                f"Set RECON_{role}_USER / RECON_{role}_PASSWORD "
                "in the environment or the repo-root .env file"
            )
        return user, pw

    def _target_kwargs(self):
        t = self.env["target"]
        return dict(
            host=t["host"], port=t["port"], database=t["database"], schema=t.get("schema", "public")
        )

    # ----- MVP-01: header / metadata ---------------------------------------

    @keyword("Get Header Errors")
    def get_header_errors(self, source_file: "str | None" = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.header_errors = reconcile.validate_csv_header(path, self.contract)
        return self.header_errors

    @keyword("Get Metadata Errors")
    def get_metadata_errors(self, source_file: "str | None" = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.metadata_errors = reconcile.validate_csv_metadata(path, self.contract)
        return self.metadata_errors

    # ----- MVP-02: data quality ---------------------------------------------

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

    # ----- MVP-08: loader (recon_rw) ----------------------------------------

    @keyword("Load Source Into Target")
    def load_source_into_target(self):
        user, pw = self._creds("RW")
        t = self._target_kwargs()
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            self.loaded_count = load_expected_rows(conn, self.contract, self.expected_df)
        finally:
            conn.close()
        logger.info(f"loaded {self.loaded_count} rows into " f"{t['schema']}.{self.contract.table}")
        return self.loaded_count

    # ----- read-only target access (recon_ro) --------------------------------

    @keyword("Connect Target Read Only")
    def connect_target_read_only(self):
        self.close_target()
        user, pw = self._creds("RO")
        self.target = create_target(self.env["target"], user=user, password=pw)
        return True

    def _ensure_target(self) -> TargetAdapter:
        if self.target is None or self.target.conn.closed:
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
        cur = self._ensure_target().conn.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    @keyword("Execute Write Sql")
    def execute_write_sql(self, sql: str):
        """Write path for negative-path seeding ONLY — uses recon_rw, never ro."""
        user, pw = self._creds("RW")
        conn = create_writer(self.env["target"], user=user, password=pw)
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    # ----- MVP-03: schema validation -----------------------------------------

    @keyword("Get Schema Errors")
    def get_schema_errors(self, category: "str | None" = None):
        """All schema errors, or just one aspect: columns|types|nullability|primary_key."""
        by_cat = schema.validate_target_schema_by_category(
            self._ensure_target().conn, self.contract
        )
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

    # ----- MVP-04 / MVP-05: comparison ----------------------------------------

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
        """MVP-05: diffs on derived/transformed columns -> transform layer."""
        transform_cols = set(self.get_transform_columns())
        return [
            {"key": d.key, "column": d.column, "expected": str(d.expected), "actual": str(d.actual)}
            for d in self.recon_result.diffs
            if d.column in transform_cols and (column is None or d.column == column)
        ]

    # ----- MVP-07: run summary ------------------------------------------------

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

    @keyword("Close Target")
    def close_target(self):
        if self.target:
            if not self.target.conn.closed:
                self.target.close()
            self.target = None
