"""The Windows startup setting is per-user, exact and conflict-safe."""

from pathlib import Path

import pytest

from scripts.desktop_startup import RUN_KEY, VALUE_NAME, StartupError, WindowsStartup, startup_command


class Key:
    def __init__(self, registry):
        self.registry = registry

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class Registry:
    HKEY_CURRENT_USER = object()
    KEY_QUERY_VALUE = 1
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def OpenKey(self, root, path, reserved, access):
        assert root is self.HKEY_CURRENT_USER and path == RUN_KEY and reserved == 0
        if access == self.KEY_QUERY_VALUE and VALUE_NAME not in self.values:
            raise FileNotFoundError
        return Key(self)

    def CreateKeyEx(self, root, path, reserved, access):
        assert root is self.HKEY_CURRENT_USER and path == RUN_KEY and reserved == 0
        assert access == self.KEY_SET_VALUE
        return Key(self)

    def QueryValueEx(self, key, name):
        return self.values[name]

    def SetValueEx(self, key, name, reserved, kind, value):
        self.values[name] = (value, kind)

    def DeleteValue(self, key, name):
        del self.values[name]


def executable(tmp_path: Path) -> Path:
    result = tmp_path / "Aisle Signals" / "AisleSignalsPilot.exe"
    result.parent.mkdir()
    result.write_bytes(b"test")
    return result


def test_enable_status_disable_round_trip(tmp_path):
    target = executable(tmp_path)
    registry = Registry()
    startup = WindowsStartup(registry)
    assert startup.status(target) == "disabled"
    assert startup.enable(target) == "enabled"
    assert registry.values[VALUE_NAME] == (f'"{target.resolve()}" --startup-launch', registry.REG_SZ)
    assert startup.status(target) == "enabled"
    assert startup.disable(target) == "disabled"
    assert startup.status(target) == "disabled"


def test_changed_entry_is_reported_and_never_overwritten_or_removed(tmp_path):
    target = executable(tmp_path)
    registry = Registry()
    registry.values[VALUE_NAME] = ('"C:\\Unexpected\\AisleSignalsPilot.exe"', registry.REG_SZ)
    startup = WindowsStartup(registry)
    assert startup.status(target) == "conflict"
    with pytest.raises(StartupError, match="different"):
        startup.enable(target)
    with pytest.raises(StartupError, match="different"):
        startup.disable(target)
    assert registry.values[VALUE_NAME][0] == '"C:\\Unexpected\\AisleSignalsPilot.exe"'


def test_startup_command_requires_an_existing_regular_file(tmp_path):
    with pytest.raises(StartupError, match="not found"):
        startup_command(tmp_path / "missing.exe")
