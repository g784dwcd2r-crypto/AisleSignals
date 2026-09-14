"""Build the same-origin console and its pinned, cloud-only Python runtime."""

from pathlib import Path
import shutil
import subprocess
import sys


def main():
    root = Path(__file__).resolve().parents[1]
    npm = shutil.which("npm")
    if not npm:
        print("Node.js/npm is required to build the management interface.", file=sys.stderr)
        return 1
    commands = [
        [sys.executable, "-m", "pip", "install", "-r", "services/cloud/requirements.txt"],
        [npm, "ci", "--prefix", "apps/control"],
        [npm, "--prefix", "apps/control", "run", "build"],
    ]
    try:
        for command in commands:
            subprocess.run(command, cwd=root, check=True)
    except subprocess.CalledProcessError:
        return 1
    return 0 if (root / "apps/control/dist/index.html").is_file() else 1


if __name__ == "__main__":
    raise SystemExit(main())
