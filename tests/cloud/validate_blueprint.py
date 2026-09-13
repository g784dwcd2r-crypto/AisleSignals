"""Offline structure validation against a separately downloaded official schema.

Usage: python tests/cloud/validate_blueprint.py /path/to/render.schema.json
This script performs no API calls, deployment or account validation.
"""

import json
from pathlib import Path
import sys

import jsonschema
import yaml


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python tests/cloud/validate_blueprint.py OFFICIAL_RENDER_SCHEMA.json", file=sys.stderr)
        return 2
    schema = json.loads(Path(sys.argv[1]).read_text())
    if schema.get("$id") != "https://render.com/schema/render.yaml.json":
        print("Expected the official Render Blueprint schema.", file=sys.stderr)
        return 2
    manifest_path = Path(__file__).resolve().parents[2] / "deployment/render-staging.yaml"
    jsonschema.Draft202012Validator.check_schema(schema)
    errors = list(jsonschema.Draft202012Validator(schema).iter_errors(yaml.safe_load(manifest_path.read_text())))
    if errors:
        for error in errors:
            print(f"{'/'.join(str(v) for v in error.path)}: {error.message}", file=sys.stderr)
        return 1
    print("Render Blueprint structure is valid. This does not deploy or verify an account, branch or region capacity.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
