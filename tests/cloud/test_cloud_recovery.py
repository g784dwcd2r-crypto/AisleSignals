import json
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

from scripts import cloud_recovery
from services.cloud.database import SCHEMA_CHECKSUM, SCHEMA_VERSION


def manifest(archive: Path) -> dict:
    return {
        "format": "aislesignals-postgresql-recovery",
        "schema_version": SCHEMA_VERSION,
        "schema_checksum": SCHEMA_CHECKSUM,
        "bytes": archive.stat().st_size,
        "sha256": cloud_recovery._digest(archive),
        "created_at": "2026-09-15T12:00:00+00:00",
    }


def test_verify_authenticates_manifest_archive_and_postgres_structure(tmp_path, monkeypatch):
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"synthetic custom archive")
    expected = manifest(archive)
    archive.with_suffix(".dump.json").write_text(json.dumps(expected))
    run = Mock(return_value=Mock(stdout="SCHEMA aislesignals_control TABLE schema_version"))
    monkeypatch.setattr(cloud_recovery.subprocess, "run", run)
    assert cloud_recovery.verify(archive) == expected
    archive.write_bytes(b"tampered")
    with pytest.raises(cloud_recovery.RecoveryError, match="integrity"):
        cloud_recovery.verify(archive)


def test_verify_rejects_extra_or_oversized_manifest_fields(tmp_path, monkeypatch):
    archive = tmp_path / "backup.dump"
    archive.write_bytes(b"synthetic")
    data = manifest(archive)
    data["secret"] = "must-not-be-accepted"
    archive.with_suffix(".dump.json").write_text(json.dumps(data))
    with pytest.raises(cloud_recovery.RecoveryError, match="incompatible"):
        cloud_recovery.verify(archive)


def test_backup_failure_removes_every_partial_file(tmp_path, monkeypatch):
    output = tmp_path / "backup.dump"
    monkeypatch.setattr(cloud_recovery, "_run", Mock(side_effect=cloud_recovery.RecoveryError("failed")))
    with pytest.raises(cloud_recovery.RecoveryError):
        cloud_recovery.backup("postgresql://user:password@db.synthetic.invalid/source", output)
    assert not output.exists() and not output.with_suffix(".dump.json").exists()
    assert not list(tmp_path.glob(".aislesignals-cloud-*"))


def test_recovery_urls_are_strict_and_secrets_leave_command_arguments(monkeypatch):
    for value in (
        "postgresql://user@db.invalid/name",
        "postgresql://user:password@db.invalid/name/other",
        "postgresql://user:password@db.invalid/name?service=private",
        "postgresql://user:password@db.invalid:bad/name",
    ):
        with pytest.raises(cloud_recovery.RecoveryError):
            cloud_recovery._connection(value)
    connection = cloud_recovery._connection(
        "postgresql://user:private-password@db.invalid/name?sslmode=require"
    )
    assert "private-password" not in " ".join(cloud_recovery._target_args(connection))
    environment = cloud_recovery._tool_environment(connection)
    assert environment["PGPASSWORD"] == "private-password"
    assert "DATABASE_URL" not in environment and "RECOVERY_DATABASE_URL" not in environment


def test_restore_drill_refuses_source_database_and_nonempty_target(tmp_path, monkeypatch):
    archive = tmp_path / "backup.dump"
    monkeypatch.setattr(cloud_recovery, "verify", Mock(return_value={}))
    source = "postgresql://user:password@source.invalid/app"
    target = "postgresql://user:password@target.invalid/app"
    same = ("app", "10.0.0.1", 5432)
    connections = []
    for _ in range(2):
        connection = MagicMock()
        connection.__enter__.return_value.execute.return_value.fetchone.return_value = same
        connections.append(connection)
    monkeypatch.setattr(cloud_recovery.psycopg, "connect", Mock(side_effect=connections))
    restore = Mock()
    monkeypatch.setattr(cloud_recovery, "_run", restore)
    with pytest.raises(cloud_recovery.RecoveryError, match="cannot target the source"):
        cloud_recovery.restore_drill(source, target, archive)
    restore.assert_not_called()
