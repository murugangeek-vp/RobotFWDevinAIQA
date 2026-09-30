"""Generate deterministic sample data for the customer contract.

Outputs (data/samples/):
  customer.csv                 100 valid rows
  customer_bad_header.csv      'email' column renamed -> header validation fails
  customer_bad_dq.csv          exactly 5 seeded rule violations
  customer_missing_rows.csv    99 rows (row-count metadata check fails; also
                               usable to seed a missing-in-target defect)

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

HEADER = ["customer_id", "first_name", "last_name", "email", "dob",
          "country", "balance", "status", "created_at"]


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
bad_dq[4][3] = ""            # row 5 : null email        -> transform_input_not_null
bad_dq[6][6] = "-50.00"      # row 7 : balance < 0       -> range
bad_dq[8][7] = "unknown"     # row 9 : bad status        -> allowed_values
bad_dq[10][0] = "abc"        # row 11: non-integer id    -> type
bad_dq[12][0] = bad_dq[11][0]  # row 13: duplicate key   -> unique
write("customer_bad_dq.csv", bad_dq)

# missing row: 99 rows
write("customer_missing_rows.csv", clean[:99])
