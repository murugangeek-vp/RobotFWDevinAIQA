import pandas as pd

from libs.adapters.base import SourceAdapter


class CsvSource(SourceAdapter):
    def __init__(
        self,
        path: str,
        encoding: str = "utf-8",
        delimiter: str = ",",
        header: bool = True,
        dtype=str,
    ):
        self.path = path
        self._df = pd.read_csv(
            path,
            encoding=encoding,
            sep=delimiter,
            header=0 if header else None,
            dtype=dtype,
            keep_default_na=False,
        )

    def read_batch(self, **kwargs):
        return self._df

    def row_count(self) -> int:
        return len(self._df)

    def schema(self) -> list:
        return list(self._df.columns)

    def close(self):
        self._df = None
