"""Fail-closed ownership watchdog for the macOS desktop wrapper."""

from __future__ import annotations

import os
import threading


class DesktopOwnerError(ValueError):
    pass


def validate_owner(owner_pid: int | None) -> int | None:
    if owner_pid is None:
        return None
    if (os.name == "nt" or type(owner_pid) is not int or owner_pid <= 1
            or owner_pid != os.getppid()):
        raise DesktopOwnerError("The desktop owner process is unavailable.")
    return owner_pid


def owner_present(owner_pid: int) -> bool:
    # Once a POSIX child is orphaned its PPID changes; PID reuse cannot make it
    # a child of the original desktop process again.
    return os.getppid() == owner_pid


def start_watchdog(owner_pid: int | None, stop_event: threading.Event, *, interval: float = .25):
    owner_pid = validate_owner(owner_pid)
    if owner_pid is None:
        return None
    if not isinstance(stop_event, threading.Event) or type(interval) not in (int, float) or not .05 <= interval <= 5:
        raise DesktopOwnerError("The desktop owner watchdog is invalid.")

    def watch():
        while not stop_event.wait(interval):
            if not owner_present(owner_pid):
                stop_event.set()
                return

    thread = threading.Thread(target=watch, name="aislesignals-desktop-owner", daemon=True)
    thread.start()
    return thread
