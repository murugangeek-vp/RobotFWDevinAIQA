import pandas as pd

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source

_JOIN_HOWS = ("left", "inner")


def _read(path, encoding, delimiter, header, dtype):
    return pd.read_csv(
        path,
        encoding=encoding,
        sep=delimiter,
        header=0 if header else None,
        dtype=dtype,
        keep_default_na=False,
    )


@register_source("csv")
class CsvSource(SourceAdapter):
    def __init__(
        self,
        path: str,
        encoding: str = "utf-8",
        delimiter: str = ",",
        header: bool = True,
        dtype=str,
        joins: "list | None" = None,
    ):
        self.path = path
        self._df = _read(path, encoding, delimiter, header, dtype)
        for spec in joins or []:
            self._df = self._apply_join(self._df, spec, encoding, delimiter, header, dtype)

    def _apply_join(self, left: pd.DataFrame, spec: dict, encoding, delimiter, header, dtype):
        """Merge one secondary file onto the main frame. Fail closed: duplicate
        join keys, missing key columns, undeclared header drift, column
        collisions, and (with require_match) unmatched rows all raise."""
        path = spec["path"]
        right = _read(path, encoding, spec.get("delimiter", delimiter), header, dtype)
        keys = spec["on"] if isinstance(spec["on"], list) else [spec["on"]]

        want = spec.get("columns")
        if want is not None and list(right.columns) != list(want):
            raise ValueError(
                f"join file {path} header mismatch: expected {want}, got {list(right.columns)}"
            )
        missing_l = [k for k in keys if k not in left.columns]
        missing_r = [k for k in keys if k not in right.columns]
        if missing_l or missing_r:
            raise ValueError(
                f"join on {keys} for {path}: missing in source {missing_l}, "
                f"missing in join file {missing_r}"
            )
        if right.duplicated(subset=keys).any():
            raise ValueError(f"join file {path} has duplicate keys on {keys} — would fan out rows")
        collide = sorted((set(right.columns) - set(keys)) & set(left.columns))
        suffix = spec.get("suffix")
        if collide and not suffix:
            raise ValueError(
                f"join file {path} shares non-key columns {collide} — set a join suffix"
            )
        if suffix:
            right = right.rename(columns={c: f"{c}{suffix}" for c in collide})

        how = spec.get("how", "left")
        if how not in _JOIN_HOWS:
            raise ValueError(f"join {path}: how must be one of {_JOIN_HOWS}, got {how!r}")
        merged = left.merge(right, on=keys, how=how, validate="m:1")

        if spec.get("require_match"):
            join_cols = [c for c in right.columns if c not in keys]
            if join_cols and merged[join_cols].isna().any(axis=1).any():
                n = int(merged[join_cols].isna().any(axis=1).sum())
                raise ValueError(f"{n} source rows have no match in join file {path}")
        return merged

    def read_batch(self, **kwargs):
        return self._df

    def row_count(self) -> int:
        return len(self._df)

    def schema(self) -> list:
        return list(self._df.columns)

    def close(self):
        self._df = None
