"""Exercise the frozen sender's real spawn/READY/cancellation path without I/O.

Only the exact internal launcher smoke command calls ``main``. No credentials,
request payload, configuration, Store or application are loaded. The production
authorization callback is reached only after the child sends its valid READY
handshake; refusing there guarantees no request is ever sent to the child.
"""

import json
import sys
import threading
import time

from services.api.cloud_delivery import OwnedRequest


REQUEST_SECONDS = 8
MAX_ELAPSED_SECONDS = REQUEST_SECONDS + 4
RECEIPT = {"check": "frozen-sender-spawn-v1", "ready": True,
           "request_sent": False, "child_reaped": True}


class SpawnSmokeError(RuntimeError):
    """A bounded, non-sensitive smoke failure code."""


def run_spawn_smoke() -> None:
    """Use production spawn and teardown, rejecting before any outbound request."""
    owner = OwnedRequest()
    ready_count = 0

    def refuse_request():
        nonlocal ready_count
        ready_count += 1
        return False

    started = time.monotonic()
    try:
        result = owner.perform({}, timeout=REQUEST_SECONDS, cancel=threading.Event(),
                               authorize=refuse_request, recheck_seconds=.05)
    finally:
        stopped = owner.close()
    if not stopped or owner.alive:
        raise SpawnSmokeError("SENDER_CHILD_NOT_REAPED")
    if time.monotonic() - started > MAX_ELAPSED_SECONDS:
        raise SpawnSmokeError("SENDER_EXIT_DEADLINE")
    if ready_count != 1 or result != {"ok": False, "code": "CONTEXT_CHANGED", "retry_after": None}:
        raise SpawnSmokeError("SENDER_HANDSHAKE_FAILED")


def main() -> int:
    # Source-mode unit tests exercise run_spawn_smoke separately. A packaged
    # acceptance receipt must come from the actual frozen executable.
    if getattr(sys, "frozen", False) is not True:
        print("SENDER_SMOKE_REQUIRES_FROZEN_EXECUTABLE", file=sys.stderr)
        return 1
    try:
        run_spawn_smoke()
    except Exception:
        print("SENDER_SPAWN_SMOKE_FAILED", file=sys.stderr)
        return 1
    print(json.dumps(RECEIPT, sort_keys=True, separators=(",", ":")))
    return 0
