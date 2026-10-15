"""Seed the Bank-1 source schema (ent_* enterprise core-banking layout).

Creates schema `bank1` in the local recon database with the enterprise
tables — geography, RBAC users/roles, customer master, products,
account_master + savings/fd/loan sub-ledgers, transaction_journal —
populated with deterministic synthetic data, grants SELECT to recon_ro,
and emits the bank trailer file certifying the Controls-sheet totals.

Usage:
    .venv/Scripts/python scripts/generate_bank1_source.py [--rows 500] [--seed 7]

--rows is the customer count; accounts/txns scale proportionally.
"""

import argparse
import csv
import os
import random
import sys
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg2
from faker import Faker
from psycopg2.extras import execute_values

_DDL = """
CREATE SCHEMA IF NOT EXISTS bank1;
CREATE TABLE IF NOT EXISTS bank1.ent_countries (
    country_iso_code CHAR(3) PRIMARY KEY,
    country_name VARCHAR(100) NOT NULL,
    currency_code CHAR(3) NOT NULL
);
CREATE TABLE IF NOT EXISTS bank1.ent_states (
    state_id VARCHAR(10) PRIMARY KEY,
    state_name VARCHAR(100) NOT NULL,
    country_iso_code CHAR(3) REFERENCES bank1.ent_countries(country_iso_code)
);
CREATE TABLE IF NOT EXISTS bank1.ent_cities (
    city_code VARCHAR(10) PRIMARY KEY,
    city_name VARCHAR(100) NOT NULL,
    state_id VARCHAR(10) REFERENCES bank1.ent_states(state_id)
);
CREATE TABLE IF NOT EXISTS bank1.ent_branches (
    branch_routing_id VARCHAR(20) PRIMARY KEY,
    branch_name VARCHAR(150) NOT NULL,
    city_code VARCHAR(10) REFERENCES bank1.ent_cities(city_code),
    branch_tier VARCHAR(10) CHECK (branch_tier IN ('TIER_1', 'TIER_2', 'TIER_3')),
    is_active BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS bank1.app_roles (
    role_id VARCHAR(30) PRIMARY KEY,
    role_description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS bank1.app_users (
    user_emp_id VARCHAR(20) PRIMARY KEY,
    username VARCHAR(50) UNIQUE NOT NULL,
    primary_role_id VARCHAR(30) REFERENCES bank1.app_roles(role_id),
    home_branch_routing_id VARCHAR(20) REFERENCES bank1.ent_branches(branch_routing_id),
    security_clearance_band INT CHECK (security_clearance_band BETWEEN 1 AND 5),
    ip_whitelist_range VARCHAR(50),
    last_login_timestamp TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bank1.customer_master (
    customer_urn VARCHAR(20) PRIMARY KEY,
    legal_name VARCHAR(200) NOT NULL,
    customer_type VARCHAR(20) CHECK (customer_type IN ('RETAIL', 'CORPORATE', 'SME', 'HNI')),
    risk_rating_score INT CHECK (risk_rating_score BETWEEN 1 AND 100),
    aml_kyc_status VARCHAR(20) CHECK (aml_kyc_status IN ('VERIFIED', 'PENDING', 'SUSPENDED')),
    kyc_expiry_date DATE NOT NULL
);
CREATE TABLE IF NOT EXISTS bank1.bank_products (
    product_code VARCHAR(20) PRIMARY KEY,
    product_category VARCHAR(10) CHECK (product_category IN ('SAVINGS', 'LOAN', 'FD')),
    product_display_name VARCHAR(100) NOT NULL,
    base_interest_rate DECIMAL(5, 4) NOT NULL
);
CREATE TABLE IF NOT EXISTS bank1.account_master (
    account_number VARCHAR(34) PRIMARY KEY,
    customer_urn VARCHAR(20) REFERENCES bank1.customer_master(customer_urn),
    product_code VARCHAR(20) REFERENCES bank1.bank_products(product_code),
    hosting_branch_id VARCHAR(20) REFERENCES bank1.ent_branches(branch_routing_id),
    account_status VARCHAR(20) CHECK (account_status IN ('ACTIVE', 'DORMANT', 'FROZEN', 'CLOSED')),
    currency_iso CHAR(3) NOT NULL,
    is_insider_account BOOLEAN DEFAULT FALSE,
    opened_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS bank1.ledger_savings (
    account_number VARCHAR(34) PRIMARY KEY REFERENCES bank1.account_master(account_number),
    available_balance DECIMAL(18, 4) NOT NULL,
    book_balance DECIMAL(18, 4) NOT NULL,
    overdraft_limit DECIMAL(15, 4) DEFAULT 0.0000
);
CREATE TABLE IF NOT EXISTS bank1.ledger_loans (
    account_number VARCHAR(34) PRIMARY KEY REFERENCES bank1.account_master(account_number),
    principal_outstanding DECIMAL(18, 4) NOT NULL,
    sanctioned_amount DECIMAL(18, 4) NOT NULL,
    tenure_months INT NOT NULL,
    emi_amount DECIMAL(15, 4) NOT NULL,
    next_due_date DATE
);
CREATE TABLE IF NOT EXISTS bank1.ledger_fixed_deposits (
    account_number VARCHAR(34) PRIMARY KEY REFERENCES bank1.account_master(account_number),
    principal_deposit DECIMAL(18, 4) NOT NULL,
    maturity_amount DECIMAL(18, 4) NOT NULL,
    maturity_date DATE NOT NULL,
    auto_renewal_flag BOOLEAN DEFAULT FALSE
);
CREATE TABLE IF NOT EXISTS bank1.transaction_journal (
    tx_reference_id VARCHAR(40) PRIMARY KEY,
    source_account_number VARCHAR(34) REFERENCES bank1.account_master(account_number),
    destination_account_number VARCHAR(34),
    tx_type VARCHAR(20) CHECK (tx_type IN ('DEBIT_TRANSFER', 'CREDIT_TRANSFER', 'CASH_WITHDRAWAL', 'INTEREST_PAYOUT')),
    amount DECIMAL(18, 4) NOT NULL,
    value_date TIMESTAMP NOT NULL,
    execution_user_id VARCHAR(20) REFERENCES bank1.app_users(user_emp_id)
);
"""

