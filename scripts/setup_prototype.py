"""Reproducible developer setup; no pharmacy hardware or OS configuration changes."""

from pathlib import Path
import shutil
import subprocess
import sys
import venv


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    if sys.version_info < (3, 12):
        print("Python 3.12 or later is required.", file=sys.stderr)
        return 1
    npm = shutil.which("npm")
    node = shutil.which("node")
    if not npm or not node:
        print("Install Node.js 24 LTS first. Standalone prototype bundles do not require Node or Python.", file=sys.stderr)
        return 1
    version = subprocess.check_output([node, "--version"], text=True).strip()
    if int(version.lstrip("v").split(".")[0]) < 24:
        print("Node.js 24 or later is required for the development toolchain.", file=sys.stderr)
        return 1
    env_dir = root / ".venv"
    if not env_dir.exists():
        venv.EnvBuilder(with_pip=True).create(env_dir)
    python = env_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", "-r", str(root / "services/api/requirements.txt")], check=True)
    subprocess.run([npm, "ci"], cwd=root / "apps/web", check=True)
    subprocess.run([npm, "run", "build"], cwd=root / "apps/web", check=True)
    print(f"Ready. Run: {python} scripts/run_prototype.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
