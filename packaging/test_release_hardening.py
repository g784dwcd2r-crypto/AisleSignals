"""Production release identity, reproducibility and signing gates."""

import base64
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.desktop_update import verify_envelope
from scripts.package_bundle import deterministic_archive
from scripts.release_feed import FeedError, _private_key, build_release, sign_release
from scripts.release_artifact_index import ArtifactIndexError, build_index
from scripts.release_identity import ReleaseIdentityError, load_identity, validate_repository_versions
from scripts.release_preflight import PreflightError, signing


def test_production_identity_is_single_version_source():
    identity = load_identity()
    validate_repository_versions(identity)
    assert identity.product == identity.update_product == "AisleSignals"
    assert identity.version == "0.1.0"
    assert identity.mac_bundle_identifier == "ie.aislesignals.desktop"
    assert identity.windows_app_id == "DD026687-3AF5-459A-AE9D-187B0482E933"


def test_unknown_identity_fields_and_package_version_drift_fail(tmp_path):
    value = json.loads(Path("packaging/release-identity.json").read_text())
    value["unexpected"] = True
    path = tmp_path / "identity.json"
    path.write_text(json.dumps(value))
    with pytest.raises(ReleaseIdentityError):
        load_identity(path)

    root = tmp_path / "repo"
    (root / "apps/web").mkdir(parents=True)
    (root / "apps/control").mkdir(parents=True)
    for target, version in ((root / "package.json", "0.1.0"),
                            (root / "apps/web/package.json", "0.1.1"),
                            (root / "apps/control/package.json", "0.1.0")):
        target.write_text(json.dumps({"version": version}))
    with pytest.raises(ReleaseIdentityError, match="versions must match"):
        validate_repository_versions(load_identity(), root)


@pytest.mark.parametrize("archive_format,suffix", [("zip", ".zip"), ("gztar", ".tar.gz")])
def test_archive_wrapper_is_byte_reproducible(tmp_path, archive_format, suffix):
    bundle = tmp_path / "AisleSignalsPilot"
    (bundle / "nested").mkdir(parents=True)
    executable = bundle / "AisleSignalsPilot"
    executable.write_bytes(b"stable executable bytes")
    executable.chmod(0o755)
    (bundle / "nested/config.json").write_text('{"stable":true}\n')
    first = deterministic_archive(bundle, tmp_path / ("first" + suffix), archive_format, 1_700_000_000)
    second = deterministic_archive(bundle, tmp_path / ("second" + suffix), archive_format, 1_700_000_000)
    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()


def test_signing_preflight_names_missing_inputs_without_values():
    secret = "must-never-appear"
    with pytest.raises(PreflightError) as failure:
        signing("Darwin", environment={"AISLESIGNALS_APPLE_DEVELOPER_ID": secret}, which=lambda _: None)
    assert secret not in str(failure.value)
    assert "AISLESIGNALS_APPLE_TEAM_ID" in str(failure.value)
    assert "tool:codesign" in str(failure.value)


def test_signing_preflight_accepts_names_and_tools_without_returning_values():
    environment = {
        "AISLESIGNALS_WINDOWS_CERT_SHA1": "A" * 40,
    }
    result = signing("Windows", environment=environment, which=lambda name: f"/tools/{name}")
    assert result["ready"] is True
    assert "A" * 40 not in json.dumps(result)


def test_update_feed_is_deterministic_and_matches_runtime_verifier(tmp_path):
    artifact = tmp_path / "AisleSignals-macOS-arm64-v0.1.0.dmg"
    artifact.write_bytes(b"synthetic signed-installer bytes")
    release = build_release(
        artifact=artifact,
        platform_name="Darwin",
        architecture="arm64",
        version="0.1.0",
        published_at="2026-09-15T12:00:00+00:00",
        source_commit="a" * 40,
        artifact_url="https://updates.aislesignals.ie/" + artifact.name,
    )
    key = Ed25519PrivateKey.generate()
    first = sign_release(release, key)
    assert first == sign_release(release, key)
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    verified = verify_envelope(first, base64.b64encode(raw).decode(), system="Darwin", architecture="arm64")
    assert verified.filename == artifact.name
    assert verified.artifact_sha256 == hashlib.sha256(artifact.read_bytes()).hexdigest()


