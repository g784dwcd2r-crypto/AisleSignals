"""Private blob storage boundary; PostgreSQL stores metadata only."""

from pathlib import Path
import os
import re
from threading import Lock
from typing import Protocol


MAX_ENVELOPE_BYTES = 8 * 1024 * 1024 + 512
KEY = re.compile(r"evidence/[0-9a-f-]{36}\.bin")


class EvidenceStoreError(RuntimeError):
    pass


class EvidenceObjectMissing(EvidenceStoreError):
    """The requested private object does not exist."""


class EvidenceBlobStore(Protocol):
    def put_if_absent(self, key: str, value: bytes) -> bool: ...
    def get(self, key: str) -> bytes: ...
    def delete(self, key: str) -> None: ...
    def probe(self) -> bool: ...


def _key(key: str) -> str:
    if not KEY.fullmatch(key):
        raise EvidenceStoreError("Invalid evidence object key.")
    return key


class MemoryEvidenceBlobStore:
    def __init__(self):
        self._objects: dict[str, bytes] = {}
        self._lock = Lock()

    def put_if_absent(self, key: str, value: bytes) -> bool:
        _key(key)
        if not isinstance(value, bytes) or len(value) > MAX_ENVELOPE_BYTES:
            raise EvidenceStoreError("Invalid evidence object.")
        with self._lock:
            if key in self._objects:
                return False
            self._objects[key] = value
            return True

    def get(self, key: str) -> bytes:
        _key(key)
        with self._lock:
            try:
                return self._objects[key]
            except KeyError:
                raise EvidenceObjectMissing("Evidence object is unavailable.") from None

    def delete(self, key: str) -> None:
        _key(key)
        with self._lock:
            self._objects.pop(key, None)

    def probe(self) -> bool:
        return True


class FilesystemEvidenceBlobStore:
    def __init__(self, root: Path):
        self.root = root.absolute()
        if not self.root.is_dir() or self.root.is_symlink():
            raise EvidenceStoreError("Evidence store must be an existing private directory.")

    def _path(self, key: str) -> Path:
        return self.root / _key(key)

    def put_if_absent(self, key: str, value: bytes) -> bool:
        if not isinstance(value, bytes) or len(value) > MAX_ENVELOPE_BYTES:
            raise EvidenceStoreError("Invalid evidence object.")
        path = self._path(key)
        path.parent.mkdir(mode=0o700, exist_ok=True)
        if path.parent.is_symlink():
            raise EvidenceStoreError("Evidence store path is unsafe.")
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            return False
        try:
            with os.fdopen(descriptor, "wb") as target:
                target.write(value)
                target.flush()
                os.fsync(target.fileno())
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return True

    def get(self, key: str) -> bytes:
        path = self._path(key)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_ENVELOPE_BYTES:
            raise EvidenceObjectMissing("Evidence object is unavailable.")
        return path.read_bytes()

    def delete(self, key: str) -> None:
        path = self._path(key)
        if path.is_symlink():
            raise EvidenceStoreError("Evidence store path is unsafe.")
        path.unlink(missing_ok=True)

    def probe(self) -> bool:
        return self.root.is_dir() and not self.root.is_symlink()
