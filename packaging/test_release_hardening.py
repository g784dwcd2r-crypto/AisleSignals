"""Production release identity, reproducibility and signing gates."""

import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from scripts.desktop_update import verify_envelope
from scripts.package_bundle import deterministic_archive
from scripts.release_feed import FeedError, _private_key, build_release, sign_release
from scripts.release_artifact_index import ArtifactIndexError, build_index
from scripts.release_identity import ReleaseIdentityError, load_identity, validate_repository_versions
from scripts.release_preflight import PreflightError, exact_source, private_update_key, signing


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


def committed_repository(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "release@example.invalid"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Release test"], cwd=root, check=True)
    (root / "source.txt").write_text("reviewed\n")
    subprocess.run(["git", "add", "source.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "reviewed source"], cwd=root, check=True)
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
    return root, sha


def test_exact_source_requires_explicit_reviewed_sha(tmp_path):
    root, sha = committed_repository(tmp_path)
    with pytest.raises(PreflightError, match="reviewed exact release commit"):
        exact_source(root, environment={})
    with pytest.raises(PreflightError, match="does not match"):
        exact_source(root, environment={"AISLESIGNALS_RELEASE_SHA": "a" * 40})
    assert exact_source(root, environment={"AISLESIGNALS_RELEASE_SHA": sha}) == sha


def test_exact_source_refuses_dirty_reviewed_checkout(tmp_path):
    root, sha = committed_repository(tmp_path)
    (root / "source.txt").write_text("changed\n")
    with pytest.raises(PreflightError, match="clean exact Git commit"):
        exact_source(root, environment={"AISLESIGNALS_RELEASE_SHA": sha})


def test_apple_team_identifier_is_validated_before_signing():
    environment = {
        "AISLESIGNALS_APPLE_DEVELOPER_ID": "Developer ID Application: Test",
        "AISLESIGNALS_APPLE_TEAM_ID": "not-a-team",
        "AISLESIGNALS_APPLE_NOTARY_PROFILE": "notary-profile",
    }
    with pytest.raises(PreflightError, match="team identifier"):
        signing("Darwin", environment=environment, which=lambda name: f"/tools/{name}")


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
    private_update_key(path)


def test_signing_preflight_refuses_non_ed25519_key_bytes(tmp_path):
    path = tmp_path / "update-key.pem"
    path.write_text("not a signing key\n")
    path.chmod(0o600)
    with pytest.raises(PreflightError, match="Ed25519 PEM"):
        private_update_key(path)


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


def test_macos_signer_preserves_input_and_checks_signed_team():
    script = Path("scripts/sign_notarize_macos.sh").read_text()
    assert 'cp -R "$app" "$release_app"' in script
    assert 'find "$release_app/Contents"' in script
    assert 'codesign --force --deep --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$release_app"' in script
    assert 'grep -Fxq "TeamIdentifier=$AISLESIGNALS_APPLE_TEAM_ID"' in script
    assert 'mv "$release_dmg" "$dmg"' in script
    assert 'codesign --force --deep --options runtime --timestamp --sign "$AISLESIGNALS_APPLE_DEVELOPER_ID" "$app"' not in script


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires the macOS release shell path")
def test_macos_signer_executes_against_copy_and_leaves_input_unchanged(tmp_path):
    app = tmp_path / "Reviewed.app"
    binary = app / "Contents/MacOS/AisleSignals"
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    before = hashlib.sha256(binary.read_bytes()).hexdigest()
    key = tmp_path / "update.pem"
    key.write_bytes(Ed25519PrivateKey.generate().private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key.chmod(0o600)
    tools = tmp_path / "tools"
    tools.mkdir()
    log = tmp_path / "codesign.log"
    helpers = {
        "codesign": """#!/bin/sh
if [ "$1" = "-dv" ]; then echo 'TeamIdentifier=TESTTEAM01' >&2; exit 0; fi
printf '%s\\n' "$*" >> "$AISLESIGNALS_TEST_CODESIGN_LOG"
""",
        "xcrun": "#!/bin/sh\nexit 0\n",
        "spctl": "#!/bin/sh\nexit 0\n",
        "hdiutil": """#!/bin/sh
for argument in "$@"; do destination=$argument; done
: > "$destination"
""",
    }
    for name, content in helpers.items():
        helper = tools / name
        helper.write_text(content)
        helper.chmod(0o755)
    environment = os.environ.copy()
    environment.update({
        "PATH": str(tools) + os.pathsep + environment["PATH"],
        "AISLESIGNALS_RELEASE_SHA": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
        "AISLESIGNALS_APPLE_DEVELOPER_ID": "Developer ID Application: Synthetic",
        "AISLESIGNALS_APPLE_TEAM_ID": "TESTTEAM01",
        "AISLESIGNALS_APPLE_NOTARY_PROFILE": "synthetic-notary-profile",
        "AISLESIGNALS_TEST_CODESIGN_LOG": str(log),
        "AISLESIGNALS_RELEASE_PYTHON": sys.executable,
    })
    destination = tmp_path / "AisleSignals.dmg"
    completed = subprocess.run(["bash", "scripts/sign_notarize_macos.sh", str(app), str(destination), str(key)],
                               env=environment, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert destination.is_file()
    assert hashlib.sha256(binary.read_bytes()).hexdigest() == before
    assert str(app) not in log.read_text()


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
