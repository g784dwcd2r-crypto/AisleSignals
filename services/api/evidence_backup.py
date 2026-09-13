"""Offline, passphrase-encrypted local recovery archives, never public exports."""

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import zipfile
from pathlib import Path
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .evidence_crypto import EvidenceCipher, EvidenceError, PilotDatabaseLock, key_path, private_open, read_frame

MAGIC = b"AISLEBACKUP1\x00"
HEADER_SIZE = len(MAGIC) + 16 + 12
MAX_ARCHIVE = 2 * 1024**3
MAX_DATABASE = 64 * 1024**2
MAX_FRAME = 350 * 1024
MAX_ENTRIES = 4000
CHUNK = 1024 * 1024


def derive(passphrase, salt):
    if not isinstance(passphrase, str) or not 14 <= len(passphrase) <= 256:
        raise EvidenceError("Use a backup passphrase of 14 to 256 characters.")
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode())


def encrypt_archive(source, destination, passphrase):
    salt, nonce = os.urandom(16), os.urandom(12)
    header = MAGIC + salt + nonce
    encryptor = Cipher(algorithms.AES(derive(passphrase, salt)), modes.GCM(nonce)).encryptor()
    encryptor.authenticate_additional_data(header)
    if source.stat().st_size > MAX_ARCHIVE:
        raise EvidenceError("The recovery archive exceeds the 2 GiB limit.")
    created = False
    try:
        with private_open(destination) as output, source.open("rb") as reader:
            created = True
            output.write(header)
            while block := reader.read(CHUNK):
                output.write(encryptor.update(block))
            output.write(encryptor.finalize())
            output.write(encryptor.tag)
            output.flush()
            os.fsync(output.fileno())
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise


