"""Immutable evidence and model artifact storage adapters."""

from stock_assistant.platform.object_store.minio_store import MinioObjectStore
from stock_assistant.platform.object_store.s3_store import S3ObjectStore

__all__ = ["MinioObjectStore", "S3ObjectStore"]
