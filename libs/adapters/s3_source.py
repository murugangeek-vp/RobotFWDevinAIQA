"""PROD-02: S3 source adapter.

Contract `source:` block with `type: s3`:
    bucket        bucket name (required)
    key           exact object key — or —
    prefix        prefix; every object under it is concatenated (latest versions)
    format        csv | json | jsonl                (default csv)
    version_id    pin a specific object version     (key mode only)
    endpoint_url  optional override — moto/minio/LocalStack for non-AWS endpoints
    region_name                                   (default us-east-1)
    encoding / delimiter — csv parsing options

Credentials never appear in the contract: boto3 uses the standard AWS chain
(env vars, ~/.aws, instance/task role). Grant the role s3:GetObject +
s3:ListBucket only — read-only by design.
"""

import boto3
import pandas as pd

from libs.adapters.base import SourceAdapter
from libs.adapters.registry import register_source


@register_source("s3")
class S3Source(SourceAdapter):
    def __init__(
        self,
        bucket: str,
        key: "str | None" = None,
        prefix: "str | None" = None,
        format: str = "csv",
        version_id: "str | None" = None,
        endpoint_url: "str | None" = None,
        region_name: str = "us-east-1",
        encoding: str = "utf-8",
        delimiter: str = ",",
    ):
        if not (key or prefix):
            raise ValueError("s3 source requires `key` or `prefix`")
        self.format = format
        self.encoding = encoding
        self.delimiter = delimiter
        self._client = boto3.client(
            "s3", endpoint_url=endpoint_url or None, region_name=region_name
        )
        if key:
            frames = [self._read_object(bucket, key, version_id)]
        else:
            frames = [
                self._read_object(bucket, obj["Key"]) for obj in self._list(bucket, prefix or "")
            ]
        self._df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def _list(self, bucket: str, prefix: str):
        """Paginated listing of all objects under `prefix`."""
        token = None
        while True:
            kw = {"Bucket": bucket, "Prefix": prefix}
            if token:
                kw["ContinuationToken"] = token
            resp = self._client.list_objects_v2(**kw)
            yield from resp.get("Contents", [])
            if not resp.get("IsTruncated"):
                break
            token = resp["NextContinuationToken"]

    def _read_object(self, bucket: str, key: str, version_id=None):
        kw = {"Bucket": bucket, "Key": key}
        if version_id:
            kw["VersionId"] = str(version_id)
        body = self._client.get_object(**kw)["Body"]  # streamed, not buffered
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

    def read_batch(self, **kwargs):
        return self._df

    def row_count(self) -> int:
        return len(self._df)

    def schema(self) -> list:
        return list(self._df.columns)

    def close(self):
        self._client.close()
        self._df = None
