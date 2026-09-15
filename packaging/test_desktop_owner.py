"""Desktop parent ownership remains opt-in and fail closed."""

import os
import threading

import pytest

import scripts.desktop_owner as owner


def test_unowned_command_line_launcher_is_unchanged():
    stop = threading.Event()
    assert owner.start_watchdog(None, stop) is None
    assert not stop.is_set()


@pytest.mark.skipif(os.name == "nt", reason="macOS/POSIX desktop ownership")
def test_owner_must_be_the_actual_direct_parent(monkeypatch):
    monkeypatch.setattr(owner.os, "getppid", lambda: 900)
    assert owner.validate_owner(900) == 900
    with pytest.raises(owner.DesktopOwnerError, match="unavailable"):
        owner.validate_owner(901)


@pytest.mark.skipif(os.name == "nt", reason="macOS/POSIX desktop ownership")
def test_owner_change_stops_supervisor(monkeypatch):
    parents = iter([900, 1])
    monkeypatch.setattr(owner.os, "getppid", lambda: next(parents, 1))
    stop = threading.Event()
    thread = owner.start_watchdog(900, stop, interval=.05)
    assert stop.wait(1)
    thread.join(timeout=1)
    assert not thread.is_alive()
