"""S3 connector: documents in an S3-compatible bucket (CLAUDE.md section 6, M12c). In the
full profile the bucket is `firm-documents` on the local MinIO (deploy/compose.yaml).

connector_config: {"bucket": "firm-documents", "prefix": "", "endpoint_url":
                   "http://127.0.0.1:9000", "region": "us-east-1",
                   "access_key": "env:NAME", "secret_key": "env:NAME", "defaults": {...}}

- Changes are found by polling (author's decision, M12c): every Markdown object under the
  prefix is listed with its ETag, which changes when the object's content changes.
  Bucket notifications would need MinIO wired to Kafka, more setup for no gain here.
- The effective date defaults to the object's LastModified date.
- Credentials are "env:NAME" references, read from .env when the connector starts.
"""

from pathlib import Path
from typing import Any

from app.connectors.base import ConnectorError, RemoteItem, SnapshotConnector, resolve_secret


class S3Connector(SnapshotConnector):
    def __init__(self, source_id: str, config: dict[str, Any], landing_dir: Path,
                 client: Any = None) -> None:
        super().__init__(source_id, config, landing_dir)
        self.bucket, self.prefix = str(config["bucket"]), str(config.get("prefix", ""))
        self.client = client if client is not None else self._client(config)

    @staticmethod
    def _client(config: dict[str, Any]) -> Any:
        import boto3  # full-profile dependency (requirements-full.txt)
        from botocore.config import Config

        return boto3.client(
            "s3", endpoint_url=config.get("endpoint_url") or None,
            region_name=config.get("region", "us-east-1"),
            aws_access_key_id=resolve_secret(config.get("access_key")),
            aws_secret_access_key=resolve_secret(config.get("secret_key")),
            config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 2}))

    def list_items(self) -> list[RemoteItem]:
        items: list[RemoteItem] = []
        try:
            pages = self.client.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=self.prefix)
            for page in pages:
                for obj in page.get("Contents", []):
                    key = obj["Key"]
                    if key.lower().endswith(self.suffixes):
                        items.append(RemoteItem(key=key, version=str(obj["ETag"]).strip('"'),
                                                modified=obj.get("LastModified")))
        except Exception as exc:  # botocore raises many types; the runner logs and retries
            raise ConnectorError(f"cannot list s3://{self.bucket}/{self.prefix}: {exc}") from None
        return items

    def fetch(self, item: RemoteItem) -> str | None:
        try:
            body = self.client.get_object(Bucket=self.bucket, Key=item.key)["Body"].read()
        except Exception as exc:
            raise ConnectorError(f"cannot read s3://{self.bucket}/{item.key}: {exc}") from None
        return body.decode("utf-8", "replace")
