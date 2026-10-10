"""Seed the Webster Bank source schema (legacy core-banking extract).

Creates the `webster` schema in the local recon database with five tables —
branch, customer, account, txn, loan — populated with deterministic seeded
data, then grants SELECT to recon_ro so the reconciliation source adapter
reads it with the same read-only role used everywhere else.

Usage:
    .venv/Scripts/python scripts/generate_bank_source.py [--rows 10000] [--seed 42]

Scale --rows toward millions for volume tests; row counts of the other
tables scale proportionally (accounts ~1.3x, txns ~2.5x, loans ~0.4x).
"""

import argparse
import os
import random
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
from faker import Faker
from psycopg2.extras import execute_values

_DDL = """
CREATE SCHEMA IF NOT EXISTS webster;
CREATE TABLE IF NOT EXISTS webster.branch (
    branch_no  integer PRIMARY KEY,
    branch_nm  varchar(80)  NOT NULL,
    city       varchar(60)  NOT NULL,
    st         varchar(2)   NOT NULL,
    region_cd  varchar(2)   NOT NULL
);
CREATE TABLE IF NOT EXISTS webster.customer (
    cust_no   integer PRIMARY KEY,
    fname     varchar(60)  NOT NULL,
    lname     varchar(60)  NOT NULL,
    dob_txt   varchar(10)  NOT NULL,
    ssn       varchar(11)  NOT NULL,
    addr1     varchar(120) NOT NULL,
    city      varchar(60)  NOT NULL,
    st        varchar(2)   NOT NULL,
    zip       varchar(10)  NOT NULL,
    phone     varchar(25),
    email     varchar(120) NOT NULL,
    open_dt   date         NOT NULL,
    stat_cd   varchar(1)   NOT NULL
);
CREATE TABLE IF NOT EXISTS webster.account (
    acct_no   integer PRIMARY KEY,
    cust_no   integer NOT NULL REFERENCES webster.customer(cust_no),
    prod_cd   varchar(3)   NOT NULL,
    branch_no integer NOT NULL REFERENCES webster.branch(branch_no),
    open_dt   date         NOT NULL,
    bal_amt   numeric(15,2) NOT NULL,
    stat_cd   varchar(1)   NOT NULL
);
CREATE TABLE IF NOT EXISTS webster.txn (
    txn_id    integer PRIMARY KEY,
    acct_no   integer NOT NULL REFERENCES webster.account(acct_no),
    txn_dt    date         NOT NULL,
    txn_tm    varchar(8),
    txn_cd    varchar(3)   NOT NULL,
    amount    numeric(12,2) NOT NULL,
    desc_txt  varchar(100)
);
CREATE TABLE IF NOT EXISTS webster.loan (
    loan_no   integer PRIMARY KEY,
    cust_no   integer NOT NULL REFERENCES webster.customer(cust_no),
    loan_type varchar(4)   NOT NULL,
    orig_amt  numeric(14,2) NOT NULL,
    curr_bal  numeric(14,2) NOT NULL,
    rate_pct  numeric(5,3)  NOT NULL,
    start_dt  date         NOT NULL,
    mat_dt    date,
    stat_cd   varchar(1)   NOT NULL
);
"""

_STATES = ["CT", "NY", "NJ", "MA", "RI", "NH", "VT", "ME", "PA", "FL"]
_REGIONS = ["NE", "SE", "MW", "WE"]
_STATUS = ["A", "I", "S"]
_STATUS_W = [0.85, 0.10, 0.05]
_PRODUCTS = ["CHK", "SAV", "MMA", "CD"]
_PRODUCT_W = [0.50, 0.30, 0.12, 0.08]
_TXN_CODES = ["DBT", "CRD", "FEE", "INT"]
_TXN_W = [0.55, 0.35, 0.06, 0.04]
_LOAN_TYPES = ["AUTO", "HOME", "PERS", "COMM"]


def _money(lo, hi, rnd):
    return Decimal(rnd.uniform(lo, hi)).quantize(Decimal("0.01"))


def gen_branches(fake, rnd, n=20):
    seen = set()
    rows = []
    for i in range(1, n + 1):
        city = fake.city()
        while (city, i) in seen:
            city = fake.city()
        rows.append(
            (
                100 + i,
                f"{city} {'Branch' if i % 3 else 'Banking Center'}",
                city,
                rnd.choice(_STATES),
                rnd.choice(_REGIONS),
            )
        )
    return rows


def gen_customers(fake, rnd, n):
    rows = []
    base = date(1995, 1, 1)
    for i in range(1, n + 1):
        dob = fake.date_of_birth(minimum_age=18, maximum_age=90)
        rows.append(
            (
                i,
                fake.first_name(),
                fake.last_name(),
                dob.strftime("%m/%d/%Y"),  # legacy varchar date
                f"{rnd.randint(100, 999)}-{rnd.randint(10, 99)}-{rnd.randint(1000, 9999)}",
                fake.street_address()[:120],
                fake.city()[:60],
                rnd.choice(_STATES),
                fake.zipcode()[:10],
                fake.phone_number()[:25] if rnd.random() < 0.9 else None,
                fake.email()[:120],
                base + timedelta(days=rnd.randint(0, 365 * 25)),
                rnd.choices(_STATUS, weights=_STATUS_W)[0],
            )
        )
    return rows


