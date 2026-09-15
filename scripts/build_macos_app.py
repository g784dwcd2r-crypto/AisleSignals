#!/usr/bin/env python3
"""Build the unsigned production .app which the signing gate later seals."""

import argparse
from pathlib import Path

try:
    from package_macos_dmg import build_app, require_macos, select_sdk
    from release_identity import load_identity, validate_repository_versions
except ModuleNotFoundError:
    from scripts.package_macos_dmg import build_app, require_macos, select_sdk
    from scripts.release_identity import load_identity, validate_repository_versions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--cloud-origin", required=True)
    parser.add_argument("--sdk", type=Path)
    args = parser.parse_args()
    require_macos()
    identity = load_identity()
    validate_repository_versions(identity)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    result = build_app(args.bundle.resolve(), args.output_dir.resolve(), identity.version,
                       sdk=select_sdk(args.sdk), cloud=args.cloud_origin, production=True)
    print(result)


if __name__ == "__main__":
    main()
