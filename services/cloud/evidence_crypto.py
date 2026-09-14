"""AES-256-GCM envelope encryption with a random per-object DEK."""

import os
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b"AISLEEVID1"


class EvidenceCryptoError(RuntimeError):
    pass


def seal(plaintext: bytes, *, kek: bytes, kek_version: str, aad: bytes) -> bytes:
    if len(kek) != 32 or not isinstance(plaintext, bytes) or not isinstance(aad, bytes):
        raise EvidenceCryptoError("Evidence encryption configuration is invalid.")
    version = kek_version.encode("ascii")
    if not 1 <= len(version) <= 32:
        raise EvidenceCryptoError("Evidence key version is invalid.")
    dek, wrap_nonce, data_nonce = os.urandom(32), os.urandom(12), os.urandom(12)
    wrapped = AESGCM(kek).encrypt(wrap_nonce, dek, b"wrap\x00" + version + b"\x00" + aad)
    ciphertext = AESGCM(dek).encrypt(data_nonce, plaintext, b"data\x00" + version + b"\x00" + aad)
    return MAGIC + struct.pack("!B", len(version)) + version + wrap_nonce + wrapped + data_nonce + ciphertext


def open_envelope(envelope: bytes, *, kek: bytes, expected_version: str, aad: bytes) -> bytes:
    try:
        if len(kek) != 32 or not envelope.startswith(MAGIC):
            raise ValueError
        offset = len(MAGIC)
        length = envelope[offset]
        offset += 1
        version = envelope[offset:offset + length]
        offset += length
        if not 1 <= length <= 32 or version.decode("ascii") != expected_version:
            raise ValueError
        wrap_nonce, wrapped = envelope[offset:offset + 12], envelope[offset + 12:offset + 60]
        offset += 60
        data_nonce, ciphertext = envelope[offset:offset + 12], envelope[offset + 12:]
        if len(wrap_nonce) != 12 or len(wrapped) != 48 or len(data_nonce) != 12 or len(ciphertext) < 16:
            raise ValueError
        dek = AESGCM(kek).decrypt(wrap_nonce, wrapped, b"wrap\x00" + version + b"\x00" + aad)
        return AESGCM(dek).decrypt(data_nonce, ciphertext, b"data\x00" + version + b"\x00" + aad)
    except Exception:
        raise EvidenceCryptoError("Evidence authentication failed.") from None
