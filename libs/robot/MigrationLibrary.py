import copy
import hashlib
import json
import os
import sys
import time
from contextlib import ExitStack, closing
from pathlib import Path

_REPO_ROOT = str(Path(__file__).resolve().parents[2])
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import yaml
from robot.api.deco import keyword

from libs.adapters.registry import create_source, create_target
from libs.adapters.staging import ordered_chunks
from libs.engine import controls as engine_controls
from libs.engine import reconcile, rules, schema
from libs.engine import relations as engine_relations
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
        self._snapshot_holder = None

    def _bind_snapshot(self):
        """Enforce the declared migration cut on the source side.

        `source.snapshot: exported` pins every source session to one
        pg_export_snapshot() (postgres only — the holder connection must stay
        open for the whole run). `source.snapshot_column` + `run.snapshot_id`
        scopes reads with a bound watermark predicate instead (any DB).
        Neither mechanism is required for emulated targets, but a real target
        refuses a run whose snapshot is only recorded, not enforced.
        """
        src = self.env["source"]
        sid = self.env.get("run", {}).get("snapshot_id")
        mode = src.get("snapshot")
        if self._snapshot_holder is not None:
            self._snapshot_holder.close()
            self._snapshot_holder = None
        if mode == "exported":
            if src.get("type") != "postgres":
                raise ValueError("source.snapshot 'exported' is postgres-only")
            import psycopg2

            kw = {
                k: src[k]
                for k in ("host", "port", "database", "user", "password", "sslmode", "sslrootcert")
                if k in src
            }
            holder = psycopg2.connect(connect_timeout=15, **kw)
            holder.set_session(readonly=True, autocommit=False, isolation_level="REPEATABLE READ")
            cur = holder.cursor()
            cur.execute("SELECT pg_export_snapshot()")
            exported = cur.fetchone()[0]
            cur.close()
            src["snapshot"] = exported
            self._snapshot_holder = holder
            self.meta["source_snapshot"] = exported
            self.meta["snapshot_enforced"] = True
        elif src.get("snapshot_column"):
            if not sid:
                raise ValueError("source.snapshot_column requires run.snapshot_id")
            src["snapshot_value"] = sid
            self.meta["source_snapshot"] = f"watermark:{src['snapshot_column']}"
            self.meta["snapshot_enforced"] = True
        elif mode:
            raise ValueError(f"unknown source.snapshot mode {mode!r}")
        else:
            self.meta["snapshot_enforced"] = False
        self.meta["snapshot_id"] = sid or ""
        if not self.env["target"].get("fake") and not self.meta["snapshot_enforced"]:
            raise ValueError(
                "non-emulated runs require an enforceable snapshot: "
                "source.snapshot: exported or source.snapshot_column + run.snapshot_id"
            )

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
            self._bind_snapshot()
            tables, codes = parser.load_workbook(workbook)
            chosen = [
                t for t in tables if t.enabled and (not mapping_id or t.mapping_id == mapping_id)
            ]
            if not chosen:
                raise ValueError("No enabled mappings match the QA selection")
            params = {"load_date": str(run["load_date"])} if run.get("load_date") else {}
            self.contracts = [
                builder.build_contract(
                    t, codes, self.env["source"], self.env["target"], workbook, params=params
                )
                for t in chosen
            ]
            self.meta.update(
                {
                    "environment": self.env["environment"],
                    "emulated_target": bool(self.env["target"].get("fake")),
                    "snapshot_id": run.get("snapshot_id", ""),
                    "load_date": str(run.get("load_date", "")),
                    "pipeline_id": str(run.get("pipeline_id", "")),
                    "hop": str(run.get("hop", "")),
                    "workbook_sha256": hashlib.sha256(Path(workbook).read_bytes()).hexdigest(),
                    "selected": [c.name for c in self.contracts],
                    "disabled": [t.mapping_id for t in tables if not t.enabled],
                    "unselected": [t.mapping_id for t in tables if t.enabled and t not in chosen],
                    "git_commit": ReconciliationLibrary._git_head(),
                    "executor": os.environ.get("USERNAME", os.environ.get("USER", "")),
                    "relation_children": [t.mapping_id for t in chosen if t.relations],
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
                    "detail": f"{type(exc).__name__}: {exc}"[:500],
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
                        col = next(
                            (c for c in contract.columns if c["name"] not in contract.keys),
                            None,
                        )
                        if col is None:
                            # Every mapped column is a key — tampering the key
                            # is an identity change (missing + extra), which is
                            # the only defect a key-only table can carry.
                            key = contract.keys[0]
                            chunk.loc[chunk.index[0], key] = (
                                -999999999
                                if contract.column(key)["type"] == "integer"
                                else str(chunk.iloc[0][key]) + "_DEFECT"
                            )
                        else:
                            value = chunk.iloc[0][col["name"]]
                            replacement = (
                                str(value) + "_DEFECT" if col["type"] == "string" else None
                            )
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
                    src_cfg = contract.raw["source"]
                    if src_cfg.get("type") == "csv":
                        # File-source catalog: every *.csv under dir must map —
                        # files without a workbook mapping surface as
                        # unmapped_source_tables.
                        files = {p.stem for p in Path(src_cfg["path"]).parent.glob("*.csv")}
                        mapped = {
                            c.raw["source"]["table"]
                            for c in self.contracts
                            if c.raw["source"].get("type") == "csv"
                        }
                        row["unmapped_source_tables"] = sorted(files - mapped)
                        if row["unmapped_source_tables"] and self.env.get("run", {}).get(
                            "fail_on_unmapped"
                        ):
                            row["status"] = "FAIL"
                            row["detail"] = (
                                "run.fail_on_unmapped: source files without workbook mappings"
                            )
                            return row
                        row["source_metadata"] = [
                            {"table": src_cfg["table"], "columns": live_columns}
                        ]
                    else:
                        from libs.adapters.catalog import inspect_schema

                        conn = source.conn if hasattr(source, "conn") else source.target.conn
                        catalog = inspect_schema(
                            conn,
                            contract.raw["source"]["schema"],
                            dialect=source.dialect,
                        )
                        mapped_tables = {
                            c.raw["source"]["table"]
                            for c in self.contracts
                            if c.raw["source"]["schema"] == contract.raw["source"]["schema"]
                        }
                        row["unmapped_source_tables"] = sorted(
                            {c["table"] for c in catalog} - mapped_tables
                        )
                        if row["unmapped_source_tables"] and self.env.get("run", {}).get(
                            "fail_on_unmapped"
                        ):
                            row["status"] = "FAIL"
                            row["detail"] = (
                                "run.fail_on_unmapped: source tables without workbook mappings"
                            )
                            return row
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

                    def _dq_pass():
                        # One source read serves both checks: contract rules
                        # are merged per chunk, and the mapped frame feeds the
                        # sorted stream for key-uniqueness detection.
                        for chunk in source.read_chunks(self._batch(contract)):
                            expected = mapped_rows(chunk, contract)
                            for failure in rules.merge_failures(
                                rules.evaluate_source_rules(chunk, contract),
                                rules.evaluate_derived_rules(expected, contract),
                            ):
                                failures[failure.rule_id] = (
                                    failures.get(failure.rule_id, 0) + failure.failing_rows
                                )
                            yield expected

                    previous = None
                    with closing(
                        ordered_chunks(_dq_pass(), contract, self._batch(contract))
                    ) as ordered:
                        for chunk in ordered:
                            for values in chunk.itertuples(index=False, name=None):
                                key = reconcile.canonical_key(
                                    dict(zip(chunk.columns, values, strict=True)), contract
                                )
                                if key == previous:
                                    raise ValueError("Duplicate composite reconciliation keys")
                                previous = key
                    row["dq_failures"] = failures
                    if failures:
                        row["status"] = "FAIL"
                    return row
                with closing(self._target(contract)) as target:
                    if stage == "schema":
                        from libs.adapters.catalog import inspect_schema

                        catalog = inspect_schema(
                            target.conn, contract.schema, dialect=target.dialect
                        )
                        row["target_metadata"] = [
                            c for c in catalog if c["table"] == contract.table
                        ]
                        mapped_tables = {
                            c.table for c in self.contracts if c.schema == contract.schema
                        }
                        row["unmapped_target_tables"] = sorted(
                            {c["table"] for c in catalog} - mapped_tables
                        )
                        if row["unmapped_target_tables"] and self.env.get("run", {}).get(
                            "fail_on_unmapped"
                        ):
                            row["status"] = "FAIL"
                            row["detail"] = (
                                "run.fail_on_unmapped: target tables without workbook mappings"
                            )
                            return row
                        asof = self.env["target"].get("asof_column")
                        if asof:
                            qn = (
                                f"{schema.ident(contract.schema, target.dialect)}."
                                f"{schema.ident(contract.table, target.dialect)}"
                            )
                            cur = target.conn.cursor()
                            try:
                                cur.execute(
                                    f"SELECT MAX({schema.ident(asof, target.dialect)}) FROM {qn}"
                                )
                                row["target_asof"] = str(cur.fetchone()[0])
                            finally:
                                cur.close()
                        row["schema_errors"] = schema.validate_target_schema(target, contract)
                        by_name = {c["column"]: c for c in row["target_metadata"]}
                        for spec in contract.columns:
                            observed = by_name.get(spec["name"], {})
                            if "max_length" in spec:
                                if (
                                    self.env["target"].get("fake")
                                    and observed.get("max_length") is None
                                ):
                                    row.setdefault("not_validated", []).append(
                                        f"{spec['name']}: emulator does not expose declared VARCHAR length"
                                    )
                                elif observed.get("max_length") != spec["max_length"]:
                                    row["schema_errors"].append(
                                        f"{spec['name']}: length differs from workbook"
                                    )
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
                        key_only = not any(c["name"] not in contract.keys for c in contract.columns)
                        tampered = (
                            tamper.count("missing_in_target") + tamper.count("extra_in_target") == 2
                            if key_only
                            else tamper.count("diffs") == 1
                        )
                        if (
                            missing.count("missing_in_target") != 1
                            or not tampered
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
                    if stage == "controls":
                        specs = engine_controls.validate_controls(contract)
                        if not specs:
                            row["detail"] = "no controls declared"
                            return row
                        batch = self._batch(contract)
                        expected = engine_controls.compute_controls(
                            self._mapped(source.read_chunks(batch), contract), contract
                        )
                        need = sorted(
                            {s["column"] for s in specs if s.get("column")}
                            | {g for s in specs for g in (s.get("group_by") or [])}
                        )
                        actual = engine_controls.compute_controls(
                            target.read_chunks(contract.table, columns=need, size=batch),
                            contract,
                        )
                        mismatches = engine_controls.compare_controls(expected, actual)
                        cf = contract.raw["source"].get("control_file")
                        if cf:
                            path = Path(cf)
                            if not path.is_absolute() and not path.is_file():
                                path = Path(contract.path).parent / cf
                            declared = engine_controls.load_control_file(path)
                            mismatches += engine_controls.validate_control_file(
                                expected, declared, contract
                            )
                        row["control_mismatches"] = mismatches
                        if mismatches:
                            row["status"] = "FAIL"
                        else:
                            row["detail"] = f"{len(specs)} controls certified"
                        return row
                    check = contract
                    if stage == "transform":
                        # Attribute drift to the transform layer: compare keys
                        # plus transform-derived columns only — recon already
                        # covers the full column set.
                        if not any("transform" in c for c in contract.columns):
                            row["detail"] = "no transform columns declared"
                            return row
                        check = copy.deepcopy(contract)
                        check.raw["columns"] = [
                            c
                            for c in contract.columns
                            if "transform" in c or c["name"] in contract.keys
                        ]
                    result = self._compare(source, target, check)
                    pii = {c["name"]: bool(c.get("pii")) for c in contract.columns}
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
                                    "expected": (
                                        "[REDACTED]" if pii.get(d.column, True) else str(d.expected)
                                    ),
                                    "actual": (
                                        "[REDACTED]" if pii.get(d.column, True) else str(d.actual)
                                    ),
                                }
                                for d in result.diffs
                            ],
                        }
                    )
                    arrivals = source.post_snapshot_count()
                    if arrivals is not None:
                        row["post_snapshot_arrivals"] = arrivals
                        if arrivals and not self.env.get("run", {}).get(
                            "allow_post_snapshot", False
                        ):
                            row["status"] = "FAIL"
                            row["detail"] = (
                                "post-snapshot arrivals require a delta migration "
                                "or run.allow_post_snapshot approval"
                            )
                    if result.mismatch_count:
                        row["status"] = "FAIL"
                    elif not row["detail"]:
                        row["detail"] = (
                            f"{result.target_count}/{result.source_count} rows match; "
                            f"{row['missing']} missing / {row['extra']} extra / "
                            f"{row['diffs']} column diffs"
                        )
        except Exception as exc:
            row["status"] = "ERROR"
            # Bounded message — enough to diagnose (which module, which
            # connection, which column) without dumping rows or credentials.
            row["detail"] = (
                f"{type(exc).__name__}: {exc}"[:500]
                or f"{type(exc).__name__}: validation could not complete"
            )
        finally:
            row["seconds"] = round(time.perf_counter() - started, 3)
            self.evidence.append(row)
        return row

    def _relations(self):
        """Cross-mapping FK orphan checks — one evidence row per declared
        relation, on both the source (post-transform) and target sides."""
        by_name = {c.name: c for c in self.contracts}
        rows = []
        for child in self.contracts:
            for rel in child.raw.get("relations", []):
                row = {
                    "mapping": child.name,
                    "stage": "relations",
                    "status": "PASS",
                    "detail": "",
                    "relation": (
                        f"{rel['parent_mapping']}.{rel['parent_column']}"
                        f" <- {child.name}.{rel['child_column']}"
                    ),
                }
                started = time.perf_counter()
                try:
                    parent = by_name.get(rel["parent_mapping"])
                    if parent is None:
                        raise ValueError(
                            f"relation parent {rel['parent_mapping']!r} "
                            "is not a selected mapping"
                        )
                    for side, contract, col in (
                        ("parent", parent, rel["parent_column"]),
                        ("child", child, rel["child_column"]),
                    ):
                        if col not in {c["name"] for c in contract.columns}:
                            raise ValueError(
                                f"relation {side} column {col!r} is not mapped in {contract.name}"
                            )
                    batch = self._batch(child)
                    with closing(self._source(parent)) as psrc:
                        pkeys_src = engine_relations.key_set(
                            self._mapped(psrc.read_chunks(batch), parent),
                            rel["parent_column"],
                        )
                    with closing(self._source(child)) as csrc:
                        src = engine_relations.find_orphans(
                            self._mapped(csrc.read_chunks(batch), child),
                            rel["child_column"],
                            pkeys_src,
                        )
                    with closing(self._target(parent)) as ptgt:
                        pkeys_tgt = engine_relations.key_set(
                            ptgt.read_chunks(
                                parent.table, columns=[rel["parent_column"]], size=batch
                            ),
                            rel["parent_column"],
                        )
                    with closing(self._target(child)) as ctgt:
                        tgt = engine_relations.find_orphans(
                            ctgt.read_chunks(
                                child.table, columns=[rel["child_column"]], size=batch
                            ),
                            rel["child_column"],
                            pkeys_tgt,
                        )
                    row.update(
                        {
                            "source_orphans": src["orphans"],
                            "source_null_fk": src["null_fk"],
                            "target_orphans": tgt["orphans"],
                            "target_null_fk": tgt["null_fk"],
                        }
                    )
                    if src["orphans"] or tgt["orphans"]:
                        row["status"] = "FAIL"
                        row["detail"] = (
                            f"{src['orphans']} source / {tgt['orphans']} target orphan rows"
                        )
                        row["dq_failures"] = {
                            "orphan_samples": (tgt["samples"] or src["samples"])[:5]
                        }
                    else:
                        row["detail"] = "no orphans on either side"
                except Exception as exc:
                    row["status"] = "ERROR"
                    row["detail"] = f"{type(exc).__name__}: {exc}"[:500]
                finally:
                    row["seconds"] = round(time.perf_counter() - started, 3)
                    self.evidence.append(row)
                    rows.append(row)
        return rows

    @keyword("Run Migration Stage")
    def run_stage(self, stage):
        if stage not in (
            "mapping",
            "source",
            "dq",
            "schema",
            "recon",
            "controls",
            "transform",
            "relations",
            "negative",
            "e2e",
        ):
            raise ValueError("Unknown migration stage")
        stages = (
            ("source", "dq", "schema", "recon", "controls", "transform")
            if stage == "e2e"
            else (stage,)
        )
        failed = []
        for contract in self.contracts:
            for step in stages:
                if step == "relations":
                    continue  # cross-mapping — handled after the per-contract loop
                row = self._one(step, contract)
                if row["status"] != "PASS":
                    failed.append(f"{contract.name}/{step}: {row['status']}")
        if stage in ("relations", "e2e"):
            for row in self._relations():
                if row["status"] != "PASS":
                    failed.append(f"{row['mapping']}/relations: {row['status']}")
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
        if not data["results"] or data["verdict"] != "PASS" or data["coverage"] != "COMPLETE":
            raise AssertionError("Evidence contains failed, incomplete or absent checks")
