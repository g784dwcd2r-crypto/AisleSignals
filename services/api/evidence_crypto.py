"""Private local sampled-media storage. Metadata remains in SQLite.

AES-GCM protects media confidentiality and binds ciphertext to its branch, job,
and frame. The OS account that owns the separate key can decrypt it; this is not
protection from a compromised signed-in laptop or a full database/key theft.
"""

import hashlib
import json
import os
import threading
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ASJPEG1\x00"
OVERHEAD = len(MAGIC) + 12 + 16


class EvidenceError(ValueError):
    pass


def key_path(db):
    return Path(str(db) + ".evidence-key")


def private_open(path):
    """Exclusive creation prevents overwriting or following an existing link."""
    return os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb")


class PilotDatabaseLock:
    """Single pilot process; backup refuses while the API owns this OS lock."""

    def __init__(self, db):
        self._mutex = threading.Lock()
        path = Path(str(db) + ".pilot-lock")
        if path.is_symlink():
            raise EvidenceError("The pilot lock must not be a symbolic link.")
        self.file = path.open("a+b")
        os.chmod(path, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                self.file.seek(0, os.SEEK_END)
                if self.file.tell() == 0:
                    self.file.write(b"\x00")
                    self.file.flush()
                self.file.seek(0)
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise EvidenceError("Stop the pilot service before backup or opening this database in another process.") from None

    def close(self):
        with self._mutex:
            if not self.file.closed:
                if os.name == "nt":
                    import msvcrt
                    self.file.seek(0)
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                self.file.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class EvidenceCipher:
    def __init__(self, db, *, create=False):
        path = key_path(db)
        if path.is_symlink():
            raise EvidenceError("The evidence key must not be a symbolic link.")
        if not path.exists() and create:
            with private_open(path) as target:
                target.write(AESGCM.generate_key(bit_length=256))
        if not path.is_file() or path.stat().st_size != 32:
            raise EvidenceError("The private evidence key is missing or invalid. Restore the matching backup; do not replace the key.")
        os.chmod(path, 0o600)
        self.cipher = AESGCM(path.read_bytes())

    @staticmethod
    def aad(item, index):
        return json.dumps(["aislesignals-sampled-frame-v1", item["organisation_id"],
                           item["site_id"], item["id"], index], separators=(",", ":")).encode()

    def encrypt(self, data, item, index):
        nonce = os.urandom(12)
        return MAGIC + nonce + self.cipher.encrypt(nonce, data, self.aad(item, index))

    def decrypt(self, data, item, index):
        if not data.startswith(MAGIC) or len(data) < OVERHEAD:
            raise EvidenceError("Sampled evidence is not authenticated encrypted media.")
        try:
            return self.cipher.decrypt(data[len(MAGIC):len(MAGIC)+12],
                                       data[len(MAGIC)+12:], self.aad(item, index))
        except InvalidTag:
            raise EvidenceError("Sampled evidence failed authentication.") from None


def read_frame(root, item, index, cipher, max_bytes):
    from uuid import UUID
    item_id = item["id"]
    if str(UUID(item_id)) != item_id or not 0 <= index < len(item["frames"]):
        raise EvidenceError("Invalid sampled evidence reference.")
    directory = root / item_id
    path = directory / f"{index}.jpg"
    if directory.is_symlink() or path.is_symlink() or path.stat().st_size > max_bytes + OVERHEAD:
        raise EvidenceError("Sampled evidence is unavailable.")
    with path.open("rb") as source:
        data = source.read(max_bytes + OVERHEAD + 1)
    if len(data) > max_bytes + OVERHEAD:
        raise EvidenceError("Sampled evidence exceeds the storage bound.")
    if cipher:
        data = cipher.decrypt(data, item, index)
    frame = item["frames"][index]
    if len(data) != frame["bytes"] or len(data) > max_bytes or hashlib.sha256(data).hexdigest() != frame["sha256"]:
        raise EvidenceError("Sampled evidence failed its integrity check.")
    return data
