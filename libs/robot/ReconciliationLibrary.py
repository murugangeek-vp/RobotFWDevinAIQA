"""Robot Framework library for contract-driven data reconciliation.

Wraps libs/engine + libs/adapters so .robot suites stay declarative:
  Load Environment -> Load Contract -> Read Source -> run checks ->
  Load Source Into Target -> post-load checks -> Write Run Summary.

Credentials come ONLY from environment variables:
  RECON_RO_USER / RECON_RO_PASSWORD  (verification, SELECT-only)
  RECON_RW_USER / RECON_RW_PASSWORD  (loader)
"""

import json
import os
import sys
import time
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml
from robot.api import logger
from robot.api.deco import keyword

from libs.adapters.csv_source import CsvSource
from libs.adapters.postgres_target import PostgresTarget, connect_rw
from libs.engine import reconcile, rules, schema
from libs.loader import load_expected_rows


class ReconciliationLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_LIBRARY_DOC_FORMAT = "REST"

    def __init__(self):
        self.env = None
        self.env_name = None
        self.contract = None
        self.source_df = None
        self.expected_df = None
        self.target = None          # read-only adapter
        self.header_errors = []
        self.metadata_errors = []
        self.rule_failures = []
        self.schema_errors = []
        self.recon_result = None
        self.loaded_count = 0
        self.run_status = "UNKNOWN"

    # ----- setup -----------------------------------------------------------

    @keyword("Load Environment")
    def load_environment(self, env_file: str):
        """Load config/environments/<name>.yaml. Credentials are read from
        RECON_RO_USER / RECON_RO_PASSWORD / RECON_RW_USER / RECON_RW_PASSWORD."""
        self.env = yaml.safe_load(Path(env_file).read_text(encoding="utf-8"))
        self.env_name = self.env["environment"]
        logger.info(f"environment: {self.env_name}")
        return self.env_name

    @keyword("Load Contract")
    def load_contract(self, contract_path: str = None):
        path = contract_path or self.env["source"]["contract"]
        self.contract = schema.load_contract(path)
        logger.info(f"contract: {self.contract.name} v{self.contract.version}")
        return self.contract.name

    @keyword("Read Source")
    def read_source(self, source_file: str = None):
        src = self.contract.raw["source"]
        path = source_file or src["path"]
        adapter = CsvSource(path, encoding=src.get("encoding", "utf-8"),
                            delimiter=src.get("delimiter", ","),
                            header=src.get("header", True))
        self.source_df = adapter.read_batch()
        self.expected_df = reconcile.expected_target_rows(self.source_df, self.contract)
        logger.info(f"source rows: {len(self.source_df)}")
        return len(self.source_df)

    # ----- credential helpers ---------------------------------------------

    def _creds(self, role: str):
        user = os.environ.get(f"RECON_{role}_USER")
        pw = os.environ.get(f"RECON_{role}_PASSWORD")
        if not user or not pw:
            raise RuntimeError(
                f"missing env vars RECON_{role}_USER / RECON_{role}_PASSWORD")
        return user, pw

    def _target_kwargs(self):
        t = self.env["target"]
        return dict(host=t["host"], port=t["port"], database=t["database"],
                    schema=t.get("schema", "public"))

    # ----- MVP-01: header / metadata ---------------------------------------

    @keyword("Get Header Errors")
    def get_header_errors(self, source_file: str = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.header_errors = reconcile.validate_csv_header(path, self.contract)
        return self.header_errors

    @keyword("Get Metadata Errors")
    def get_metadata_errors(self, source_file: str = None):
        path = source_file or self.contract.raw["source"]["path"]
        self.metadata_errors = reconcile.validate_csv_metadata(path, self.contract)
        return self.metadata_errors

    # ----- MVP-02: data quality ---------------------------------------------

    @keyword("Run Data Quality Rules")
    def run_data_quality_rules(self):
        self.rule_failures = rules.evaluate_source_rules(self.source_df, self.contract)
        for f in self.rule_failures:
            logger.warn(f"rule failure {f.rule_id}: {f.failing_rows} rows, "
                        f"samples {f.samples}")
        return len(self.rule_failures)

    @keyword("Get Rule Failures")
    def get_rule_failures(self):
        return [
            {"rule_id": f.rule_id, "column": f.column,
             "failing_rows": f.failing_rows, "samples": f.samples}
            for f in self.rule_failures
        ]

    # ----- MVP-08: loader (recon_rw) ----------------------------------------

    @keyword("Load Source Into Target")
    def load_source_into_target(self):
        user, pw = self._creds("RW")
        t = self._target_kwargs()
        conn = connect_rw(user=user, password=pw, **{k: v for k, v in t.items()
                                                   if k != "schema"})
        try:
            self.loaded_count = load_expected_rows(conn, self.contract, self.expected_df)
        finally:
            conn.close()
        logger.info(f"loaded {self.loaded_count} rows into "
                    f"{t['schema']}.{self.contract.table}")
        return self.loaded_count

    # ----- read-only target access (recon_ro) --------------------------------

    @keyword("Connect Target Read Only")
    def connect_target_read_only(self):
        user, pw = self._creds("RO")
        self.target = PostgresTarget(user=user, password=pw, **self._target_kwargs())
        return True

    @keyword("Get Target Count")
    def get_target_count(self):
        return self.target.row_count(self.contract.table)

    @keyword("Execute Write Sql")
    def execute_write_sql(self, sql: str):
        """Write path for negative-path seeding ONLY — uses recon_rw, never ro."""
        user, pw = self._creds("RW")
        conn = connect_rw(user=user, password=pw,
                          **{k: v for k, v in self._target_kwargs().items()
                             if k != "schema"})
        try:
            cur = conn.cursor()
            cur.execute(sql)
            conn.commit()
            cur.close()
        finally:
            conn.close()

    # ----- MVP-03: schema validation -----------------------------------------

    @keyword("Get Schema Errors")
    def get_schema_errors(self):
        self.schema_errors = schema.validate_target_schema(self.target.conn, self.contract)
        return self.schema_errors

    # ----- MVP-04 / MVP-05: comparison ----------------------------------------

    @keyword("Compare Records")
    def compare_records(self, columns: list = None):
        actual = self.target.read_table(
            self.contract.table,
            columns=[c["name"] for c in self.contract.columns])
        self.recon_result = reconcile.compare(
            self.expected_df, actual, self.contract, columns=columns)
        return self.recon_result.mismatch_count

    @keyword("Get Mismatch Detail")
    def get_mismatch_detail(self):
        r = self.recon_result
        return {
            "source_count": r.source_count,
            "target_count": r.target_count,
            "missing_in_target": r.missing_in_target[:10],
            "extra_in_target": r.extra_in_target[:10],
            "diffs": [{"key": d.key, "column": d.column,
                       "expected": str(d.expected), "actual": str(d.actual)}
                      for d in r.diffs[:10]],
        }

    @keyword("Get Transform Diffs")
    def get_transform_diffs(self):
        """MVP-05: diffs on derived/transformed columns -> transform layer."""
        transform_cols = [c["name"] for c in self.contract.columns if "transform" in c]
        return [
            {"key": d.key, "column": d.column,
             "expected": str(d.expected), "actual": str(d.actual)}
            for d in self.recon_result.diffs if d.column in transform_cols
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
            "source_count": r.source_count if r else
                (0 if self.source_df is None else len(self.source_df)),
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

    @keyword("Close Target")
    def close_target(self):
        if self.target:
            self.target.close()
            self.target = None
