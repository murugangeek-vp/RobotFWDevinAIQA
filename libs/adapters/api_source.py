"""PROD-03: authenticated REST API source adapter.

Contract `source:` block with `type: api`:
    url          endpoint to GET
    page_param   query param for the page number     (default "page")
    size_param   query param for the page size       (default "page_size")
    page_size    rows per page                       (default 100)
    data_path    dotted key path to the rows array   (default "rows")
    token_env    env var holding the bearer token    (secret never in the contract)
    auth_header  header name                          (default "Authorization")
    auth_scheme  token scheme                         (default "Bearer")
    headers      optional static header dict
    timeout / max_retries / backoff — resilience knobs

Pagination: page numbers until a page returns fewer than `page_size` rows.
Retries: 429 honors Retry-After; 5xx and connection errors retry with
exponential backoff. Values are normalized to strings to match CsvSource
semantics (dtype=str) so downstream rules/transforms behave identically.
"""

import contextlib
import os
import time

import pandas as pd
import requests

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source


@register_source("api")
class ApiSource(SourceAdapter):
    def __init__(
        self,
        url: str,
        page_param: str = "page",
        size_param: str = "page_size",
        page_size: int = 100,
        data_path: str = "rows",
        token_env: "str | None" = None,
        auth_header: str = "Authorization",
        auth_scheme: str = "Bearer",
        headers: "dict | None" = None,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff: float = 1.0,
    ):
        # Defensive coercion — config may arrive as strings from Robot args.
        self.url = url
        self.page_param = page_param
        self.size_param = size_param
        self.page_size = int(page_size)
        self.data_path = data_path
        self.timeout = float(timeout)
        self.max_retries = int(max_retries)
        self.backoff = float(backoff)

        self._session = requests.Session()
        hdrs = dict(headers or {})
        if token_env:
            token = os.environ.get(token_env)
            if not token:
                raise RuntimeError(f"api source requires env var {token_env} for auth — not set")
            hdrs[auth_header] = f"{auth_scheme} {token}"
        self._session.headers.update(hdrs)
        self._df = self._fetch_all()

    def _get(self, params: dict):
        """GET with retry/backoff; 429 honors Retry-After."""
        last_err: "Exception | None" = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._session.get(self.url, params=params, timeout=self.timeout)
            except requests.RequestException as e:
                last_err = e
                retry_after = None
            else:
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_err = RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    retry_after = resp.headers.get("Retry-After")
                else:
                    resp.raise_for_status()
                    return resp
            if attempt < self.max_retries:
                delay = self.backoff * (2**attempt)
                if retry_after:
                    with contextlib.suppress(ValueError):
                        delay = max(delay, float(retry_after))
                time.sleep(delay)
        raise RuntimeError(
            f"GET {self.url} failed after {self.max_retries + 1} attempts: {last_err}"
        )

    def _rows(self, payload) -> list:
        for part in self.data_path.split("."):
            payload = payload[part]
        return payload

    def _fetch_all(self):
        rows = []
        page = 1
        while True:
            resp = self._get({self.page_param: page, self.size_param: self.page_size})
            chunk = self._rows(resp.json())
            rows.extend(chunk)
            if len(chunk) < self.page_size:
                break
            page += 1
        df = pd.DataFrame(rows)
        # Match CsvSource dtype=str semantics; keep nulls as None.
        return df.map(lambda v: None if pd.isna(v) else str(v))

    def read_batch(self, **kwargs):
        return self._df

    def row_count(self) -> int:
        return len(self._df)

    def schema(self) -> list:
        return list(self._df.columns)

    def close(self):
        self._session.close()
        self._df = None
