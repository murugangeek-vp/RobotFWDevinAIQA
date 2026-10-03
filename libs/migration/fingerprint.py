"""MIG-P2: bucketed row fingerprints for tables above the full-compare bound.

Design (must stay byte-identical on the Python side and every SQL dialect):

- Canonical value: NULL -> '@@RECON_NULL@@'; otherwise the contract-normalized value
  rendered as text (Decimal keeps scale, dates 'YYYY-MM-DD', timestamps with fixed
  six-digit microseconds, booleans 'true'/'false').
- Per-column fingerprint: MD5(canonical) — fixed 32-char hex, so concatenating column
  fingerprints has no separator ambiguity (a value can never spoof a boundary).
- Row fingerprint: int(MD5(concat of column fingerprints)[:15 hex digits], 16)
  (60 bits; SUM fits in NUMERIC/UNSIGNED aggregates without overflow at bank scale).
- Bucket: int(MD5(concat of key fingerprints)[:8 hex digits], 16) mod B.
  Bucketing is engine-independent MD5 — identical on Postgres, MySQL, Snowflake,
  and Python — so a row's bucket matches on both sides by construction.
- Bucket checksum: (row count, sum of row fingerprints). A changed/inserted/deleted
  row flips its bucket's checksum; we then drill down into only the differing buckets.
"""

import hashlib

from libs.engine.reconcile import _isna, normalize

NULL_SENTINEL = "@@RECON_NULL@@"
_FP_HEX_DIGITS = 15
_BUCKET_HEX_DIGITS = 8


# ----- canonical values (python) ---------------------------------------------


def canonical_value(v, col: dict) -> str:
    """String form of a normalized value — must match canonical_sql() per dialect."""
    if _isna(v):
        return NULL_SENTINEL
    n = normalize(v, col)
    if n is None:
        return NULL_SENTINEL
    if col["type"] == "timestamp":
        return n.strftime("%Y-%m-%d %H:%M:%S.%f")  # fixed microseconds both sides
    if col["type"] == "boolean":
        return "true" if n else "false"
    if col["type"] == "date":
        return n.isoformat()
    return str(n)


def _md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8"), usedforsecurity=False).hexdigest()


def row_fingerprint(values: list) -> int:
    """values = canonical strings in contract column order."""
    return int(_md5("".join(_md5(v) for v in values))[:_FP_HEX_DIGITS], 16)


def key_bucket(key_values: list, buckets: int) -> int:
    return int(_md5("".join(_md5(v) for v in key_values))[:_BUCKET_HEX_DIGITS], 16) % buckets


def source_checksums(expected_df, contract, buckets: int) -> dict:
    """{bucket: (row_count, fingerprint_sum)} over expected target rows.

    Iterates positionally over the contract's column order. Duplicate or null
    keys raise — a hashed compare must never silently collapse keys.
    """
    keys = contract.keys
    key_df = expected_df[keys]
    if key_df.isna().any().any() or (key_df == "").any().any():
        raise ValueError("source contains null reconciliation keys")
    if key_df.duplicated().any():
        raise ValueError("source contains duplicate reconciliation keys")
    cols = contract.columns
    order = [c["name"] for c in cols]
    key_pos = [order.index(k) for k in keys]
    out: dict = {}
    for row in expected_df[order].itertuples(index=False, name=None):
        canonical = [canonical_value(v, c) for v, c in zip(row, cols, strict=True)]
        bucket = key_bucket([canonical[p] for p in key_pos], buckets)
        count, fp = out.get(bucket, (0, 0))
        out[bucket] = (count + 1, fp + row_fingerprint(canonical))
    return out


# ----- canonical values (SQL) -------------------------------------------------

