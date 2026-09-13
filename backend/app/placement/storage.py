"""Private placement-document storage with a replaceable local backend."""
from __future__ import annotations

import hashlib
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile

from .. import config
from .service import PlacementError

KEY_PATTERN = re.compile(r'^[0-9a-f]{32}\.pdf$')


@dataclass(frozen=True)
class StoredDocument:
    storage_key: str
    original_name: str
    content_type: str
    size_bytes: int
    sha256: str


class LocalPlacementDocumentStorage:
    """Local private storage; callers only persist opaque generated keys."""

    def __init__(self, root: Path | None = None, max_bytes: int | None = None):
        self.root = (root or config.PLACEMENT_DOCUMENT_ROOT).resolve()
        self.max_bytes = max_bytes or config.PLACEMENT_DOCUMENT_MAX_BYTES

    def _path(self, key: str) -> Path:
        if not KEY_PATTERN.fullmatch(key or ''):
            raise PlacementError('Invalid document reference', 404)
        path = (self.root / key).resolve()
        if path.parent != self.root:
            raise PlacementError('Invalid document reference', 404)
        return path

    async def save_pdf(self, upload: UploadFile) -> StoredDocument:
        original = (upload.filename or '').strip()
        if not original or len(original) > 255 or Path(original).name != original or '/' in original or '\\' in original:
            raise PlacementError('PDF filename is invalid', 400)
        if Path(original).suffix.lower() != '.pdf':
            raise PlacementError('Job document must have a .pdf extension', 400)
        declared_type = (upload.content_type or '').lower().split(';', 1)[0].strip()
        if declared_type != 'application/pdf':
            raise PlacementError('Job document must declare application/pdf', 400)

        try:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        except OSError as exc:
            raise PlacementError('Private document storage is unavailable', 503) from exc
        key = f'{uuid.uuid4().hex}.pdf'
        destination = self._path(key)
        try:
            fd, temporary_name = tempfile.mkstemp(prefix='.upload-', dir=self.root)
        except OSError as exc:
            raise PlacementError('Private document storage is unavailable', 503) from exc
        size = 0
        digest = hashlib.sha256()
        try:
            with os.fdopen(fd, 'wb') as stream:
                first = await upload.read(5)
                if first != b'%PDF-':
                    raise PlacementError('Job document content is not a PDF', 400)
                stream.write(first); digest.update(first); size = len(first)
                while chunk := await upload.read(64 * 1024):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise PlacementError(f'Job document exceeds the {self.max_bytes // (1024 * 1024)} MB limit', 400)
                    stream.write(chunk); digest.update(chunk)
            os.chmod(temporary_name, 0o600)
            os.replace(temporary_name, destination)
        except OSError as exc:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise PlacementError('Private document storage write failed', 503) from exc
        except Exception:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
            raise
        finally:
            await upload.close()
        return StoredDocument(key, original, 'application/pdf', size, digest.hexdigest())

    def open_path(self, key: str) -> Path:
        path = self._path(key)
        if not path.is_file():
            raise PlacementError('Job document is unavailable', 404)
        return path

    def remove(self, key: str | None) -> None:
        if not key:
            return
        try:
            self._path(key).unlink(missing_ok=True)
        except OSError as exc:
            raise PlacementError('Job document could not be removed from storage', 503) from exc


def placement_document_storage() -> LocalPlacementDocumentStorage:
    return LocalPlacementDocumentStorage()
