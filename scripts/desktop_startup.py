"""Per-user startup setting for the packaged Windows desktop launcher.

macOS uses SMAppService.mainAppService in the native application menu.  The
Windows implementation deliberately uses only the current user's Run key: it
does not request elevation, weaken Windows prompts, or start monitoring.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys


VALUE_NAME = "AisleSignalsPilot"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


class StartupError(RuntimeError):
    pass


def startup_command(executable: Path) -> str:
    executable = executable.resolve()
    if not executable.is_file():
        raise StartupError("The installed AisleSignals executable was not found.")
    # Windows CommandLineToArgvW quoting for a path argument with no embedded
    # quote.  A resolved Windows path cannot contain a double quote.
    if '"' in str(executable):
        raise StartupError("The installed AisleSignals path is unsupported.")
    return f'"{executable}" --startup-launch'


class WindowsStartup:
    def __init__(self, registry=None):
        if registry is None:
            if os.name != "nt":
                raise StartupError("Windows startup settings can only be changed on Windows.")
            import winreg
            registry = winreg
        self.registry = registry

    def _read(self) -> str | None:
        try:
            with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER, RUN_KEY, 0,
                                       self.registry.KEY_QUERY_VALUE) as key:
                value, kind = self.registry.QueryValueEx(key, VALUE_NAME)
        except FileNotFoundError:
            return None
        if kind != self.registry.REG_SZ or not isinstance(value, str):
            raise StartupError("An incompatible AisleSignals startup entry already exists.")
        return value

    def status(self, executable: Path) -> str:
        actual = self._read()
        if actual is None:
            return "disabled"
        if actual != startup_command(executable):
            return "conflict"
        return "enabled"

    def enable(self, executable: Path) -> str:
        expected = startup_command(executable)
        actual = self._read()
        if actual not in (None, expected):
            raise StartupError("A different AisleSignals startup entry exists; remove it manually before enabling this installation.")
        with self.registry.CreateKeyEx(self.registry.HKEY_CURRENT_USER, RUN_KEY, 0,
                                       self.registry.KEY_SET_VALUE) as key:
            self.registry.SetValueEx(key, VALUE_NAME, 0, self.registry.REG_SZ, expected)
        return "enabled"

    def disable(self, executable: Path) -> str:
        actual = self._read()
        if actual is None:
            return "disabled"
        if actual != startup_command(executable):
            raise StartupError("A different AisleSignals startup entry exists; it was not removed.")
        with self.registry.OpenKey(self.registry.HKEY_CURRENT_USER, RUN_KEY, 0,
                                   self.registry.KEY_SET_VALUE) as key:
            self.registry.DeleteValue(key, VALUE_NAME)
        return "disabled"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage per-user startup for the installed Windows pilot.")
    parser.add_argument("action", choices=("status", "enable", "disable"))
    parser.add_argument("--executable", type=Path, default=None, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if os.name != "nt":
        print("On macOS, use AisleSignals Pilot > Start at Login in the application menu.", file=sys.stderr)
        return 1
    try:
        if args.executable is None and not getattr(sys, "frozen", False):
            raise StartupError("Startup can be enabled only from the installed AisleSignals application.")
        executable = args.executable or Path(sys.executable)
        manager = WindowsStartup()
        state = getattr(manager, args.action)(executable)
    except StartupError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(state)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
