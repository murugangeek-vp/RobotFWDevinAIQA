"""Populate the EMULATED migration target from the mapping workbook.

Dev/demo helper only: tests/migration is verify-only, so the fakesnow
"Santander" target must be loaded out-of-band before a run. For each
enabled workbook mapping this reads the source through the real adapter,
applies the workbook transformations, and bulk-loads the expected rows.

Refuses non-fake targets — a real target is loaded by the bank's ETL,
never by QA tooling.

Usage:
    .venv/Scripts/python scripts/load_migration_target.py [--mapping account_migration]
"""

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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default="config/environments/webster_santander.yaml")
    ap.add_argument("--workbook", default="data/mapping/bank_mapping.xlsx")
    ap.add_argument("--mapping", default="", help="one Mapping ID (default: all enabled)")
    args = ap.parse_args()

    load_credentials(Path(".env"))
    env = _expand_env(yaml.safe_load(Path(args.env).read_text(encoding="utf-8")))
    if not env["target"].get("fake"):
        sys.exit(
            "refusing to load a non-emulated target — real migration targets "
            "are loaded by the bank's ETL, not by QA tooling"
        )

    tables, codes = parser.load_workbook(args.workbook)
    chosen = [t for t in tables if t.enabled and (not args.mapping or t.mapping_id == args.mapping)]
    if not chosen:
        sys.exit("no enabled mappings match the selection")

    for t in chosen:
        contract = builder.build_contract(
            t, codes, env["source"], env["target"], str(args.workbook)
        )
        source = create_source(contract.raw["source"])
        try:
            frames = [mapped_rows(chunk, contract) for chunk in source.read_chunks()]
        finally:
            source.close()
        expected = (
            pd.concat(frames, ignore_index=True)
            if frames
            else pd.DataFrame(columns=[c["name"] for c in contract.columns])
        )
        conn = create_writer({**env["target"], "schema": contract.schema}, user="", password="")
        try:
            n = load_expected_rows(conn, contract, expected, dialect="snowflake")
        finally:
            conn.close()
        print(f"{contract.name}: {n} rows -> {contract.schema}.{contract.table}")


if __name__ == "__main__":
    main()
