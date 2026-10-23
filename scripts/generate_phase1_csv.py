"""Generate deterministic synthetic Phase-1 CSV extracts + trailer files.

Outputs:
  data/phase1/csv/{cup00301,cup01201,dim_reference}.csv   — positive extracts
  data/phase1/negative/<case>/*.csv                        — corrupted variants
      (full file set in each dir so a hop-1 env can point source.dir at it)
  data/trailer/phase1_hop{1,2,3}_trailer.csv               — certified totals

Synthetic only: faker-generated names/addresses, deterministic seeds. No real
customer data.

Run: .venv/Scripts/python.exe scripts/generate_phase1_csv.py
"""

import csv
import random
import shutil
from pathlib import Path

from faker import Faker

REPO = Path(__file__).resolve().parent.parent
CSV_DIR = REPO / "data" / "phase1" / "csv"
NEG_DIR = REPO / "data" / "phase1" / "negative"
TRAILER_DIR = REPO / "data" / "trailer"

LOAD_DATE = "2026-10-15"
N_CUSTOMERS = 200
SEED = 42071

CUP00301_HEADER = [
    "cubk",
    "cunbr",
    "cupers",
    "cucncd",
    "cusic",
    "cuna1",
    "cucfnm",
    "cuclnm",
    "cussnr",
    "cussty",
    "cujbdt",
    "cuadl1",
    "cuadl2",
    "cuadct",
    "cuadst",
    "cuadpc",
    "load_date",
]
CUP01201_HEADER = ["cunbr", "culegenty", "load_date"]
DIM_REFERENCE_HEADER = ["attribute_cd", "attribute_value_desc", "load_date", "attribute_map_cd"]

PERSON_TYPES = ["P", "P", "P", "P", "P", "P", "P", "N", "N", "N", "N", "N", "C", "C", "B"]
COUNTRY_CODES = [840, 840, 840, 124, 826, 392, 356]  # NUMBER(5,0) per spec
NAICS_CODES = [522110, 522120, 522210, 523110, 523120, 531110, 541511]
TAX_TYPES = ["S", "S", "S", "S", "E", "T", ""]
LEGAL_ENTITIES = ["SOLE PROP", "PARTNERSHIP", "LLC", "CORP", "TRUST"]

DIM_REFERENCE_ROWS = [
    # tax-id-type lookups (hop-2 join on attribute_cd; no map code)
    ["S", "SOCIAL SECURITY NUMBER", LOAD_DATE, ""],
    ["E", "EMPLOYER IDENTIFICATION NUMBER", LOAD_DATE, ""],
    ["T", "OTHER TAX IDENTIFIER", LOAD_DATE, ""],
    # naics -> customer_category overrides (hop-3 join on
    # attribute_map_cd = naics_cd; lpad'd 6-char codes)
    ["BANK_HOLDING", "NAICS 523110 - INVESTMENT BANKING", LOAD_DATE, "523110"],
    ["CREDIT_UNION", "NAICS 522120 - CREDIT UNIONS", LOAD_DATE, "522120"],
    ["FINTECH", "NAICS 541511 - CUSTOM SOFTWARE", LOAD_DATE, "541511"],
]

# Deterministic CUSTOMER_CATEGORY coverage — one row per CASE arm plus
# precedence edges (ref lookup beats type/name; LLC/INC arm precedes BANK
# arm; 'P' beats name markers). cupers/cusic/cuna1 positions 2/4/5.
_CATEGORY_CASES = {
    0: ("P", "522210", "ALICE INDIVIDUAL SAMPLE"),  # 'P' -> INDIVIDUAL
    1: ("C", "522210", "DELTA HOLDINGS LLC"),  # LLC -> CORP_SMALL
    2: ("C", "522210", "epsilon inc"),  # lowercase marker -> CORP_SMALL (UPPER fold)
    3: ("B", "522210", "FIRST RIVER BANK"),  # BANK -> BANK
    4: ("C", "522210", "BANK STREET LLC"),  # arm order: LLC checked before BANK
    5: ("C", "522210", "PLAIN SERVICES GROUP"),  # no marker -> NULL (no ELSE)
    6: ("P", "523110", "MARIA BANKINGTON"),  # ref hit beats 'P' and %BANK% name
    7: ("N", "522210", "NORTHFIELD LLC"),  # N->'C' in hop 2, LLC -> CORP_SMALL
    8: ("B", "522120", "ANY HOLDER"),  # ref hit 522120 -> CREDIT_UNION
    9: ("P", "522210", "CAROL BANKSTON"),  # 'P' arm evaluated before name arms
}