_COUNTRIES = [("IND", "India", "INR"), ("USA", "United States", "USD"), ("ESP", "Spain", "EUR")]
_STATES = [
    ("MH", "Maharashtra", "IND"),
    ("KA", "Karnataka", "IND"),
    ("NY", "New York", "USA"),
    ("TX", "Texas", "USA"),
    ("MD", "Madrid", "ESP"),
]
_CITIES = [
    ("BOM", "Mumbai", "MH"),
    ("PUN", "Pune", "MH"),
    ("BLR", "Bengaluru", "KA"),
    ("NYC", "New York City", "NY"),
    ("HOU", "Houston", "TX"),
    ("MAD", "Madrid", "MD"),
]
_ROLES = [
    ("CORE_TELLER", "Front line transaction operations clerk"),
    ("CREDIT_ANALYST", "Loan review and corporate asset manager"),
    ("CROSS_BORDER_AUDITOR", "Global compliance and anti-fraud inspector"),
    ("BRANCH_MANAGER", "Branch operations owner"),
]
_PRODUCTS = [
    ("PRD_SAV_01", "SAVINGS", "Premium Institutional Savings", Decimal("0.0400")),
    ("PRD_SAV_02", "SAVINGS", "Retail Everyday Savings", Decimal("0.0300")),
    ("PRD_SAV_03", "SAVINGS", "Student Starter Savings", Decimal("0.0250")),
    ("PRD_LND_05", "LOAN", "Commercial Secured Mortgage", Decimal("0.0825")),
    ("PRD_LND_06", "LOAN", "Retail Home Loan", Decimal("0.0750")),
    ("PRD_LND_07", "LOAN", "SME Working Capital", Decimal("0.0950")),
    ("PRD_FD_02", "FD", "High Yield Fixed Deposit", Decimal("0.0710")),
    ("PRD_FD_03", "FD", "Senior Citizen FD", Decimal("0.0785")),
]
_CUST_TYPES = ["RETAIL", "CORPORATE", "SME", "HNI"]
_CUST_W = [0.70, 0.12, 0.13, 0.05]
_KYC = ["VERIFIED", "PENDING", "SUSPENDED"]
_KYC_W = [0.85, 0.10, 0.05]
_ACCT_STATUS = ["ACTIVE", "DORMANT", "FROZEN", "CLOSED"]
_ACCT_W = [0.80, 0.08, 0.04, 0.08]
_TXN_TYPES = ["DEBIT_TRANSFER", "CREDIT_TRANSFER", "CASH_WITHDRAWAL", "INTEREST_PAYOUT"]
_TXN_W = [0.45, 0.35, 0.15, 0.05]
_ACCT_STATUSES = {"ACTIVE", "DORMANT"}


