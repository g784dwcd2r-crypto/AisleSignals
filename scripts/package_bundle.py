"""Archive the bundle without losing executable modes or macOS symlinks."""
from pathlib import Path
import platform
import shutil
import sys


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    bundle = root / "dist" / "AisleSignalsPrototype"
    if not bundle.is_dir():
        raise SystemExit("Build the PyInstaller bundle before archiving it.")
    (bundle / "READ-ME.txt").write_text(
        "AisleSignals 0.1 — local synthetic prototype\n\n"
        "Run AisleSignalsPrototype (Mac) or AisleSignalsPrototype.exe (Windows).\n"
        "Keep its terminal open. The browser opens on http://127.0.0.1:8765.\n"
        "Demo login: manager@harbour.demo / AisleDemo!2026\n"
        "Use synthetic data only; no real camera monitoring, paid AI or alarms.\n"
        "This development bundle is not a signed/notarised pharmacy installer.\n"
        "Do not bypass organisation or OS security policies to install it.\n"
        "Close the terminal with Ctrl+C to stop; local demo data is preserved.\n",
        encoding="utf-8",
    )
    filename = root / "dist" / f"AisleSignalsPrototype-{platform.system()}-{platform.machine()}-unsigned"
    archive_format = "zip" if sys.platform == "win32" else "gztar"
    archive = shutil.make_archive(str(filename), archive_format, root_dir=bundle.parent, base_dir=bundle.name)
    print(archive)


if __name__ == "__main__":
    main()
