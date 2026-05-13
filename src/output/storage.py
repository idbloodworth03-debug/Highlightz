"""
Pluggable storage backend: local filesystem, AWS S3, or Google Cloud Storage.
All backends return a public/presigned URL for the uploaded clip.
"""

import asyncio
import shutil
from abc import ABC, abstractmethod
from pathlib import Path

import structlog

from config.settings import settings

log = structlog.get_logger(__name__)


class StorageBackend(ABC):
    @abstractmethod
    async def upload(self, local_path: Path, clip_id: str) -> str:
        ...


class LocalStorage(StorageBackend):
    def __init__(self) -> None:
        self._root = Path(settings.local_storage_path) / "clips"
        self._root.mkdir(parents=True, exist_ok=True)

    async def upload(self, local_path: Path, clip_id: str) -> str:
        dest = self._root / f"{clip_id}.mp4"
        shutil.copy2(local_path, dest)
        log.info("stored_local", clip_id=clip_id, path=str(dest))
        return str(dest)


class S3Storage(StorageBackend):
    def __init__(self) -> None:
        import boto3
        self._s3 = boto3.client(
            "s3",
            aws_access_key_id=settings.aws_access_key_id,
            aws_secret_access_key=settings.aws_secret_access_key,
            region_name=settings.aws_region,
        )
        self._bucket = settings.s3_bucket

    async def upload(self, local_path: Path, clip_id: str) -> str:
        key = f"clips/{clip_id}.mp4"
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            lambda: self._s3.upload_file(
                str(local_path),
                self._bucket,
                key,
                ExtraArgs={"ContentType": "video/mp4"},
            ),
        )
        url = f"https://{self._bucket}.s3.{settings.aws_region}.amazonaws.com/{key}"
        log.info("stored_s3", clip_id=clip_id, url=url)
        return url


class GCSStorage(StorageBackend):
    def __init__(self) -> None:
        from google.cloud import storage as gcs
        self._client = gcs.Client(project=settings.gcs_project_id)
        self._bucket = self._client.bucket(settings.gcs_bucket)

    async def upload(self, local_path: Path, clip_id: str) -> str:
        blob_name = f"clips/{clip_id}.mp4"
        loop = asyncio.get_event_loop()
        blob = self._bucket.blob(blob_name)
        await loop.run_in_executor(
            None,
            lambda: blob.upload_from_filename(str(local_path), content_type="video/mp4"),
        )
        url = f"https://storage.googleapis.com/{settings.gcs_bucket}/{blob_name}"
        log.info("stored_gcs", clip_id=clip_id, url=url)
        return url


def build_storage() -> StorageBackend:
    backends = {"s3": S3Storage, "gcs": GCSStorage, "local": LocalStorage}
    cls = backends.get(settings.storage_backend, LocalStorage)
    return cls()