def _iban(country, routing, seq):
    return f"{country}91{routing}{seq:013d}"[:34]


def _write_trailer(path, savings, loans, fds, accounts, customers):
    """Post-mapping certified totals matching the workbook Controls sheet."""
    rows = [
        ("savings_book", "", sum(s[2] for s in savings)),
        ("savings_rows", "", len(savings)),
        ("loans_principal", "", sum(ln[1] for ln in loans)),
        ("fd_principal", "", sum(f[1] for f in fds)),
        ("customers", "", len(customers)),
        ("accounts", "", len(accounts)),
    ]
    by_status = Counter(a[4] for a in accounts)
    for status, n in sorted(by_status.items()):
        rows.append(("account_status", status, n))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["name", "group", "value"])
        w.writerows(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", type=int, default=500)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--trailer", default="data/trailer/bank1_trailer.csv")
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
    cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", ("bank1",))
    if cur.fetchone():
        cur.close()
        conn.close()
        raise RuntimeError("Refusing to overwrite existing bank1 schema; drop it first")
    cur.execute(_DDL)

    # Geography
    cur.executemany("INSERT INTO bank1.ent_countries VALUES (%s,%s,%s)", _COUNTRIES)
    cur.executemany("INSERT INTO bank1.ent_states VALUES (%s,%s,%s)", _STATES)
    cur.executemany("INSERT INTO bank1.ent_cities VALUES (%s,%s,%s)", _CITIES)

    branches = []
    for i, (city_code, _cname, _st) in enumerate(_CITIES * 2):
        routing = f"MSSB{i:07d}"
        branches.append(
            (
                routing,
                fake.company() + " Branch",
                city_code,
                rnd.choice(["TIER_1", "TIER_2", "TIER_3"]),
                # last branch inactive — exercises the workbook WHERE scope
                # filter; it must never receive accounts (would be an orphan)
                i < len(_CITIES * 2) - 1,
            )
        )
    active_branches = branches[:-1]
    execute_values(cur, "INSERT INTO bank1.ent_branches VALUES %s", branches)

    cur.executemany("INSERT INTO bank1.app_roles VALUES (%s,%s)", _ROLES)
    users = []
    for i in range(12):
        users.append(
            (
                f"EMP-{9000 + i}",
                fake.user_name(),
                rnd.choice([r[0] for r in _ROLES]),
                rnd.choice(branches)[0],
                rnd.randint(1, 5),
                fake.ipv4_private() + "/32" if rnd.random() < 0.6 else "*",
                datetime(2026, 10, rnd.randint(1, 14), rnd.randint(8, 17)),
            )
        )
    execute_values(cur, "INSERT INTO bank1.app_users VALUES %s", users)

    customers = []
    for i in range(args.rows):
        customers.append(
            (
                f"URN-{1000000 + i}",
                fake.company() if rnd.random() < 0.2 else fake.name(),
                rnd.choices(_CUST_TYPES, _CUST_W)[0],
                rnd.randint(1, 100),
                rnd.choices(_KYC, _KYC_W)[0],
                date(2027, 1, 1) + timedelta(days=rnd.randint(0, 1000)),
            )
        )
    execute_values(cur, "INSERT INTO bank1.customer_master VALUES %s", customers)

    cur.executemany("INSERT INTO bank1.bank_products VALUES (%s,%s,%s,%s)", _PRODUCTS)

    accounts, savings, loans, fds = [], [], [], []
    state_of = {c[0]: c[2] for c in _CITIES}
    country_of = {s[0]: s[2] for s in _STATES}
    iso_of = {"IND": "IN", "USA": "US", "ESP": "ES"}
    ccy_of = {"IN": "INR", "US": "USD", "ES": "EUR"}
    for i in range(int(args.rows * 1.6)):
        cust = rnd.choice(customers)
        prod = rnd.choice(_PRODUCTS)
        branch = rnd.choice(active_branches)
        iso = iso_of[country_of[state_of[branch[2]]]]
        acct = _iban(iso, branch[0].replace("MSSB", "M"), i)
        status = rnd.choices(_ACCT_STATUS, _ACCT_W)[0]
        insider = rnd.random() < 0.02
        accounts.append(
            (
                acct,
                cust[0],
                prod[0],
                branch[0],
                status,
                ccy_of[iso],
                insider,
                datetime(2020, 1, 1) + timedelta(days=rnd.randint(0, 2000)),
            )
        )
        if prod[1] == "SAVINGS":
            bal = Decimal(rnd.randint(1000, 9000000)) / 100
            avail = bal - Decimal(rnd.randint(0, 50000)) / 100
            savings.append((acct, avail, bal, Decimal(rnd.choice([0, 5000, 25000]))))
        elif prod[1] == "LOAN":
            sanctioned = Decimal(rnd.randint(500000, 90000000)) / 100
            outstanding = sanctioned - Decimal(rnd.randint(0, 60)) * sanctioned / 100
            loans.append(
                (
                    acct,
                    outstanding,
                    sanctioned,
                    rnd.choice([120, 180, 240, 300]),
                    (sanctioned / 240).quantize(Decimal("0.0001")),
                    date(2026, 11, rnd.randint(1, 28)),
                )
            )
        else:
            principal = Decimal(rnd.randint(100000, 50000000)) / 100
            fds.append(
                (
                    acct,
                    principal,
                    (principal * Decimal("1.071")).quantize(Decimal("0.0001")),
                    date(2028, 1, 1) + timedelta(days=rnd.randint(0, 700)),
                    rnd.random() < 0.5,
                )
            )
    execute_values(cur, "INSERT INTO bank1.account_master VALUES %s", accounts)
    execute_values(cur, "INSERT INTO bank1.ledger_savings VALUES %s", savings)
    execute_values(cur, "INSERT INTO bank1.ledger_loans VALUES %s", loans)
    execute_values(cur, "INSERT INTO bank1.ledger_fixed_deposits VALUES %s", fds)

    txns = []
    live_accts = [a[0] for a in accounts if a[4] in _ACCT_STATUSES]
    for i in range(args.rows * 4):
        src = rnd.choice(live_accts)
        txns.append(
            (
                f"TXN-{args.seed}-{i:08d}",
                src,
                rnd.choice(live_accts) if rnd.random() < 0.5 else None,
                rnd.choices(_TXN_TYPES, _TXN_W)[0],
                Decimal(rnd.randint(100, 2500000)) / 100,
                datetime(2026, 9, 1) + timedelta(days=rnd.randint(0, 44)),
                rnd.choice(users)[0],
            )
        )
    execute_values(cur, "INSERT INTO bank1.transaction_journal VALUES %s", txns)

    cur.execute("GRANT USAGE ON SCHEMA bank1 TO recon_ro")
    cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA bank1 TO recon_ro")
    conn.commit()
    cur.close()
    conn.close()

    _write_trailer(args.trailer, savings, loans, fds, accounts, customers)
    for name, rows in (
        ("ent_branches", branches),
        ("app_users", users),
        ("customer_master", customers),
        ("bank_products", _PRODUCTS),
        ("account_master", accounts),
        ("ledger_savings", savings),
        ("ledger_loans", loans),
        ("ledger_fixed_deposits", fds),
        ("transaction_journal", txns),
    ):
        print(f"  bank1.{name}: {len(rows)} rows")
    print(f"trailer -> {args.trailer}")
    print("done — bank1 schema seeded and granted to recon_ro")


if __name__ == "__main__":
    main()
