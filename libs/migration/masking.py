"""MIG-01: PII masking for every value that leaves the engine (logs, reports, failures).

Columns flagged ``pii: true`` in a contract are masked. With ``RECON_MASK_KEY`` set
(>= 32 characters, from a secret store) values become keyed HMAC-SHA256 tokens so the
same value correlates across tables and runs without being reversible. Without a key,
values are fully redacted. Unkeyed hashes are never emitted: account numbers and tax
ids are low-entropy and a plain hash is trivially brute-forced.
"""

import hashlib
import hmac
import os

MASK_KEY_ENV = "RECON_MASK_KEY"
REDACTED = "***REDACTED***"
_MIN_KEY_LEN = 32


def _is_empty(v) -> bool:
    if v is None:
        return True
    try:
        return bool(v != v)  # NaN
    except (TypeError, ValueError):
        return False


class Masker:
    def __init__(self, pii_columns, key: "str | None" = None):
        if key is not None and len(key) < _MIN_KEY_LEN:
            raise ValueError(f"{MASK_KEY_ENV} must be at least {_MIN_KEY_LEN} characters")
        self.pii_columns = frozenset(pii_columns)
        self._key = key.encode("utf-8") if key else None

    @classmethod
    def for_contract(cls, contract, key: "str | None" = None) -> "Masker":
        """PII set covers target names and their mapped source names."""
        names = set()
        for col in contract.columns:
            if col.get("pii"):
                names.add(col["name"])
                if col.get("source_name"):
                    names.add(col["source_name"])
        return cls(names, key if key is not None else (os.environ.get(MASK_KEY_ENV) or None))

    @property
    def keyed(self) -> bool:
        return self._key is not None

    def token(self, v):
        if _is_empty(v):
            return None
        if self._key is None:
            return REDACTED
        digest = hmac.new(self._key, str(v).strip().encode("utf-8"), hashlib.sha256)
        return "hmac:" + digest.hexdigest()[:20]

    def value(self, column: str, v):
        """Mask `v` when `column` is PII; otherwise return its string form."""
        if column in self.pii_columns:
            return self.token(v)
        return None if _is_empty(v) else str(v)

    def key(self, key_columns: list, k):
        """Mask a reconciliation key (scalar for single keys, tuple for composite)."""
        parts = k if isinstance(k, tuple) else (k,)
        masked = tuple(self.value(c, p) for c, p in zip(key_columns, parts, strict=True))
        return masked[0] if len(masked) == 1 else masked

    def values(self, column: str, items) -> list:
        return [self.value(column, v) for v in items]
