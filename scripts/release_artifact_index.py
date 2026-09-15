#!/usr/bin/env python3
"""Write a canonical, source-bound index for already-built desktop artifacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys

try:
    from desktop_update import canonical
    from release_identity import load_identity, validate_repository_versions
    from release_preflight import exact_source
except ModuleNotFoundError:
    from scripts.desktop_update import canonical
    from scripts.release_identity import load_identity, validate_repository_versions
    from scripts.release_preflight import exact_source


class ArtifactIndexError(ValueError):
    pass


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def build_index(artifacts: list[Path], *, source_commit: str, system: str, architecture: str) -> bytes:
    identity = load_identity()
    validate_repository_versions(identity)
    normalized = sorted((path.expanduser().absolute() for path in artifacts), key=lambda path: path.name)
    names = [path.name for path in normalized]
    if not normalized or len(names) != len(set(names)):
        raise ArtifactIndexError("Provide at least one artifact with a unique filename.")
    entries = []
    for path in normalized:
        info = path.lstat()
        if path.is_symlink() or not path.is_file() or info.st_size <= 0:
            raise ArtifactIndexError("Every release artifact must be a nonempty regular file.")
        entries.append({"filename": path.name, "bytes": info.st_size, "sha256": digest(path)})
    return canonical({
        "schema_version": 1,
        "product": identity.product,
        "version": identity.version,
        "source_commit": source_commit,
        "platform": system,
        "architecture": architecture,
        "artifacts": entries,
    }) + b"\n"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--platform", default=platform.system(), choices=("Darwin", "Windows"))
    parser.add_argument("--architecture", default=platform.machine())
    args = parser.parse_args(argv)
    try:
        output = args.output.expanduser().absolute()
        if output.exists() or output.is_symlink() or not output.parent.is_dir():
            raise ArtifactIndexError("The artifact index output must be a new file in an existing directory.")
        payload = build_index(args.artifact, source_commit=exact_source(), system=args.platform,
                              architecture=args.architecture)
        descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(descriptor, "wb") as target:
            target.write(payload)
        print(output)
        return 0
    except (ArtifactIndexError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