def _customers(fake):
    rows = []
    for i in range(N_CUSTOMERS):
        cupers = PERSON_TYPES[i % len(PERSON_TYPES)]
        row = [
            str(random.choice([1, 2, 3])),  # cubk
            f"C{i + 1:06d}",  # cunbr
            cupers,
            str(random.choice(COUNTRY_CODES)),  # cucncd
            "" if i % 7 == 0 else str(random.choice(NAICS_CODES)),  # cusic
            fake.name() if cupers == "P" else fake.company(),  # cuna1
            fake.first_name(),  # cucfnm
            fake.last_name(),  # cuclnm
            "" if i % 10 == 3 else str(random.randint(100000000, 999999999)),  # cussnr
            TAX_TYPES[i % len(TAX_TYPES)],  # cussty ('' -> null path)
            fake.date_of_birth(minimum_age=18, maximum_age=90).strftime("%Y%m%d"),
            fake.street_address().replace("\n", " "),  # cuadl1
            "" if i % 3 else fake.secondary_address(),  # cuadl2
            fake.city(),  # cuadct
            fake.state_abbr(),  # cuadst
            fake.postcode(),  # cuadpc
            LOAD_DATE,
        ]
        if i in _CATEGORY_CASES:
            row[2], row[4], row[5] = _CATEGORY_CASES[i]
        rows.append(row)
    return rows


def _legal_entities(customers):
    # ~80% coverage exercises the LEFT JOIN null path deterministically
    return [
        [r[1], LEGAL_ENTITIES[i % len(LEGAL_ENTITIES)], LOAD_DATE]
        for i, r in enumerate(customers)
        if i % 5 != 4
    ]


