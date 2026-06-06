"""File storage abstraction for multi-node deployment.

Provides a uniform API for reading and writing files, switching between
local disk (single-node) and MinIO/S3 (multi-node) based on environment
variables.

Usage::

    from hermes_cli.storage import get_storage

    storage = get_storage()
    storage.write("sessions/sessions.json", json_data)
    data = storage.read("sessions/sessions.json")
    storage.delete("sessions/sessions.json")
"""

from __future__ import annotations

import io
import logging
import os
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Module-level singleton
_storage: FileStorage | None = None
_storage_initialized: bool = False


def get_storage() -> FileStorage:
    """Return the configured FileStorage singleton.

    Reads ``HERMES_S3_ENDPOINT`` etc. on first call; caches thereafter.
    """
    global _storage, _storage_initialized
    if not _storage_initialized:
        _storage = _create_storage()
        _storage_initialized = True
    assert _storage is not None
    return _storage


def setup_storage(
    *,
    endpoint: str | None = None,
    access_key: str | None = None,
    secret_key: str | None = None,
    bucket: str | None = None,
    prefix: str = "",
    local_root: Path | None = None,
) -> FileStorage:
    """Explicitly configure storage (e.g. from tests)."""
    global _storage, _storage_initialized
    if endpoint:
        _storage = S3Storage(
            endpoint=endpoint,
            access_key=access_key or "",
            secret_key=secret_key or "",
            bucket=bucket or "hermes",
            prefix=prefix,
        )
    else:
        _storage = LocalStorage(root=local_root)
    _storage_initialized = True
    return _storage


def _create_storage() -> FileStorage:
    endpoint = os.environ.get("HERMES_S3_ENDPOINT", "").strip()
    if endpoint:
        return S3Storage(
            endpoint=endpoint,
            access_key=os.environ.get("HERMES_S3_ACCESS_KEY", ""),
            secret_key=os.environ.get("HERMES_S3_SECRET_KEY", ""),
            bucket=os.environ.get("HERMES_S3_BUCKET", "hermes"),
            prefix=os.environ.get("HERMES_S3_PREFIX", "").strip(),
        )
    # Default: local disk under ~/.hermes/
    from hermes_constants import get_hermes_home
    return LocalStorage(root=get_hermes_home())


# ── Abstract base ──────────────────────────────────────────────────────────────


class FileStorage:
    """Abstract file storage: local disk, S3, MinIO, etc."""

    def read(self, path: str) -> bytes:
        raise NotImplementedError

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        return self.read(path).decode(encoding)

    def write(self, path: str, data: bytes | str) -> None:
        raise NotImplementedError

    def delete(self, path: str) -> None:
        raise NotImplementedError

    def exists(self, path: str) -> bool:
        raise NotImplementedError

    def list(self, prefix: str = "") -> list[str]:
        raise NotImplementedError


# ── Local storage ──────────────────────────────────────────────────────────────


class LocalStorage(FileStorage):
    """Files stored on the local filesystem under *root*."""

    def __init__(self, root: Path | None = None):
        from hermes_constants import get_hermes_home
        self.root = Path(root) if root else get_hermes_home()

    def _full_path(self, path: str) -> Path:
        return self.root / path

    def read(self, path: str) -> bytes:
        full = self._full_path(path)
        try:
            return full.read_bytes()
        except FileNotFoundError:
            raise FileNotFoundError(f"File not found: {full}")

    def write(self, path: str, data: bytes | str) -> None:
        full = self._full_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(data, str):
            full.write_text(data)
        else:
            full.write_bytes(data)

    def delete(self, path: str) -> None:
        full = self._full_path(path)
        full.unlink(missing_ok=True)

    def exists(self, path: str) -> bool:
        return self._full_path(path).exists()

    def list(self, prefix: str = "") -> list[str]:
        base = self._full_path(prefix)
        if not base.exists():
            return []
        rel_root = self.root
        results = []
        for p in base.rglob("*") if base.is_dir() else ([base] if base.exists() else []):
            if p.is_file():
                results.append(str(p.relative_to(rel_root)))
        return results


# ── S3 / MinIO storage ─────────────────────────────────────────────────────────


class S3Storage(FileStorage):
    """Files stored in S3-compatible object storage (AWS S3, MinIO, etc.)."""

    def __init__(
        self,
        *,
        endpoint: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        prefix: str = "",
    ):
        self.endpoint = endpoint
        self.access_key = access_key
        self.secret_key = secret_key
        self.bucket = bucket
        self.prefix = prefix.rstrip("/") + "/" if prefix else ""
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                from minio import Minio
                self._client = Minio(
                    endpoint=self.endpoint.replace("http://", "").replace("https://", ""),
                    access_key=self.access_key,
                    secret_key=self.secret_key,
                    secure=self.endpoint.startswith("https://"),
                )
                # Ensure bucket exists
                if not self._client.bucket_exists(self.bucket):
                    self._client.make_bucket(self.bucket)
            except ImportError:
                raise ImportError(
                    "minio is required for S3 storage. "
                    "Install it with: pip install minio"
                )
        return self._client

    def _key(self, path: str) -> str:
        return self.prefix + path

    def read(self, path: str) -> bytes:
        try:
            response = self.client.get_object(self.bucket, self._key(path))
            return response.read()
        except Exception as e:
            raise FileNotFoundError(f"S3 key not found: {self._key(path)}") from e

    def write(self, path: str, data: bytes | str) -> None:
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.client.put_object(
            self.bucket,
            self._key(path),
            io.BytesIO(data),
            length=len(data),
        )

    def delete(self, path: str) -> None:
        try:
            self.client.remove_object(self.bucket, self._key(path))
        except Exception:
            pass  # Best-effort delete

    def exists(self, path: str) -> bool:
        try:
            self.client.stat_object(self.bucket, self._key(path))
            return True
        except Exception:
            return False

    def list(self, prefix: str = "") -> list[str]:
        full_prefix = self._key(prefix)
        objects = self.client.list_objects(self.bucket, prefix=full_prefix)
        offset = len(self.prefix)
        return [obj.object_name[offset:] for obj in objects]
