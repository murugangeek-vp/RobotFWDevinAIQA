import hashlib
import json
import os
import time
from contextlib import ExitStack, closing
from pathlib import Path

import yaml
from robot.api.deco import keyword

from libs.adapters.registry import create_source, create_target
from libs.adapters.staging import ordered_chunks
from libs.engine import reconcile, rules, schema
from libs.engine.mapping_transforms import mapped_rows
from libs.mapping import builder, parser
from libs.reporting.report import write_evidence
from libs.robot.ReconciliationLibrary import ReconciliationLibrary, _expand_env, load_credentials


class MigrationLibrary:
    ROBOT_LIBRARY_SCOPE = "GLOBAL"
    ROBOT_AUTO_KEYWORDS = False

    def __init__(self):
        self.evidence = []
        self.contracts = []
        self.env = {}
        self.meta = {}
        self.signature = None

    @keyword("Configure Migration")
    def configure(self, env_file, workbook, mapping_id=""):
        signature = (str(env_file), str(workbook), str(mapping_id))
        if signature == self.signature:
            return
        self.evidence = []
        self.contracts = []
        self.signature = None
        self.meta = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "selected": []}
        root = Path(__file__).resolve().parents[2]
        load_credentials(root / ".env")
        try:
            self.env = _expand_env(yaml.safe_load(Path(env_file).read_text(encoding="utf-8")))
            run = self.env.get("run", {})
            if not self.env["target"].get("fake") and not run.get("snapshot_id"):
                raise ValueError(
                    "Real database runs require run.snapshot_id for an agreed frozen migration cut"
                )
            batch = int(run.get("batch_size", 5000))
            limit = int(run.get("sample_limit", 20))
            if batch < 1 or not 0 <= limit <= 1000:
                raise ValueError("Invalid batch_size/sample_limit")
            tables, codes = parser.load_workbook(workbook)
            chosen = [
                t for t in tables if t.enabled and (not mapping_id or t.mapping_id == mapping_id)
            ]
            if not chosen:
                raise ValueError("No enabled mappings match the QA selection")
            self.contracts = [
                builder.build_contract(t, codes, self.env["source"], self.env["target"], workbook)
                for t in chosen
            ]
            self.meta.update(
                {
                    "environment": self.env["environment"],
                    "emulated_target": bool(self.env["target"].get("fake")),
                    "snapshot_id": run.get("snapshot_id", ""),
                    "workbook_sha256": hashlib.sha256(Path(workbook).read_bytes()).hexdigest(),
                    "selected": [c.name for c in self.contracts],
                    "disabled": [t.mapping_id for t in tables if not t.enabled],
                    "unselected": [t.mapping_id for t in tables if t.enabled and t not in chosen],
                    "git_commit": ReconciliationLibrary._git_head(),
                    "executor": os.environ.get("USERNAME", os.environ.get("USER", "")),
                    "read_only": True,
                    "comparison": "canonical external sort; full comparison or client-side SHA-256",
                    "evidence_values": "redacted; exact totals with bounded samples",
                }
            )
            self.signature = signature
        except Exception as exc:
            self.evidence.append(
                {
                    "mapping": "configuration",
                    "stage": "setup",
                    "status": "ERROR",
                    "detail": type(exc).__name__,
                }
            )
            raise

    def _batch(self, contract):
        return int(
            contract.raw.get("recon", {}).get(
                "batch_size", self.env.get("run", {}).get("batch_size", 5000)
            )
        )

    def _source(self, contract):
        return create_source(contract.raw["source"])

    def _target(self, contract):
        cfg = {**self.env["target"], "schema": contract.schema}
        if cfg.get("fake"):
            return create_target(cfg, user="", password="")
        if not cfg.get("user") or not cfg.get("password"):
            raise ValueError(
                "Target requires explicit read-only user/password environment references"
            )
        return create_target(cfg, user=cfg["user"], password=cfg["password"])

    def _compare(self, source, target, contract, defect=None):
        batch = self._batch(contract)
        limit = int(self.env.get("run", {}).get("sample_limit", 20))
        expected = self._mapped(source.read_chunks(batch), contract)
        actual = target.read_chunks(
            contract.table,
            columns=[c["name"] for c in contract.columns],
            keys=contract.keys,
            size=batch,
        )
        if defect:
            actual = self._defective(actual, contract, defect)
        with ExitStack() as stack:
            expected_sorted = stack.enter_context(
                closing(ordered_chunks(expected, contract, batch))
            )
            actual_sorted = stack.enter_context(closing(ordered_chunks(actual, contract, batch)))
            mode = contract.raw.get("recon", {}).get("mode", "full")
            fn = reconcile.hash_compare if mode == "hash" else reconcile.compare_chunked
            return fn(expected_sorted, actual_sorted, contract, sample_limit=limit)

    @staticmethod
    def _mapped(chunks, contract):
        try:
            for chunk in chunks:
                yield mapped_rows(chunk, contract)
        finally:
            if hasattr(chunks, "close"):
                chunks.close()

    @staticmethod
    def _defective(chunks, contract, kind):
        changed = False
        try:
            for chunk in chunks:
                if not changed and not chunk.empty:
                    chunk = chunk.copy()
                    if kind == "missing":
                        chunk = chunk.iloc[1:]
                    elif kind in ("duplicate", "extra"):
                        import pandas as pd

                        added = chunk.iloc[:1].copy()
                        if kind == "extra":
                            key = contract.keys[0]
                            added.loc[added.index[0], key] = (
                                -999999999
                                if contract.column(key)["type"] == "integer"
                                else "__RECON_SYNTHETIC_EXTRA__"
                            )
                        chunk = pd.concat([chunk, added], ignore_index=True)
                    else:
                        col = next(c for c in contract.columns if c["name"] not in contract.keys)
                        value = chunk.iloc[0][col["name"]]
                        replacement = str(value) + "_DEFECT" if col["type"] == "string" else None
                        chunk.loc[chunk.index[0], col["name"]] = replacement
                    changed = True
                yield chunk
            if not changed:
                raise ValueError("Negative self-test requires nonempty fixtures")
        finally:
            if hasattr(chunks, "close"):
                chunks.close()

    def _one(self, stage, contract):
        row = {
            "mapping": contract.name,
            "stage": stage,
            "status": "PASS",
            "detail": "",
            "source": f"{contract.raw['source']['schema']}.{contract.raw['source']['table']}",
            "target": f"{contract.schema}.{contract.table}",
        }
        started = time.perf_counter()
        try:
            with closing(self._source(contract)) as source:
                live_columns = source.schema()
                missing = set(contract.source_columns) - set(live_columns)
                if missing:
                    raise ValueError("Mapped columns missing from source metadata")
                row["source_columns"] = live_columns
                if stage in ("mapping", "source"):
                    from libs.adapters.catalog import inspect_schema

                    conn = source.conn if hasattr(source, "conn") else source.target.conn
                    catalog = inspect_schema(conn, contract.raw["source"]["schema"])
                    mapped_tables = {
                        c.raw["source"]["table"]
                        for c in self.contracts
                        if c.raw["source"]["schema"] == contract.raw["source"]["schema"]
                    }
                    row["unmapped_source_tables"] = sorted(
                        {c["table"] for c in catalog} - mapped_tables
                    )
                    row["source_metadata"] = [
                        c for c in catalog if c["table"] == contract.raw["source"]["table"]
                    ]
                row["source_count"] = source.row_count()
                if not row["source_count"] and not self.env.get("run", {}).get(
                    "allow_empty", False
                ):
                    raise ValueError("Empty source requires explicit run.allow_empty approval")
                if stage in ("mapping", "source"):
                    row["field_mappings"] = [
                        {
                            k: v
                            for k, v in c.items()
                            if k in ("name", "type", "source_name", "transform", "nullable")
                        }
                        for c in contract.columns
                    ]
                    return row
                if stage == "dq":
                    failures = {}
                    for chunk in source.read_chunks(self._batch(contract)):
                        expected = mapped_rows(chunk, contract)
                        found = rules.merge_failures(
                            rules.evaluate_source_rules(chunk, contract),
                            rules.evaluate_derived_rules(expected, contract),
                        )
                        for failure in found:
                            failures[failure.rule_id] = (
                                failures.get(failure.rule_id, 0) + failure.failing_rows
                            )
                    row["dq_failures"] = failures
                    expected_chunks = (
                        mapped_rows(chunk, contract)
                        for chunk in source.read_chunks(self._batch(contract))
                    )
                    previous = None
                    with closing(
                        ordered_chunks(expected_chunks, contract, self._batch(contract))
                    ) as ordered:
                        for chunk in ordered:
                            for values in chunk.itertuples(index=False, name=None):
                                key = reconcile.canonical_key(
                                    dict(zip(chunk.columns, values, strict=True)), contract
                                )
                                if key == previous:
                                    raise ValueError("Duplicate composite reconciliation keys")
                                previous = key
                    if failures:
                        row["status"] = "FAIL"
                    return row
                with closing(self._target(contract)) as target:
                    if stage == "schema":
                        from libs.adapters.catalog import inspect_schema

                        catalog = inspect_schema(target.conn, contract.schema)
                        row["target_metadata"] = [
                            c for c in catalog if c["table"] == contract.table
                        ]
                        mapped_tables = {
                            c.table for c in self.contracts if c.schema == contract.schema
                        }
                        row["unmapped_target_tables"] = sorted(
                            {c["table"] for c in catalog} - mapped_tables
                        )
                        row["schema_errors"] = schema.validate_target_schema(target, contract)
                        by_name = {c["column"]: c for c in row["target_metadata"]}
                        for spec in contract.columns:
                            observed = by_name.get(spec["name"], {})
                            if "max_length" in spec:
                                if self.env["target"].get("fake") and observed.get("max_length") is None:
                                    row.setdefault("not_validated", []).append(f"{spec['name']}: emulator does not expose declared VARCHAR length")
                                elif observed.get("max_length") != spec["max_length"]:
                                    row["schema_errors"].append(f"{spec['name']}: length differs from workbook")
                            if "scale" in spec and observed.get("scale") != spec["scale"]:
                                row["schema_errors"].append(
                                    f"{spec['name']}: numeric scale differs from workbook"
                                )
                        if row["schema_errors"]:
                            row["status"] = "FAIL"
                        return row
                    if stage == "negative":
                        clean = self._compare(source, target, contract)
                        if clean.mismatch_count:
                            raise ValueError("Negative self-test requires a clean baseline")
                        missing = self._compare(source, target, contract, "missing")
                        tamper = self._compare(source, target, contract, "tamper")
                        extra = self._compare(source, target, contract, "extra")
                        if (
                            missing.count("missing_in_target") != 1
                            or tamper.count("diffs") != 1
                            or extra.count("extra_in_target") != 1
                        ):
                            raise ValueError("Defect counts differ from the injected defects")
                        try:
                            self._compare(source, target, contract, "duplicate")
                        except ValueError as exc:
                            if "duplicate" not in str(exc):
                                raise
                        else:
                            raise ValueError("Duplicate defect was not detected")
                        row["detail"] = (
                            "Exact missing/extra/tamper/duplicate detection on streamed copies; databases untouched"
                        )
                        return row
                    result = self._compare(source, target, contract)
                    row.update(
                        {
                            "source_count": result.source_count,
                            "target_count": result.target_count,
                            "missing": result.count("missing_in_target"),
                            "extra": result.count("extra_in_target"),
                            "diffs": result.count("diffs"),
                            "mismatches": result.mismatch_count,
                            "diff_samples": [
                                {
                                    "column": d.column,
                                    "expected": "[REDACTED]",
                                    "actual": "[REDACTED]",
                                }
                                for d in result.diffs
                            ],
                        }
                    )
                    if result.mismatch_count:
                        row["status"] = "FAIL"
        except Exception as exc:
            row["status"] = "ERROR"
            row["detail"] = (
                f"{type(exc).__name__}: validation could not complete; check mapped schema, types, keys and connectivity"
            )
        finally:
            row["seconds"] = round(time.perf_counter() - started, 3)
            self.evidence.append(row)
        return row

    @keyword("Run Migration Stage")
    def run_stage(self, stage):
        if stage not in (
            "mapping",
            "source",
            "dq",
            "schema",
            "recon",
            "transform",
            "negative",
            "e2e",
        ):
            raise ValueError("Unknown migration stage")
        stages = ("source", "dq", "schema", "recon", "transform") if stage == "e2e" else (stage,)
        failed = []
        for contract in self.contracts:
            for step in stages:
                row = self._one(step, contract)
                if row["status"] != "PASS":
                    failed.append(f"{contract.name}/{step}: {row['status']}")
        if not self.contracts:
            raise AssertionError("No mappings configured")
        if failed:
            raise AssertionError("; ".join(failed))

    @keyword("Write Migration Evidence")
    def write_report(self, out_dir):
        return write_evidence(self.evidence, self.meta, out_dir)

    @keyword("Validate Migration Evidence")
    def validate_report(self, out_dir):
        self.write_report(out_dir)
        data = json.loads((Path(out_dir) / "run_summary.json").read_text(encoding="utf-8"))
        if not data["results"] or data["verdict"] != "PASS":
            raise AssertionError("Evidence contains failed, incomplete or absent checks")
