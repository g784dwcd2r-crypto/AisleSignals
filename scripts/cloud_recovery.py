#!/usr/bin/env python3
"""Create, verify and restore-drill private PostgreSQL recovery archives.

Database passwords are accepted only through environment variables and are
passed to PostgreSQL tools through their environment, never process arguments.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlsplit, unquote

import psycopg

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.cloud.database import SCHEMA_CHECKSUM, SCHEMA_VERSION


class RecoveryError(RuntimeError):
    pass


MAX_ARCHIVE_BYTES = 20 * 1024**3
MAX_MANIFEST_BYTES = 64 * 1024


def _connection(url: str):
    try:
        parsed = urlsplit(url)
        database = unquote(parsed.path[1:])
        query = parse_qsl(parsed.query, strict_parsing=True, max_num_fields=1)
        port = parsed.port or 5432
    except (ValueError, UnicodeError):
        raise RecoveryError("A valid PostgreSQL recovery URL is required.") from None
    if (len(url) > 8192 or any(ord(character) < 32 for character in url)
            or parsed.scheme not in {"postgres", "postgresql"} or not parsed.hostname or not parsed.username
            or not parsed.password or not database or "/" in database or parsed.fragment
            or (query and query not in ([("sslmode", "require")], [("sslmode", "disable")]))
            or (query == [("sslmode", "disable")] and parsed.hostname not in {"localhost", "127.0.0.1", "::1"})
            or not 1 <= port <= 65535):
        raise RecoveryError("A valid PostgreSQL recovery URL is required.")
    return {
        "host": parsed.hostname,
        "port": str(port),
        "user": unquote(parsed.username),
        "database": database,
        "password": unquote(parsed.password or ""),
        "sslmode": query[0][1] if query else "require",
        "url": url,
    }


def _tool_environment(connection: dict[str, str]) -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment.pop("RECOVERY_DATABASE_URL", None)
    environment.update({"PGPASSWORD": connection["password"], "PGSSLMODE": connection["sslmode"]})
    return environment


def _target_args(connection: dict[str, str]) -> list[str]:
    return ["--host", connection["host"], "--port", connection["port"], "--username", connection["user"], "--dbname", connection["database"]]


def _run(command: list[str], connection: dict[str, str], *, capture: bool = False) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            env=_tool_environment(connection),
            check=True,
            capture_output=capture,
            text=capture,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError):
        raise RecoveryError("PostgreSQL recovery tooling failed; inspect the private operator logs.") from None


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            value.update(block)
    return value.hexdigest()


def backup(database_url: str, output: Path) -> dict:
    connection = _connection(database_url)
    output = output.absolute()
    manifest = output.with_suffix(output.suffix + ".json")
    if (output.exists() or manifest.exists() or output.is_symlink()
            or output.parent.is_symlink() or not output.parent.is_dir()):
        raise RecoveryError("Backup output and manifest must be new files in an existing directory.")
    descriptor, temporary_name = tempfile.mkstemp(prefix=".aislesignals-cloud-", suffix=".dump", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    temporary.chmod(stat.S_IRUSR | stat.S_IWUSR)
    try:
        _run([
            "pg_dump", "--format=custom", "--no-owner", "--no-privileges",
            "--file", str(temporary), *_target_args(connection),
        ], connection)
        if not temporary.stat().st_size:
            raise RecoveryError("PostgreSQL produced an empty recovery archive.")
        listing = _run(["pg_restore", "--list", str(temporary)], connection, capture=True)
        if "aislesignals_control" not in listing.stdout or "schema_version" not in listing.stdout:
            raise RecoveryError("Recovery archive does not contain the AisleSignals schema.")
        metadata = {
            "format": "aislesignals-postgresql-recovery",
            "schema_version": SCHEMA_VERSION,
            "schema_checksum": SCHEMA_CHECKSUM,
            "bytes": temporary.stat().st_size,
            "sha256": _digest(temporary),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if metadata["bytes"] > MAX_ARCHIVE_BYTES:
            raise RecoveryError("PostgreSQL recovery archive exceeds the 20 GiB operator limit.")
        temporary.replace(output)
        descriptor, manifest_name = tempfile.mkstemp(prefix=".aislesignals-cloud-manifest-", dir=output.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as target:
                target.write(json.dumps(metadata, sort_keys=True) + "\n")
                target.flush()
                os.fsync(target.fileno())
            Path(manifest_name).chmod(stat.S_IRUSR | stat.S_IWUSR)
            Path(manifest_name).replace(manifest)
        except BaseException:
            Path(manifest_name).unlink(missing_ok=True)
            raise
        return metadata
    except BaseException:
        temporary.unlink(missing_ok=True)
        output.unlink(missing_ok=True)
        manifest.unlink(missing_ok=True)
        raise


def verify(archive: Path) -> dict:
    archive = archive.absolute()
    manifest_path = archive.with_suffix(archive.suffix + ".json")
    if archive.is_symlink() or manifest_path.is_symlink() or not archive.is_file() or not manifest_path.is_file():
        raise RecoveryError("Recovery archive or manifest is missing or unsafe.")
    if archive.stat().st_size > MAX_ARCHIVE_BYTES or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise RecoveryError("Recovery archive or manifest exceeds its operator limit.")
    try:
        metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise RecoveryError("Recovery manifest is invalid.") from None
    required = {"format", "schema_version", "schema_checksum", "bytes", "sha256", "created_at"}
    if (set(metadata) != required or metadata["format"] != "aislesignals-postgresql-recovery"
            or type(metadata["bytes"]) is not int or metadata["bytes"] < 1
            or not isinstance(metadata["sha256"], str) or len(metadata["sha256"]) != 64
            or not isinstance(metadata["created_at"], str)
            or metadata["schema_version"] != SCHEMA_VERSION or metadata["schema_checksum"] != SCHEMA_CHECKSUM):
        raise RecoveryError("Recovery manifest is incompatible.")
    if metadata["bytes"] != archive.stat().st_size or metadata["sha256"] != _digest(archive):
        raise RecoveryError("Recovery archive integrity verification failed.")
    # pg_restore needs no database connection to validate and enumerate a custom archive.
    try:
        environment = os.environ.copy()
        environment.pop("DATABASE_URL", None)
        environment.pop("RECOVERY_DATABASE_URL", None)
        result = subprocess.run(["pg_restore", "--list", str(archive)], env=environment,
                                check=True, capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.SubprocessError):
        raise RecoveryError("Recovery archive structure verification failed.") from None
    if "aislesignals_control" not in result.stdout or "schema_version" not in result.stdout:
        raise RecoveryError("Recovery archive does not contain the AisleSignals schema.")
    return metadata


def restore_drill(source_url: str, target_url: str, archive: Path) -> dict:
    verify(archive)
    source, target = _connection(source_url), _connection(target_url)
    try:
        with psycopg.connect(source["url"], sslmode=source["sslmode"], connect_timeout=5) as source_db, psycopg.connect(target["url"], sslmode=target["sslmode"], connect_timeout=5) as target_db:
            source_identity = source_db.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()").fetchone()
            target_identity = target_db.execute("SELECT current_database(), inet_server_addr()::text, inet_server_port()").fetchone()
            if source_identity == target_identity:
                raise RecoveryError("A restore drill cannot target the source database.")
            occupied = target_db.execute("""SELECT EXISTS (
                SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%'
                AND c.relkind IN ('r','p','v','m','S','f'))""").fetchone()[0]
            if occupied:
                raise RecoveryError("Restore drill target must be an empty isolated database.")
    except RecoveryError:
        raise
    except psycopg.Error:
        raise RecoveryError("Recovery database identity check failed.") from None
    _run([
        "pg_restore", "--exit-on-error", "--single-transaction", "--no-owner", "--no-privileges",
        *_target_args(target), str(archive.absolute()),
    ], target)
    try:
        with psycopg.connect(target["url"], sslmode=target["sslmode"], connect_timeout=5) as restored:
            row = restored.execute("SELECT version, checksum FROM aislesignals_control.schema_version WHERE singleton=true").fetchone()
            if row != (SCHEMA_VERSION, SCHEMA_CHECKSUM):
                raise RecoveryError("Restored schema version does not match this release.")
    except RecoveryError:
        raise
    except psycopg.Error:
        raise RecoveryError("Restored database verification failed.") from None
    return {"restored": True, "schema_version": SCHEMA_VERSION, "schema_checksum": SCHEMA_CHECKSUM}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Private AisleSignals PostgreSQL backup and restore drill.")
    commands = parser.add_subparsers(dest="action", required=True)
    create = commands.add_parser("backup")
    create.add_argument("--output", required=True, type=Path)
    check = commands.add_parser("verify")
    check.add_argument("--archive", required=True, type=Path)
    restore = commands.add_parser("restore-drill")
    restore.add_argument("--archive", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.action == "backup":
            result = backup(os.environ.get("DATABASE_URL", ""), args.output)
        elif args.action == "verify":
            result = verify(args.archive)
        else:
            result = restore_drill(os.environ.get("DATABASE_URL", ""), os.environ.get("RECOVERY_DATABASE_URL", ""), args.archive)
        print(json.dumps(result, sort_keys=True))
        return 0
    except (RecoveryError, KeyboardInterrupt):
        print("Cloud recovery operation failed. Check private operator logs, database access and the isolated target.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