def decrypt_archive(source, destination, passphrase):
    size = source.stat().st_size
    if source.is_symlink() or not HEADER_SIZE + 16 < size <= MAX_ARCHIVE + HEADER_SIZE + 16:
        raise EvidenceError("The recovery archive size or file type is invalid.")
    created = False
    try:
        with source.open("rb") as reader, private_open(destination) as output:
            created = True
            header = reader.read(HEADER_SIZE)
            if not header.startswith(MAGIC):
                raise EvidenceError("Unsupported recovery archive format.")
            salt, nonce = header[len(MAGIC):len(MAGIC)+16], header[-12:]
            reader.seek(-16, os.SEEK_END)
            tag = reader.read(16)
            reader.seek(HEADER_SIZE)
            decryptor = Cipher(algorithms.AES(derive(passphrase, salt)), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = size - HEADER_SIZE - 16
            while remaining:
                block = reader.read(min(CHUNK, remaining))
                if not block:
                    raise EvidenceError("The recovery archive is truncated.")
                output.write(decryptor.update(block))
                remaining -= len(block)
            output.write(decryptor.finalize())
    except InvalidTag:
        if created:
            destination.unlink(missing_ok=True)
        raise EvidenceError("Recovery authentication failed: wrong passphrase or damaged archive.") from None
    except BaseException:
        if created:
            destination.unlink(missing_ok=True)
        raise
    # No archive entry or database is inspected before GCM final authentication.


def connect(db):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA trusted_schema=OFF")
    return conn


def verify_database(conn, root, cipher):
    if conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
        raise EvidenceError("Recovery database failed its integrity check.")
    if conn.execute("PRAGMA user_version").fetchone()[0] != 2:
        raise EvidenceError("Unsupported recovery database schema.")
    mode = conn.execute("SELECT value FROM runtime_settings WHERE key='mode'").fetchone()
    if not mode or mode[0] != "pilot":
        raise EvidenceError("Recovery archives support protected pilot workspaces only.")
    if conn.execute("PRAGMA foreign_key_check").fetchone():
        raise EvidenceError("Recovery database contains broken account references.")
    sites = {(r["organisation_id"], r["site_id"]) for r in conn.execute("SELECT * FROM entities WHERE kind='site' AND id=site_id")}
    for row in conn.execute("SELECT * FROM memberships"):
        if (row["organisation_id"], row["site_id"]) not in sites:
            raise EvidenceError("Recovery database contains an invalid branch membership.")
        user = conn.execute("SELECT organisation_id FROM users WHERE id=?", (row["user_id"],)).fetchone()
        if not user or user[0] != row["organisation_id"]:
            raise EvidenceError("Recovery membership belongs to a different organisation.")
    files = []
    for row in conn.execute("SELECT * FROM entities WHERE kind='interaction'"):
        item = json.loads(row["body"])
        if (str(UUID(item["id"])) != item["id"] or item["id"] != row["id"] or
            (item["organisation_id"], item["site_id"]) != (row["organisation_id"], row["site_id"]) or
            (row["organisation_id"], row["site_id"]) not in sites or
            not isinstance(item["frames"], list) or len(item["frames"]) > 6):
            raise EvidenceError("Recovery sampled evidence has an invalid branch scope.")
        actor = conn.execute("SELECT organisation_id FROM users WHERE id=?", (item["actor_id"],)).fetchone()
        if not actor or actor[0] != item["organisation_id"]:
            raise EvidenceError("Recovery sampled evidence has an invalid actor scope.")
        for index in range(len(item["frames"])):
            read_frame(root, item, index, cipher, MAX_FRAME)
            files.append((f"evidence/{item['id']}/{index}.jpg", root / item["id"] / f"{index}.jpg"))
    return files


def create_backup(db, output, passphrase):
    db, output = Path(db).absolute(), Path(output).absolute()
    if db.is_symlink() or not db.is_file() or db.stat().st_size > MAX_DATABASE:
        raise EvidenceError("Select an existing pilot database under 64 MiB.")
    if output.exists() or output.is_symlink() or not output.parent.is_dir():
        raise EvidenceError("Choose a new backup filename in an existing directory.")
    with PilotDatabaseLock(db), tempfile.TemporaryDirectory(prefix=".aisle-backup-", dir=output.parent) as temporary:
        stage = Path(temporary)
        snapshot = stage / "database.sqlite"
        with private_open(snapshot):
            pass
        # The OS lock rejects an active API. BEGIN IMMEDIATE also serialises
        # provisioning tools. A second read connection takes a consistent copy
        # while this connection prevents changes to DB-referenced media.
        lock = connect(db)
        try:
            lock.execute("BEGIN IMMEDIATE")
            source, target = connect(db), connect(snapshot)
            try:
                source.backup(target)
            finally:
                source.close()
                target.close()
            if snapshot.stat().st_size > MAX_DATABASE:
                raise EvidenceError("The recovery database exceeds the 64 MiB limit.")
            cipher = EvidenceCipher(db)
            root = Path(str(db) + ".interaction-evidence")
            if root.is_symlink():
                raise EvidenceError("The evidence directory must not be a symbolic link.")
            copied = connect(snapshot)
            try:
                files = verify_database(copied, root, cipher)
            finally:
                copied.close()
            entries = [("database.sqlite", snapshot), ("evidence.key", key_path(db)), *files]
            if len(entries) + 1 > MAX_ENTRIES or sum(path.stat().st_size for _, path in entries) > MAX_ARCHIVE - CHUNK:
                raise EvidenceError("The recovery archive exceeds its bounded capacity.")
            manifest = {"schema_version": 1, "format": "aislesignals-offline-recovery", "files": {}}
            archive = stage / "recovery.zip"
            with private_open(archive) as file, zipfile.ZipFile(file, "w", compression=zipfile.ZIP_STORED) as zipped:
                for name, path in entries:
                    # All entries have strict size bounds; never copy arbitrary paths.
                    data = path.read_bytes()
                    manifest["files"][name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                    zipped.writestr(name, data)
                zipped.writestr("manifest.json", json.dumps(manifest, sort_keys=True))
        finally:
            lock.rollback()
            lock.close()
        encrypt_archive(archive, output, passphrase)
    return {"created": True, "encrypted": True, "media_frames": len(files), "sessions_revoked_on_restore": True}


def unpack_verified(archive, stage):
    with zipfile.ZipFile(archive) as zipped:
        entries = zipped.infolist()
        names = [entry.filename for entry in entries]
        if len(entries) > MAX_ENTRIES or len(set(names)) != len(names) or "manifest.json" not in names:
            raise EvidenceError("Invalid or duplicated recovery archive entries.")
        total = 0
        for entry in entries:
            name = entry.filename
            allowed = name in {"database.sqlite", "evidence.key", "manifest.json"} or re.fullmatch(r"evidence/[0-9a-f-]{36}/[0-5]\.jpg", name)
            limit = MAX_DATABASE if name == "database.sqlite" else CHUNK if name == "manifest.json" else 32 if name == "evidence.key" else MAX_FRAME + 64
            total += entry.file_size
            if (not allowed or entry.compress_type != zipfile.ZIP_STORED or
                entry.file_size > limit or total > MAX_ARCHIVE or
                (entry.external_attr >> 16) & 0o170000 == 0o120000):
                raise EvidenceError("Unsafe or oversized recovery archive entry.")
        manifest = json.loads(zipped.read("manifest.json"))
        if manifest.get("schema_version") != 1 or manifest.get("format") != "aislesignals-offline-recovery":
            raise EvidenceError("Unsupported recovery manifest.")
        if set(manifest.get("files", {})) != set(names) - {"manifest.json"} or not {"database.sqlite", "evidence.key"} <= set(names):
            raise EvidenceError("Recovery manifest does not match its files.")
        for name, expected in manifest["files"].items():
            data = zipped.read(name)
            if len(data) != expected["bytes"] or hashlib.sha256(data).hexdigest() != expected["sha256"]:
                raise EvidenceError("Recovery file failed its integrity check.")
            path = stage / name
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with private_open(path) as target:
                target.write(data)


def restore_backup(archive, target_dir, passphrase):
    archive, target_dir = Path(archive).absolute(), Path(target_dir).absolute()
    if target_dir.exists() or target_dir.is_symlink() or not target_dir.parent.is_dir():
        raise EvidenceError("Restore requires a new, absent target directory in an existing parent. Existing data is never overwritten.")
    with tempfile.TemporaryDirectory(prefix=".aisle-restore-", dir=target_dir.parent) as temporary:
        stage = Path(temporary)
        plain = stage / "authenticated.zip"
        decrypt_archive(archive, plain, passphrase)
        recovered = stage / "workspace"
        recovered.mkdir(mode=0o700)
        unpack_verified(plain, recovered)
        db = recovered / "aislesignals.db"
        (recovered / "database.sqlite").rename(db)
        (recovered / "evidence.key").rename(key_path(db))
        root = Path(str(db) + ".interaction-evidence")
        if (recovered / "evidence").exists():
            (recovered / "evidence").rename(root)
        else:
            root.mkdir(mode=0o700)
        conn = connect(db)
        try:
            expected = verify_database(conn, root, EvidenceCipher(db))
            actual = {str(path.relative_to(root)) for path in root.glob("*/*")}
            if actual != {name.removeprefix("evidence/") for name, _ in expected}:
                raise EvidenceError("Recovery archive includes unreferenced sampled evidence.")
            from .interactions import expired
            # Restored cookies and in-flight model jobs must not regain authority.
            conn.execute("DELETE FROM session_scopes")
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM runtime_settings WHERE key='first_owner_setup'")
            # An old snapshot may predate staff revocation. Its passwords and
            # memberships must not automatically restore former staff access.
            conn.execute("UPDATE account_security SET enabled=0")
            for row in conn.execute("SELECT * FROM entities WHERE kind='interaction'").fetchall():
                item = json.loads(row["body"])
                if expired(item):
                    if (root / item["id"]).exists():
                        shutil.rmtree(root / item["id"])
                    conn.execute("DELETE FROM entities WHERE id=?", (item["id"],))
                elif item["status"] in {"pending", "running"}:
                    if (root / item["id"]).exists():
                        shutil.rmtree(root / item["id"])
                    item.update(status="cancelled", frames=[], error="Restored job cancelled. Sign in and submit a fresh sample.")
                    conn.execute("UPDATE entities SET body=? WHERE id=?", (json.dumps(item), item["id"]))
            conn.commit()
            conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            conn.close()
        # Reserve destination exclusively, then move files. A failed move removes
        # only the directory this invocation created, never a pre-existing target.
        target_dir.mkdir(mode=0o700)
        try:
            for path in recovered.iterdir():
                path.rename(target_dir / path.name)
        except BaseException:
            shutil.rmtree(target_dir)
            raise
    return {"restored": True, "database_name": "aislesignals.db", "sessions_revoked": True,
            "accounts_disabled": True, "requires_account_access_review": True,
            "monitoring_resumed": False, "requires_sign_in_and_camera_checks": True}