def _write(path, header, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(header)
        w.writerows(rows)


def _write_case(name, cup00301=None, cup01201=None, dim_reference=None, base=None):
    """One negative dir containing a complete 3-file set: mutate one file,
    copy the rest from the positive set."""
    out = NEG_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    cup, leg, ref = base
    _write(out / "cup00301.csv", CUP00301_HEADER, cup00301 if cup00301 is not None else cup)
    _write(out / "cup01201.csv", CUP01201_HEADER, cup01201 if cup01201 is not None else leg)
    _write(
        out / "dim_reference.csv",
        DIM_REFERENCE_HEADER,
        dim_reference if dim_reference is not None else ref,
    )


def _negatives(cup, leg, ref):
    base = (cup, leg, ref)
    # missing rows — last 5 customers dropped
    _write_case("missing_rows", cup00301=cup[:-5], base=base)
    # extra rows — two phantom customers
    extra = cup + [[r[0], f"C9{i:05d}", *r[2:]] for i, r in enumerate(cup[:2])]
    _write_case("extra_rows", cup00301=extra, base=base)
    # duplicate row — same key, second insert
    _write_case("duplicate_rows", cup00301=cup + [cup[0]], base=base)
    # tampered values — legal entity labels swapped (shape-valid, value-diff)
    tampered = [[r[0], "UNKNOWN-ENTITY" if i < 6 else r[1], r[2]] for i, r in enumerate(leg)]
    _write_case("tampered_values", cup01201=tampered, base=base)
    # nullability violation — empty key
    nulls = [list(r) for r in cup]
    nulls[0][1] = ""
    _write_case("null_violation", cup00301=nulls, base=base)
    # type violation — non-numeric country code
    badtype = [list(r) for r in cup]
    badtype[0][3] = "ABC"
    _write_case("bad_type", cup00301=badtype, base=base)
    # referential violation — orphan legal-entity row
    orphan = leg + [["C999999", "PHANTOM CORP", LOAD_DATE]]
    _write_case("fk_orphan", cup01201=orphan, base=base)


def _trailers(cup, leg, ref):
    """Certified totals — derived from the same deterministic data the loader
    transforms, mirroring each hop's Controls sheet."""
    # hop 1 — file row counts plus the workbook's certified aggregates:
    # distinct keys, tax-id nulls, NAICS total, max country code and the
    # per-CUPERS grouped count.
    by_pers: dict = {}
    for r in cup:
        by_pers[r[2].strip()] = by_pers.get(r[2].strip(), 0) + 1
    hop1 = [
        ("cup00301_rows", "", len(cup)),
        ("cup01201_rows", "", len(leg)),
        ("dim_reference_rows", "", len(ref)),
        ("cup00301_distinct_cunbr", "", len({r[1] for r in cup})),
        ("cup00301_taxid_nulls", "", sum(1 for r in cup if not r[8].strip())),
        ("cup00301_naics_sum", "", sum(int(r[4]) for r in cup if r[4].strip())),
        ("cup00301_max_country", "", max(int(r[3]) for r in cup)),
    ] + [("cup00301_by_pers", g, v) for g, v in sorted(by_pers.items())]
    # hop 2 — IL target row counts (LEFT JOINs preserve customer count) +
    # customer_type_cd grouping (casewhen: cupers 'N' -> 'C', else trimmed)
    by_type: dict = {}
    for r in cup:
        ctype = "C" if r[2].strip() == "N" else r[2].strip()
        by_type[ctype] = by_type.get(ctype, 0) + 1
    hop2 = [
        ("dim_customer_rows", "", len(cup)),
        ("dim_address_rows", "", len(cup)),
        ("dim_ref_rows", "", len(ref)),
    ] + [("dim_customer_by_type", g, v) for g, v in sorted(by_type.items())]
    # hop 3 — consumption rows + business-domain grouping.
    # cupers N -> 'C' upstream (spec row 5), so domain = Personal/Commercial/
    # Business only.
    domains = {"Personal": 0, "Commercial": 0, "Business": 0}
    for r in cup:
        ctype = "C" if r[2].strip() == "N" else r[2].strip()
        domains[{"P": "Personal", "C": "Commercial", "B": "Business"}[ctype]] += 1
    hop3 = [("cl_party_rows", "", len(cup))] + [
        ("cl_party_by_domain", g, v) for g, v in sorted(domains.items())
    ]
    for hop, rows in (("hop1", hop1), ("hop2", hop2), ("hop3", hop3)):
        path = TRAILER_DIR / f"phase1_{hop}_trailer.csv"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["name", "group", "value"])
            w.writerows(rows)
        print(f"wrote {path} ({len(rows)} controls)")


def main():
    fake = Faker("en_US")
    Faker.seed(SEED)
    random.seed(SEED)

    if CSV_DIR.exists():
        shutil.rmtree(CSV_DIR)
    if NEG_DIR.exists():
        shutil.rmtree(NEG_DIR)

    cup = _customers(fake)
    leg = _legal_entities(cup)
    ref = DIM_REFERENCE_ROWS

    _write(CSV_DIR / "cup00301.csv", CUP00301_HEADER, cup)
    _write(CSV_DIR / "cup01201.csv", CUP01201_HEADER, leg)
    _write(CSV_DIR / "dim_reference.csv", DIM_REFERENCE_HEADER, ref)
    print(f"wrote {CSV_DIR} — {len(cup)} customers, {len(leg)} entities, {len(ref)} refs")

    _negatives(cup, leg, ref)
    print(f"wrote {NEG_DIR} (7 cases)")
    _trailers(cup, leg, ref)


if __name__ == "__main__":
    main()