def gen_accounts(fake, rnd, customers, branches):
    rows = []
    acct = 1000000
    for cust in customers:
        n_acct = 1 + (1 if rnd.random() < 0.3 else 0)
        for _ in range(n_acct):
            acct += 1
            open_dt = cust[11] + timedelta(days=rnd.randint(0, 3650))
            if open_dt > date.today():
                open_dt = date.today() - timedelta(days=rnd.randint(0, 365))
            rows.append(
                (
                    acct,
                    cust[0],
                    rnd.choices(_PRODUCTS, weights=_PRODUCT_W)[0],
                    rnd.choice(branches)[0],
                    open_dt,
                    _money(0, 50000, rnd),
                    rnd.choices(_STATUS, weights=_STATUS_W)[0],
                )
            )
    return rows


def gen_txns(fake, rnd, accounts):
    rows = []
    txn = 0
    today = date.today()
    for acct in accounts:
        for _ in range(rnd.randint(1, 3)):
            txn += 1
            dt = acct[4] + timedelta(days=rnd.randint(0, max(1, (today - acct[4]).days)))
            rows.append(
                (
                    txn,
                    acct[0],
                    dt,
                    f"{rnd.randint(0, 23):02d}:{rnd.randint(0, 59):02d}:{rnd.randint(0, 59):02d}"
                    if rnd.random() < 0.9
                    else None,
                    rnd.choices(_TXN_CODES, weights=_TXN_W)[0],
                    _money(0.01, 5000, rnd),
                    fake.sentence(nb_words=4)[:100] if rnd.random() < 0.85 else None,
                )
            )
    return rows


def gen_loans(fake, rnd, customers):
    rows = []
    loan = 500000
    for cust in customers:
        if rnd.random() >= 0.4:
            continue
        loan += 1
        orig = _money(5000, 400000, rnd)
        curr = (orig * Decimal(rnd.uniform(0, 1))).quantize(Decimal("0.01"))
        start = cust[11] + timedelta(days=rnd.randint(0, 3650))
        if start > date.today():
            start = date.today() - timedelta(days=30)
        mat = start + timedelta(days=rnd.randint(365, 365 * 30)) if rnd.random() < 0.95 else None
        rows.append(
            (
                loan,
                cust[0],
                rnd.choice(_LOAN_TYPES),
                orig,
                curr,
                Decimal(rnd.uniform(2.5, 9.5)).quantize(Decimal("0.001")),
                start,
                mat,
                rnd.choices(_STATUS, weights=_STATUS_W)[0],
            )
        )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=10000, help="customer rows (others scale)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--confirm-synthetic", action="store_true")
    args = ap.parse_args()
    if not args.confirm_synthetic or args.rows < 1:
        ap.error("Use --confirm-synthetic and a positive row count for a new local demo schema")
    if os.environ.get("PGHOST", "localhost") not in ("localhost", "127.0.0.1", "::1"):
        ap.error("Demo seeding is restricted to local PostgreSQL")

    fake = Faker()
    Faker.seed(args.seed)
    rnd = random.Random(args.seed)

    conn = psycopg2.connect(
        host=os.environ.get("PGHOST", "localhost"),
        port=int(os.environ.get("PGPORT", 5432)),
        dbname=os.environ.get("PGDATABASE", "recon"),
        user=os.environ["RECON_ADMIN_USER"],
        password=os.environ["RECON_ADMIN_PASSWORD"],
    )
    conn.autocommit = False
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ("webster",))
    if cur.fetchone():
        cur.close()
        conn.close()
        raise RuntimeError(
            "Refusing to overwrite existing webster schema; use a fresh demo database"
        )
    cur.execute(_DDL)

    print("generating branches …")
    branches = gen_branches(fake, rnd)
    print(f"generating {args.rows} customers …")
    customers = gen_customers(fake, rnd, args.rows)
    print("generating accounts …")
    accounts = gen_accounts(fake, rnd, customers, branches)
    print("generating transactions …")
    txns = gen_txns(fake, rnd, accounts)
    print("generating loans …")
    loans = gen_loans(fake, rnd, customers)

    data = {
        "branch": ("branch_no,branch_nm,city,st,region_cd", branches),
        "customer": (
            "cust_no,fname,lname,dob_txt,ssn,addr1,city,st,zip,phone,email,open_dt,stat_cd",
            customers,
        ),
        "account": ("acct_no,cust_no,prod_cd,branch_no,open_dt,bal_amt,stat_cd", accounts),
        "txn": ("txn_id,acct_no,txn_dt,txn_tm,txn_cd,amount,desc_txt", txns),
        "loan": (
            "loan_no,cust_no,loan_type,orig_amt,curr_bal,rate_pct,start_dt,mat_dt,stat_cd",
            loans,
        ),
    }
    for table, (cols, rows) in data.items():
        execute_values(
            cur, f"INSERT INTO webster.{table} ({cols}) VALUES %s", rows, page_size=10000
        )
        print(f"  webster.{table}: {len(rows)} rows")

    cur.execute(
        """
        GRANT USAGE ON SCHEMA webster TO recon_ro;
        GRANT SELECT ON ALL TABLES IN SCHEMA webster TO recon_ro;
        ALTER DEFAULT PRIVILEGES IN SCHEMA webster GRANT SELECT ON TABLES TO recon_ro;
        """
    )
    conn.commit()
    cur.close()
    conn.close()
    print("done — webster schema seeded and granted to recon_ro")


if __name__ == "__main__":
    main()
