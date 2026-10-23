"""Phase-2 hop loader — simulates the bank ETL for the synthetic demo.

For each enabled workbook mapping: read the hop source through the real
adapter (csv dir or postgres, read-only creds), apply the workbook
transformations via mapped_rows(), and load the expected rows into the
hop target schema via the postgres writer (recon_rw).

Safeguards:
  - Refuses unless target.allow_qa_load: true or target.fake: true —
    a real/UAT target is loaded by the bank's ETL, never by QA tooling.
  - Source reads always use the env's read-only credential; target writes
    use target.load_user/load_password (${RECON_RW_*}).
  - `snapshot: exported` is verifier-only and stripped before opening the
    source — the loader reads live data, verification reads the frozen cut.

Usage:
    .venv/Scripts/python.exe scripts/load_phase1_hop.py --hop 1
    .venv/Scripts/python.exe scripts/load_phase1_hop.py \
        --env config/environments/phase1_hop1_csv_stage.yaml \
        --workbook data/mapping/phase1_hop1_csv_to_stage.xlsx
"""

HOPS = {
    1: ("phase1_hop1_csv_stage.yaml", "phase1_hop1_csv_to_stage.xlsx"),
    2: ("phase1_hop2_stage_il.yaml", "phase1_hop2_stage_to_il.xlsx"),
    3: ("phase1_hop3_il_consumption.yaml", "phase1_hop3_il_to_consumption.xlsx"),
}

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
import yaml

from libs.adapters.registry import create_source, create_writer
from libs.engine.mapping_transforms import mapped_rows
from libs.loader import load_expected_rows
from libs.mapping import builder, parser
from libs.robot.ReconciliationLibrary import _expand_env, load_credentials

_DIALECTS = {"postgres": "postgres", "snowflake": "snowflake"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hop", type=int, choices=sorted(HOPS), help="phase-1 hop shortcut")
    ap.add_argument("--env")
    ap.add_argument("--workbook")
    ap.add_argument("--mapping", default="", help="one Mapping ID (default: all enabled)")
    args = ap.parse_args()
    if args.hop:
        env_name, wb_name = HOPS[args.hop]
        args.env = args.env or f"config/environments/{env_name}"
        args.workbook = args.workbook or f"data/mapping/{wb_name}"
    if not args.env or not args.workbook:
        ap.error("--hop or both --env and --workbook are required")

    load_credentials(Path(".env"))
    env = _expand_env(yaml.safe_load(Path(args.env).read_text(encoding="utf-8")))

    target = env["target"]
    if not (target.get("fake") or target.get("allow_qa_load")):
        sys.exit(
            "refusing to load a target without allow_qa_load: true — real "
            "targets are populated by the bank's ETL, not by QA tooling"
        )
    dialect = _DIALECTS.get(target["type"])
    if dialect is None:
        sys.exit(f"unsupported loader target type {target['type']!r}")

    run = env.get("run", {})
    params = {"load_date": str(run["load_date"])} if run.get("load_date") else {}

    tables, codes = parser.load_workbook(args.workbook)
    chosen = [t for t in tables if t.enabled and (not args.mapping or t.mapping_id == args.mapping)]
    if not chosen:
        sys.exit("no enabled mappings match the selection")

    for t in chosen:
        contract = builder.build_contract(
            t, codes, env["source"], target, str(args.workbook), params=params
        )
        src_cfg = {k: v for k, v in contract.raw["source"].items() if k != "snapshot"}
        source = create_source(src_cfg)
        try:
            frames = [mapped_rows(chunk, contract) for chunk in source.read_chunks()]
        finally:
            source.close()
        expected = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=[c["name"] for c in contract.columns])
        )
        writer_cfg = {**target, "schema": contract.schema}
        conn = create_writer(
            writer_cfg,
            user=target.get("load_user", target.get("user", "")),
            password=target.get("load_password", target.get("password", "")),
        )
        try:
            n = load_expected_rows(conn, contract, expected, dialect=dialect)
        finally:
            conn.close()
        print(f"{contract.name}: {n} rows -> {contract.schema}.{contract.table}")


if __name__ == "__main__":
    main()
