"""Native macOS wrapper structure and compile acceptance."""

import json
import os
from pathlib import Path
import platform
import plistlib
import subprocess

import pytest

from scripts.package_macos_dmg import build_app, cloud_origin, select_sdk


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS frameworks")
def test_native_wrapper_compiles_and_declares_private_local_transport(tmp_path):
    source = tmp_path / "AisleSignalsPilot"
    source.mkdir()
    executable = source / "AisleSignalsPilot"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (source / "release-manifest.json").write_text(json.dumps({
        "bundle": "AisleSignalsPilot", "platform": "Darwin"
    }), encoding="utf-8")
    app = build_app(source, tmp_path / "output", "0.1.0", sdk=select_sdk())
    launcher = app / "Contents" / "MacOS" / "AisleSignalsPilot"
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    with (app / "Contents" / "Info.plist").open("rb") as source_file:
        info = plistlib.load(source_file)
    assert info["CFBundleIdentifier"] == "ie.aislesignals.pilot"
    assert info["LSMinimumSystemVersion"] == "13.0"
    assert info["NSAppTransportSecurity"] == {"NSAllowsLocalNetworking": True}
    assert info["AisleSignalsCloudOrigin"] == "https://aislesignals-control-staging.onrender.com/"
    assert "authorised pharmacy operator" in info["NSCameraUsageDescription"]
    # Inspect the compiled native launcher rather than source text. The normal
    # app path must let the guarded pilot launcher decide whether verified
    # model assets are usable; a user can still choose explicit safe mode from
    # command-line maintenance when required.
    compiled_strings = subprocess.check_output(["strings", "-a", launcher], text=True)
    assert "--owner-pid" in compiled_strings
    assert "--casework-only" not in compiled_strings


@pytest.mark.skipif(platform.system() != "Darwin", reason="requires macOS frameworks")
def test_production_wrapper_uses_stable_identity(tmp_path):
    source = tmp_path / "AisleSignalsPilot"
    source.mkdir()
    executable = source / "AisleSignalsPilot"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    (source / "release-manifest.json").write_text(json.dumps({
        "bundle": "AisleSignalsPilot", "platform": "Darwin"
    }), encoding="utf-8")
    app = build_app(source, tmp_path / "output", "0.1.0", sdk=select_sdk(), production=True)
    assert app.name == "AisleSignals.app"
    with (app / "Contents" / "Info.plist").open("rb") as source_file:
        info = plistlib.load(source_file)
    assert info["CFBundleIdentifier"] == "ie.aislesignals.desktop"
    assert info["CFBundleExecutable"] == "AisleSignals"


def test_explicit_sdk_must_be_an_sdk_directory(tmp_path):
    with pytest.raises(SystemExit, match="installed macOS SDK"):
        select_sdk(tmp_path)


@pytest.mark.parametrize("value", ["http://example.test", "https://user@example.test",
                                    "https://example.test/path", "https://example.test/?secret=yes"])
def test_cloud_origin_refuses_non_https_credentials_paths_and_queries(value):
    with pytest.raises(SystemExit, match="path-free HTTPS"):
        cloud_origin(value)


def test_cloud_origin_is_canonical():
    assert cloud_origin("https://CONTROL.EXAMPLE.TEST:443/") == "https://control.example.test/"
