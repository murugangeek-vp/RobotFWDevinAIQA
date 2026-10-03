"""Generate SYNTHETIC Webster-style extracts for local migration verification.

No real customer data: names come from a short fixed list, emails use the reserved
example.com domain, tax ids use the never-issued SSN area 000, and account numbers are
random 10-digit strings (several with leading zeros to prove they survive as strings).
Output is deterministic (fixed seed) so committed fixtures are reproducible:

    python scripts/generate_bank_samples.py
"""

import csv
import random
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "data" / "samples" / "bank"
FIRST = ["Ava", "Liam", "Mia", "Noah", "Emma", "Lucas", "Zoe", "Ethan", "Ivy", "Owen"]
LAST = ["Stone", "Rivera", "Patel", "Nguyen", "Okafor", "Kowalski", "Haddad", "Silva"]
SEGMENTS = ["R"] * 8 + ["B"] * 2
PRODUCTS = ["CHK01", "SAV01", "CD12"]
STATUSES = ["A"] * 8 + ["C", "D"]
CURRENCIES = ["USD"] * 9 + ["CAD"]


def _write(table: str, header: list, rows: list, parts: int) -> None:
    folder = OUT / table
    folder.mkdir(parents=True, exist_ok=True)
    size = -(-len(rows) // parts)
    for i in range(parts):
        with open(folder / f"part-{i + 1:04d}.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(header)
            w.writerows(rows[i * size : (i + 1) * size])


def _write_ctl(table: str, entries: list) -> None:
    """Bank trailer file: name,group,value — group values in SOURCE domain."""
    with open(OUT / table / "_control.ctl", "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["name", "group", "value"])
        w.writerows(entries)


def _counts(rows: list, idx: int) -> dict:
    out: dict = {}
    for r in rows:
        out[r[idx]] = out.get(r[idx], 0) + 1
    return out


def _sums(rows: list, key_idx: tuple, val_idx: int) -> dict:
    out: dict = {}
    for r in rows:
        key = tuple(r[i] for i in key_idx)
        out[key] = out.get(key, Decimal(0)) + Decimal(str(r[val_idx]))
    return out


def main(customers: int = 250, accounts: int = 400, transactions: int = 2000) -> None:
    rnd = random.Random(20240501)
    cust_rows = []
    for cid in range(100001, 100001 + customers):
        first, last = rnd.choice(FIRST), rnd.choice(LAST)
        dob = date(1940, 1, 1) + timedelta(days=rnd.randrange(0, 23000))
        opened = datetime(2005, 1, 1) + timedelta(seconds=rnd.randrange(0, 600_000_000))
        cust_rows.append(
            [
                cid,
                first,
                last,
                f"{first}.{last}{cid}@Example.com",
                dob.isoformat(),
                f"000-{rnd.randrange(10, 99)}-{rnd.randrange(1000, 9999)}",
                rnd.choice(SEGMENTS),
                opened.strftime("%Y-%m-%d %H:%M:%S"),
            ]
        )
    _write(
        "customer",
        [
            "cust_id",
            "first_nm",
            "last_nm",
            "email_addr",
            "birth_dt",
            "tax_id",
            "segment_cd",
            "open_ts",
        ],
        cust_rows,
        parts=1,
    )
    _write_ctl(
        "customer",
        [("row_count", "", customers)]
        + [("customers_by_segment", seg, n) for seg, n in sorted(_counts(cust_rows, 6).items())]
        + [
            ("tax_id_nulls", "", 0),
            ("earliest_customer", "", min(r[7] for r in cust_rows)),
        ],
    )

    acct_rows, acct_numbers = [], set()
    while len(acct_numbers) < accounts:
        acct_numbers.add(f"{rnd.randrange(0, 10**10):010d}")
    for i, acct in enumerate(sorted(acct_numbers)):
        cid = 100001 + (i % customers)
        balance = Decimal(rnd.randrange(0, 25_000_000)) / 100
        opened = date(2005, 1, 1) + timedelta(days=rnd.randrange(0, 7000))
        acct_rows.append(
            [
                acct,
                cid,
                rnd.choice(PRODUCTS),
                rnd.choice(CURRENCIES),
                opened.isoformat(),
                rnd.choice(STATUSES),
                f"{balance:.2f}",
            ]
        )
    _write(
        "account",
        ["acct_no", "cust_id", "prod_cd", "ccy", "open_dt", "status_cd", "cur_bal"],
        acct_rows,
        parts=2,
    )
    _write_ctl(
        "account",
        [("row_count", "", accounts)]
        + [("accounts_by_status", st, n) for st, n in sorted(_counts(acct_rows, 5).items())]
        + [
            ("balance_by_currency", c, f"{v:.2f}")
            for (c,), v in sorted(_sums(acct_rows, (3,), 6).items())
        ]
        + [
            ("balance_by_product_currency", "|".join(k), f"{v:.2f}")
            for k, v in sorted(_sums(acct_rows, (2, 3), 6).items())
        ]
        + [
            ("max_balance", "", f"{max(Decimal(r[6]) for r in acct_rows):.2f}"),
            ("distinct_customers", "", len({r[1] for r in acct_rows})),
        ],
    )

    accts = [r[0] for r in acct_rows]
    ccy = {r[0]: r[3] for r in acct_rows}
    txn_rows = []
    for tid in range(5000001, 5000001 + transactions):
        acct = rnd.choice(accts)
        posted = datetime(2024, 1, 1) + timedelta(seconds=rnd.randrange(0, 15_000_000))
        amount = Decimal(rnd.randrange(1, 500_000)) / 100
        txn_rows.append(
            [
                tid,
                acct,
                posted.strftime("%Y-%m-%d %H:%M:%S"),
                f"{amount:.2f}",
                rnd.choice(["DR", "CR"]),
                ccy[acct],
                rnd.choice(["POS PURCHASE", "ACH CREDIT", "ATM WITHDRAWAL", "WIRE OUT", "FEE"]),
            ]
        )
    _write(
        "transaction",
        ["txn_id", "acct_no", "post_ts", "amt", "dr_cr_ind", "ccy", "narrative"],
        txn_rows,
        parts=2,
    )
    _write_ctl(
        "transaction",
        [("row_count", "", transactions), ("txn_count", "", transactions)]
        + [
            ("amount_by_direction_currency", "|".join(k), f"{v:.2f}")
            for k, v in sorted(_sums(txn_rows, (4, 5), 3).items())
        ]
        + [
            ("latest_posting", "", max(r[2] for r in txn_rows)),
            ("earliest_posting", "", min(r[2] for r in txn_rows)),
        ],
    )
    print(f"wrote synthetic fixtures to {OUT}")


if __name__ == "__main__":
    main()
