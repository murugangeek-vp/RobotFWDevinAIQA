"""Generate deterministic sample data for the customer contract.

Outputs (data/samples/):
  customer.csv                 100 valid rows
  customer_bad_header.csv      'email' column renamed -> header validation fails
  customer_bad_dq.csv          exactly 5 seeded rule violations
  customer_missing_rows.csv    99 rows (row-count metadata check fails; also
                               usable to seed a missing-in-target defect)
  account_extract.csv          100 rows, main file for the multi-source MVP
                               (acct_seq 11001-11100)
  account_codes.csv            per-account branch/channel codes; joined onto
                               account_extract on acct_seq (loan_account = SN|11001)
  account_codes_bad_dup.csv    duplicate join key -> read must fail closed
  account_codes_bad_missing.csv missing join key -> read must fail closed
                               (require_match) and row-count check fails
  account_codes_bad_header.csv 'branch_code' renamed -> join header fails

Run:  .venv/Scripts/python scripts/generate_samples.py
"""

import csv
import random
from datetime import date, timedelta
from pathlib import Path

random.seed(42)
OUT = Path(__file__).resolve().parents[1] / "data" / "samples"
OUT.mkdir(parents=True, exist_ok=True)

FIRST = ["Asha", "Ravi", "Mei", "Liam", "Zara", "Noah", "Ivy", "Omar", "Lena", "Kai"]
LAST = ["Patel", "Kumar", "Chen", "Smith", "Ali", "Brown", "Diaz", "Khan", "Rossi", "Ito"]
COUNTRIES = ["us", "gb", "in", "de", "fr", "jp", "br", "ca", "au", "nl"]
STATUS = ["active", "inactive", "suspended"]

HEADER = [
    "customer_id",
    "first_name",
    "last_name",
    "email",
    "dob",
    "country",
    "balance",
    "status",
    "created_at",
]


def rows(n=100):
    base = date(2024, 1, 1)
    for i in range(1, n + 1):
        first, last = FIRST[i % 10], LAST[(i * 3) % 10]
        yield [
            i,
            first,
            last,
            f"User{i}@Example.COM" if i % 7 == 0 else f"user{i}@example.com",
            str(date(1970 + i % 40, (i % 12) + 1, (i % 28) + 1)),
            COUNTRIES[i % 10],
            f"{random.uniform(0, 5000):.4f}",
            STATUS[i % 3],
            f"{base + timedelta(days=i)} 10:{i % 60:02d}:00",
        ]


def write(name, data, header=HEADER):
    with open(OUT / name, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(data)
    print(f"wrote {name} ({len(data)} rows)")


clean = list(rows())
write("customer.csv", clean)

# bad header: rename 'email' -> 'email_address'
bad_header = [c if c != "email" else "email_address" for c in HEADER]
write("customer_bad_header.csv", clean, header=bad_header)

# bad DQ: exactly 5 seeded violations, one per rule family
bad_dq = [r[:] for r in clean]
bad_dq[4][3] = ""  # row 5 : null email        -> transform_input_not_null
bad_dq[6][6] = "-50.00"  # row 7 : balance < 0       -> range
bad_dq[8][7] = "unknown"  # row 9 : bad status        -> allowed_values
bad_dq[10][0] = "abc"  # row 11: non-integer id    -> type
bad_dq[12][0] = bad_dq[11][0]  # row 13: duplicate key   -> unique
write("customer_bad_dq.csv", bad_dq)

# missing row: 99 rows
write("customer_missing_rows.csv", clean[:99])

# ---- multi-source MVP: account extract + branch/channel codes file --------
# business case: target column loan_account merges two files, e.g. SN|11001

ACCT_HEADER = ["acct_seq", "cust_id", "prod_code", "opened_on", "ccy", "bal", "st"]
CODES_HEADER = ["acct_seq", "branch_code", "channel_code"]
PRODUCTS = ["LN", "FD", "RD"]
BRANCHES = ["SN", "PR", "CL", "ED", "DL"]
CHANNELS = ["ib", "mb", "br"]
ACCT_STATUS = ["A", "C", "S"]
CCY = ["usd", "eur", "gbp", "inr", "jpy"]


def account_rows(n=100):
    base = date(2024, 1, 1)
    for i in range(1, n + 1):
        yield [
            10999 + i,  # acct_seq 11000..11099
            i,
            PRODUCTS[i % 3],
            str(base + timedelta(days=i)),
            CCY[i % 5],
            f"{random.uniform(100, 50000):.4f}",
            ACCT_STATUS[i % 3],
        ]


def code_rows(n=100):
    for i in range(1, n + 1):
        yield [10999 + i, BRANCHES[(i - 1) % 5], CHANNELS[i % 3]]


accounts = list(account_rows())
codes = list(code_rows())
write("account_extract.csv", accounts, header=ACCT_HEADER)
write("account_codes.csv", codes, header=CODES_HEADER)

# duplicate join key: acct_seq 11050 appears twice -> read fails closed
dup = codes[:50] + [[11050, "XX", "ib"]] + codes[50:]
write("account_codes_bad_dup.csv", dup, header=CODES_HEADER)

# missing join key: no row for acct_seq 11050 -> require_match fails closed
missing = [r for r in codes if r[0] != 11050]
write("account_codes_bad_missing.csv", missing, header=CODES_HEADER)

# drifted codes header: branch_code renamed -> join header validation fails
bad_codes_header = [c if c != "branch_code" else "branch" for c in CODES_HEADER]
write("account_codes_bad_header.csv", codes, header=bad_codes_header)
