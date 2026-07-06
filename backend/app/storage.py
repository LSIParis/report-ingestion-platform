from __future__ import annotations

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from app.config import Settings


class ObjectStore:
    """Wrapper S3/MinIO. Une seule interface pour tout le pipeline."""

    def __init__(self, client, default_bucket: str):
        self._c = client
        self._default_bucket = default_bucket

    @classmethod
    def from_settings(cls, settings: Settings) -> "ObjectStore":
        client = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint,
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region,
            config=Config(signature_version="s3v4"),
        )
        store = cls(client, settings.s3_bucket)
        store._ensure_bucket(settings.s3_bucket)
        return store

    # ---- écriture ----
    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._c.put_object(Bucket=self._default_bucket, Key=key, Body=data,
                           ContentType=content_type)

    # ---- lecture ----
    def get_default(self, key: str) -> bytes:
        return self.get(self._default_bucket, key)

    def get(self, bucket: str, key: str) -> bytes:
        return self._c.get_object(Bucket=bucket, Key=key)["Body"].read()

    # ---- URL signée ----
    def presign_get(self, key: str, expires_s: int = 300) -> str:
        return self._c.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._default_bucket, "Key": key},
            ExpiresIn=expires_s,
        )

    def _ensure_bucket(self, bucket: str) -> None:
        try:
            self._c.head_bucket(Bucket=bucket)
        except ClientError:
            try:
                self._c.create_bucket(Bucket=bucket)
            except ClientError:
                pass  # course avec le service createbuckets : idempotent