_COL_EXPR = {
    "postgres": {
        "integer": "CAST({c} AS TEXT)",
        "decimal": "CAST(CAST({c} AS NUMERIC(38,{s})) AS TEXT)",
        "string": "CAST({c} AS TEXT)",
        "date": "TO_CHAR({c}::DATE, 'YYYY-MM-DD')",
        "timestamp": "TO_CHAR({c}, 'YYYY-MM-DD HH24:MI:SS.US')",
        "boolean": "CASE WHEN {c} THEN 'true' ELSE 'false' END",
    },
    "mysql": {
        "integer": "CAST({c} AS CHAR)",
        "decimal": "CAST({c} AS DECIMAL(38,{s}))",
        "string": "CAST({c} AS CHAR)",
        "date": "DATE_FORMAT({c}, '%Y-%m-%d')",
        "timestamp": "DATE_FORMAT({c}, '%Y-%m-%d %H:%i:%s.%f')",
        "boolean": "CASE WHEN {c} THEN 'true' ELSE 'false' END",
    },
    "snowflake": {
        "integer": "TO_VARCHAR({c})",
        "decimal": "TO_VARCHAR(CAST({c} AS NUMBER(38,{s})))",
        "string": "TO_VARCHAR({c})",
        "date": "TO_VARCHAR({c}, 'YYYY-MM-DD')",
        "timestamp": "TO_VARCHAR({c}, 'YYYY-MM-DD HH24:MI:SS.FF6')",
        "boolean": "LOWER(TO_VARCHAR({c}))",
    },
}


def canonical_sql(col: dict, dialect: str) -> str:
    from libs.engine.schema import ident

    try:
        expr = _COL_EXPR[dialect][col["type"]].format(
            c=ident(col["name"], dialect), s=col.get("scale", 2)
        )
    except KeyError as e:
        raise ValueError(f"no canonical SQL for type {col.get('type')!r} on {dialect}") from e
    return f"COALESCE({expr}, '{NULL_SENTINEL}')"


def _hex_to_int(md5_expr: str, digits: int, dialect: str) -> str:
    if dialect == "postgres":
        return f"('x' || substr({md5_expr}, 1, {digits}))::bit({digits * 4})::bigint"
    if dialect == "mysql":
        return f"CAST(CONV(LEFT({md5_expr}, {digits}), 16, 10) AS UNSIGNED)"
    if dialect == "snowflake":
        return f"TO_NUMBER(SUBSTR({md5_expr}, 1, {digits}), '{'X' * digits}')"
    raise ValueError(f"unsupported dialect {dialect!r}")


def _fingerprint_sql(specs: list, dialect: str, digits: int) -> str:
    """int(md5(concat of per-column md5 hex)[:digits]) — order = contract order."""
    per_col = ", ".join(f"MD5({canonical_sql(c, dialect)})" for c in specs)
    return _hex_to_int(f"MD5(CONCAT({per_col}))", digits, dialect)


def bucket_sql(contract, dialect: str, buckets: int) -> str:
    """MOD(bucket_int, B) expression — deterministic across engines."""
    key_specs = [contract.column(k) for k in contract.keys]
    inner = _fingerprint_sql(key_specs, dialect, _BUCKET_HEX_DIGITS)
    return f"MOD({inner}, {int(buckets)})"


def fingerprint_sql(contract, dialect: str) -> str:
    """Row fingerprint expression over all contract columns (contract order)."""
    return _fingerprint_sql(contract.columns, dialect, _FP_HEX_DIGITS)


def bucket_checksum_sql(contract, dialect: str, table_expr: str, buckets: int) -> str:
    """SELECT bucket, COUNT(*), SUM(fp) FROM t GROUP BY bucket — generated only."""
    bucket = bucket_sql(contract, dialect, buckets)
    return (
        f"SELECT {bucket} AS bucket, COUNT(*) AS n, SUM({fingerprint_sql(contract, dialect)}) "
        f"AS fp FROM {table_expr} GROUP BY {bucket}"
    )


def source_rows_in_bucket(expected_df, contract, buckets: int, bucket: int):
    """Expected rows whose key falls in `bucket` — drill-down for a checksum diff."""
    key_pos = [expected_df.columns.get_loc(k) for k in contract.keys]
    key_specs = [contract.column(k) for k in contract.keys]
    rows = []
    for row in expected_df.itertuples(index=False, name=None):
        canon = [canonical_value(row[p], c) for p, c in zip(key_pos, key_specs, strict=True)]
        if key_bucket(canon, buckets) == bucket:
            rows.append(row)
    return expected_df.__class__(rows, columns=expected_df.columns)


def compare_checksums(source: dict, target: dict) -> list:
    """[{bucket, expected, actual}] — buckets that differ in count or fingerprint sum."""
    diffs = []
    for bucket in sorted(source.keys() | target.keys()):
        e, a = source.get(bucket, (0, 0)), target.get(bucket, (0, 0))
        if e != a:
            diffs.append({"bucket": bucket, "expected": e, "actual": a})
    return diffs
