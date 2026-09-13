#!/usr/bin/env python3
"""Check the native optional runtime starts and supports required arguments.

No model weights, camera inputs, or inference are exercised by this check.
"""
import importlib.util
import subprocess
from pathlib import Path


def main():
    source = Path(__file__).resolve().with_name("local-vision.py")
    spec = importlib.util.spec_from_file_location("aislesignals_vision_pins", source)
    pins = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pins)
    server = pins.default_server()
    if server is None or not server.is_file():
        raise SystemExit("Pinned native runtime is missing; run setup --runtime-only.")
    result = subprocess.run([str(server), "--help"], capture_output=True, text=True,
                            errors="replace", timeout=30, cwd=server.parent)
    help_text = result.stdout + result.stderr
    required = ["--mmproj", "--api-key-file", "--no-webui", "--no-agent",
                "--image-max-tokens", "--cors-origins", "--log-disable"]
    if result.returncode != 0 or any(flag not in help_text for flag in required):
        raise SystemExit("Native runtime startup or required argument check failed.")
    print("PASS: native runtime started and advertises required arguments.")
    print("NOT_RUN: model inference, pharmacy footage and client laptop performance.")


if __name__ == "__main__":
    main()
