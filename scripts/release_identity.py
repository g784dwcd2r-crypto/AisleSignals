"""Single, strict identity and version source for desktop release tooling."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]
RUNTIME_ROOT = Path(getattr(sys, "_MEIPASS", ROOT))
IDENTITY_PATH = RUNTIME_ROOT / "packaging" / "release-identity.json"
VERSION = re.compile(r"(?:0|[1-9][0-9]*)(?:\.(?:0|[1-9][0-9]*)){2}")
SHA = re.compile(r"[0-9a-f]{40}")


class ReleaseIdentityError(ValueError):
    pass


@dataclass(frozen=True)
class ReleaseIdentity:
    product: str
    update_product: str
    version: str
    publisher: str
    support_url: str
    mac_bundle_identifier: str
    mac_minimum_version: str
    mac_architectures: tuple[str, ...]
    windows_app_id: str
    windows_minimum_build: str
    windows_architectures: tuple[str, ...]


def _bounded(value, pattern, label):
    if not isinstance(value, str) or not re.fullmatch(pattern, value):
        raise ReleaseIdentityError(f"Invalid desktop release {label}.")
    return value


def load_identity(path: Path = IDENTITY_PATH) -> ReleaseIdentity:
    try:
        raw = path.read_bytes()
        if len(raw) > 8192 or path.is_symlink():
            raise ValueError
        value = json.loads(raw)
        if set(value) != {"schema_version", "product", "update_product", "version", "publisher",
                          "support_url", "macos", "windows"} or value["schema_version"] != 1:
            raise ValueError
        mac, windows = value["macos"], value["windows"]
        if set(mac) != {"bundle_identifier", "minimum_version", "architectures"} or set(windows) != {
                "app_id", "minimum_build", "architectures"}:
            raise ValueError
        identity = ReleaseIdentity(
            _bounded(value["product"], r"[A-Za-z][A-Za-z0-9 ]{1,63}", "product"),
            _bounded(value["update_product"], r"[A-Za-z][A-Za-z0-9]{1,63}", "update product"),
            _bounded(value["version"], VERSION.pattern, "version"),
            _bounded(value["publisher"], r"[A-Za-z][A-Za-z0-9 .'-]{1,63}", "publisher"),
            _bounded(value["support_url"], r"https://[A-Za-z0-9.-]+/[A-Za-z0-9/_-]*", "support URL"),
            _bounded(mac["bundle_identifier"], r"[a-z][a-z0-9]*(?:\.[a-z0-9-]+){2,}", "bundle identifier"),
            _bounded(mac["minimum_version"], r"[0-9]+\.[0-9]+", "macOS minimum"),
            tuple(mac["architectures"]),
            _bounded(windows["app_id"], r"[0-9A-F]{8}(?:-[0-9A-F]{4}){3}-[0-9A-F]{12}", "Windows app id"),
            _bounded(windows["minimum_build"], r"10\.0\.[0-9]+", "Windows minimum"),
            tuple(windows["architectures"]),
        )
        if identity.mac_architectures != ("arm64",) or identity.windows_architectures != ("x86_64",):
            raise ValueError
        return identity
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        raise ReleaseIdentityError("The desktop release identity file is invalid.") from None


def validate_repository_versions(identity: ReleaseIdentity, root: Path = ROOT) -> None:
    paths = [root / "package.json", root / "apps" / "web" / "package.json",
             root / "apps" / "control" / "package.json"]
    versions = []
    for path in paths:
        try:
            versions.append(json.loads(path.read_text(encoding="utf-8"))["version"])
        except (OSError, KeyError, TypeError, json.JSONDecodeError):
            raise ReleaseIdentityError(f"Cannot read the release version from {path.relative_to(root)}.") from None
    if any(version != identity.version for version in versions):
        raise ReleaseIdentityError("Desktop, web and control package versions must match release-identity.json.")
