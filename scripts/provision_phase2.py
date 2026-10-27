"""Provision Phase-2 demo databases: schemas, grants, and the read-only
join views that model the spec's lookup/enrichment semantics.

Views created (idempotent, CREATE OR REPLACE):

  wdr_uat_active_stage.signature.v_dim_customer_src
      CUP00301 LEFT JOIN CUP01201 (legal entity) LEFT JOIN DIM_REFERENCE
      (tax-id-type description via the derived TAX_ID_NUMBER_TYPE_CD rule)

  wdr_uat_integration.wdr_int_core.v_party_snpsht_src
      DIM_CUSTOMER LEFT JOIN DIM_CUSTOMER_ADDRESS on involved_party_key
      LEFT JOIN DIM_REFERENCE on attribute_map_cd = naics_cd
      (CUSTOMER_CATEGORY reference override)

Run order: stage tables exist after `load_phase2_hop.py --hop 1`; run this
script (creates the stage view) -> hop-2 load -> run again for the INT view.

Synthetic/local only: connects with ${RECON_RW_*} credentials, refuses any
database name not matching the wdr_uat_* demo set.

Usage:
    .venv/Scripts/python.exe scripts/provision_phase2.py [--views all|stage|int]
"""

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from libs.robot.ReconciliationLibrary import load_credentials

DEMO_DATABASES = {"wdr_uat_active_stage", "wdr_uat_integration", "wdr_uat_consumption"}

STAGE_VIEW_SQL = """
CREATE OR REPLACE VIEW signature.v_dim_customer_src AS
SELECT c.cubk, c.cunbr, c.cupers, c.cucncd, c.cusic, c.cuna1, c.cucfnm,
       c.cuclnm, c.cussnr, c.cussty, c.cujbdt, c.cuadl1, c.cuadl2, c.cuadct,
       c.cuadst, c.cuadpc, c.load_date,
       l.culegenty,
       r.attribute_value_desc
FROM signature.cup00301 c
LEFT JOIN signature.cup01201 l ON l.cunbr = c.cunbr
LEFT JOIN signature.dim_reference r
       ON r.attribute_cd = CASE
             WHEN COALESCE(c.cussty, '') = '' THEN 'S'
             ELSE c.cussty
          END
"""

INT_VIEW_SQL = """
CREATE OR REPLACE VIEW wdr_int_core.v_party_snpsht_src AS
SELECT d.*, a.address_country_cd, r.attribute_cd
FROM wdr_int_core.dim_customer d
LEFT JOIN wdr_int_core.dim_customer_address a
       ON a.involved_party_key = d.involved_party_key
LEFT JOIN wdr_int_core.dim_reference r
       ON r.attribute_map_cd = d.naics_cd
"""


def _connect(database: str):
    import psycopg2

    if database not in DEMO_DATABASES:
        raise SystemExit(f"refusing non-demo database {database!r}")
    user = os.environ.get("RECON_RW_USER")
    password = os.environ.get("RECON_RW_PASSWORD")
    if not user or not password:
        raise SystemExit("RECON_RW_USER/RECON_RW_PASSWORD not set")
    return psycopg2.connect(
        host="localhost", port=5432, database=database, user=user, password=password
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--views", choices=["all", "stage", "int"], default="all")
    args = ap.parse_args()

    load_credentials(Path(".env"))
    if args.views in ("all", "stage"):
        with _connect("wdr_uat_active_stage") as conn, conn.cursor() as cur:
            cur.execute(STAGE_VIEW_SQL)
        print("created signature.v_dim_customer_src (wdr_uat_active_stage)")
    if args.views in ("all", "int"):
        with _connect("wdr_uat_integration") as conn, conn.cursor() as cur:
            cur.execute(INT_VIEW_SQL)
        print("created wdr_int_core.v_party_snpsht_src (wdr_uat_integration)")


if __name__ == "__main__":
    main()