@pytest.mark.parametrize("url", [
    "http://updates.aislesignals.ie/AisleSignals.dmg",
    "https://updates.aislesignals.ie/AisleSignals.dmg?temporary=token",
    "https://user@updates.aislesignals.ie/AisleSignals.dmg",
])
def test_update_feed_refuses_ambiguous_artifact_urls(tmp_path, url):
    artifact = tmp_path / "AisleSignals.dmg"
    artifact.write_bytes(b"candidate")
    with pytest.raises(FeedError):
        build_release(artifact=artifact, platform_name="Darwin", architecture="arm64", version="0.1.0",
                      published_at="2026-09-15T12:00:00+00:00", source_commit="a" * 40,
                      artifact_url=url)


def test_update_feed_refuses_empty_and_oversized_artifacts(tmp_path):
    artifact = tmp_path / "AisleSignals.dmg"
    for size in (0, 1024 * 1024 * 1024 + 1):
        with artifact.open("wb") as output:
            output.truncate(size)
        with pytest.raises(FeedError):
            build_release(artifact=artifact, platform_name="Darwin", architecture="arm64", version="0.1.0",
                          published_at="2026-09-15T12:00:00+00:00", source_commit="a" * 40,
                          artifact_url="https://updates.aislesignals.ie/AisleSignals.dmg")


def test_update_private_key_must_be_owner_only(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "update-key.pem"
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
                                       serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()))
    path.chmod(0o644)
    with pytest.raises(FeedError, match="owner-only"):
        _private_key(path)
    path.chmod(0o600)
    assert isinstance(_private_key(path), Ed25519PrivateKey)


def test_production_windows_installer_uses_stable_identity():
    script = Path("packaging/windows/AisleSignalsProduction.iss").read_text()
    assert "AppId={{DD026687-3AF5-459A-AE9D-187B0482E933}" in script
    assert "AppName=AisleSignals" in script
    assert "PrivilegesRequired=lowest" in script
    assert 'DestName: "AisleSignals.exe"' in script
    assert "SignedUninstaller=yes" in script and "SignedUninstaller=no" in script
    assert "SignTool=aislesignals" in script
    assert "unsigned-pilot" not in script
    signing_script = Path("scripts/sign_windows_release.ps1").read_text()
    assert "'/DSignedBuild=1'" in signing_script
    assert "signtool verify /pa /all /v $expectedInstaller" in signing_script
    assert "Refusing to replace an existing signed installer" in signing_script


def test_artifact_index_is_order_independent_and_source_bound(tmp_path):
    first = tmp_path / "a.dmg"
    second = tmp_path / "b.json"
    first.write_bytes(b"artifact-a")
    second.write_bytes(b"artifact-b")
    left = build_index([second, first], source_commit="b" * 40, system="Darwin", architecture="arm64")
    right = build_index([first, second], source_commit="b" * 40, system="Darwin", architecture="arm64")
    assert left == right
    value = json.loads(left)
    assert value["source_commit"] == "b" * 40
    assert [item["filename"] for item in value["artifacts"]] == ["a.dmg", "b.json"]


def test_artifact_index_refuses_symlinks_and_duplicate_names(tmp_path):
    target = tmp_path / "real.dmg"
    target.write_bytes(b"content")
    link = tmp_path / "link.dmg"
    link.symlink_to(target)
    with pytest.raises(ArtifactIndexError, match="regular"):
        build_index([link], source_commit="b" * 40, system="Darwin", architecture="arm64")
    with pytest.raises(ArtifactIndexError, match="unique"):
        build_index([target, target], source_commit="b" * 40, system="Darwin", architecture="arm64")
