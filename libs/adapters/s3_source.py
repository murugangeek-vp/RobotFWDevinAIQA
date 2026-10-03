"""PROD-02: S3 source adapter.

Contract `source:` block with `type: s3` (connection fields may come from the
environment's `source_connection:` block instead — see MigrationLibrary):
    bucket        bucket name (required)
    key           exact object key — or —
    prefix        prefix; every object under it is concatenated (latest versions)
    suffix        prefix mode only: read keys ending with this (e.g. ".csv")
    format        csv | json | jsonl                (default csv)
    version_id    pin a specific object version     (key mode only)
    expected_bucket_owner  12-digit AWS account id; S3 rejects requests if the
                  bucket is owned by anyone else (protects against bucket takeover)
    max_objects   prefix mode upper bound            (default 10000)
    endpoint_url  optional override — moto/minio/LocalStack. Must be HTTPS unless
                  it points at a loopback address (local stubs).
    region_name                                   (default us-east-1)
    encoding / delimiter — csv parsing options

Fail closed: an empty prefix, parts with differing headers, or more objects than
`max_objects` raise instead of returning partial data. `lineage()` returns the
exact objects read (key, VersionId, ETag, size, LastModified) for the audit trail.

Credentials never appear in the contract: boto3 uses the standard AWS chain
(env vars, ~/.aws, SSO, instance/task role). Grant s3:GetObject,
s3:GetObjectVersion and s3:ListBucket only — read-only by design.
"""

from urllib.parse import urlparse

import boto3
import pandas as pd

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source

_LOOPBACK = {"127.0.0.1", "localhost", "::1"}


def _check_endpoint(endpoint_url: "str | None") -> None:
    if not endpoint_url:
        return
    parsed = urlparse(endpoint_url)
    if parsed.scheme == "https" and parsed.hostname:
        return
    if parsed.scheme == "http" and parsed.hostname in _LOOPBACK:
        return
    raise ValueError("s3 endpoint_url must use https (plain http is allowed only for loopback)")


@register_source("s3")
class S3Source(SourceAdapter):
    def __init__(
        self,
        bucket: str,
        key: "str | None" = None,
        prefix: "str | None" = None,
        suffix: "str | None" = None,
        format: str = "csv",
        version_id: "str | None" = None,
        expected_bucket_owner: "str | None" = None,
        max_objects: int = 10000,
        endpoint_url: "str | None" = None,
        region_name: str = "us-east-1",
        encoding: str = "utf-8",
        delimiter: str = ",",
    ):
        if not (key or prefix):
            raise ValueError("s3 source requires `key` or `prefix`")
        if key and prefix:
            raise ValueError("s3 source takes `key` or `prefix`, not both")
        if version_id and not key:
            raise ValueError("s3 `version_id` requires `key`")
        if expected_bucket_owner is not None and not (
            str(expected_bucket_owner).isdigit() and len(str(expected_bucket_owner)) == 12
        ):
            raise ValueError("s3 expected_bucket_owner must be a 12-digit AWS account id")
        if isinstance(max_objects, bool) or int(max_objects) < 1:
            raise ValueError("s3 max_objects must be a positive integer")
        _check_endpoint(endpoint_url)
        self.bucket = bucket
        self.format = format
        self.encoding = encoding
        self.delimiter = delimiter
        self._owner = (
            {"ExpectedBucketOwner": str(expected_bucket_owner)} if expected_bucket_owner else {}
        )
        self._lineage: list = []
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url or None, region_name=region_name
        )
        try:
            if key:
                frames = [self._read_object(key, version_id)]
            else:
                keys = self._list(prefix or "", suffix, int(max_objects))
                if not keys:
                    raise ValueError(f"s3 prefix {prefix!r} matched no objects")
                frames = [self._read_object(k) for k in keys]
            self._check_part_headers(frames)
            self._df = pd.concat(frames, ignore_index=True)
        except Exception:
            self._client.close()
            raise

    def _list(self, prefix: str, suffix: "str | None", max_objects: int) -> list:
        """Paginated, sorted listing; skips folder markers; bounded."""
        keys: list = []
        token = None
        while True:
            kw = {"Bucket": self.bucket, "Prefix": prefix, **self._owner}
            if token:
                kw["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kw)
            for obj in resp.get("Contents", []):
                k = obj["Key"]
                if k.endswith("/") or (suffix and not k.endswith(suffix)):
                    continue
                keys.append(k)
                if len(keys) > max_objects:
                    raise ValueError(f"s3 prefix {prefix!r} exceeds max_objects={max_objects}")
            if not resp.get("IsTruncated"):
                break
            token = resp["NextContinuationToken"]
        return sorted(keys)

    def _read_object(self, key: str, version_id=None):
        kw = {"Bucket": self.bucket, "Key": key, **self._owner}
        if version_id:
            kw["VersionId"] = str(version_id)
        resp = self._client.get_object(**kw)
        modified = resp.get("LastModified")
        self._lineage.append(
            {
                "bucket": self.bucket,
                "key": key,
                "version_id": resp.get("VersionId"),
                "etag": (resp.get("ETag") or "").strip('"') or None,
                "size": resp.get("ContentLength"),
                "last_modified": modified.isoformat() if modified else None,
            }
        )
        body = resp["Body"]  # streamed, not buffered
        if self.format == "csv":
            return pd.read_csv(
                body,
                dtype=str,
                keep_default_na=False,
                encoding=self.encoding,
                sep=self.delimiter,
            )
        if self.format == "jsonl":
            df = pd.read_json(body, lines=True)
        elif self.format == "json":
            df = pd.read_json(body)
        else:
            raise ValueError(f"unknown s3 format {self.format!r}")
        # Match CsvSource dtype=str semantics for downstream rules/transforms.
        return df.map(lambda v: None if pd.isna(v) else str(v))

    def _check_part_headers(self, frames: list) -> None:
        first = list(frames[0].columns)
        for i, frame in enumerate(frames[1:], start=1):
            if list(frame.columns) != first:
                raise ValueError(
                    f"s3 part {self._lineage[i]['key']!r} header differs from "
                    f"{self._lineage[0]['key']!r}"
                )

    def lineage(self) -> list:
        """Objects read, in read order: bucket/key/version_id/etag/size/last_modified."""
        return [dict(item) for item in self._lineage]

    def read_batch(self, **kwargs):
        return self._df

    def row_count(self) -> int:
        return len(self._df)

    def schema(self) -> list:
        return list(self._df.columns)

    def close(self):
        self._client.close()
        self._df = None
