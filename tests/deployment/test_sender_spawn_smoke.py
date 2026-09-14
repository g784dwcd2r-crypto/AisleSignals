"""Real spawn lifecycle without model, credentials, network or default Store."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import sender_spawn_smoke as smoke


def test_real_sender_spawn_ready_refusal_and_exit_leave_no_state(tmp_path):
    root = Path(__file__).resolve().parents[2]
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("AISLESIGNALS_")}
    env["PYTHONPATH"] = str(root)
    check = subprocess.run([sys.executable, "-c", """
import multiprocessing, sys, threading
from scripts.sender_spawn_smoke import run_spawn_smoke
assert 'services.api.app' not in sys.modules
assert 'services.api.store' not in sys.modules
run_spawn_smoke()
assert not multiprocessing.active_children()
assert not any(t.name == 'aislesignals-request-deadline' for t in threading.enumerate())
assert 'services.api.app' not in sys.modules
assert 'services.api.store' not in sys.modules
print('READY_REFUSED_REAPED')
"""], cwd=tmp_path, env=env, capture_output=True, timeout=20)
    assert check.returncode == 0, check.stderr.decode(errors="replace")
    assert check.stdout == b"READY_REFUSED_REAPED\n" or check.stdout == b"READY_REFUSED_REAPED\r\n"
    assert not check.stderr
    assert list(tmp_path.iterdir()) == []


def test_source_execution_cannot_claim_frozen_acceptance(monkeypatch, capsys):
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setattr(smoke, "run_spawn_smoke", lambda: pytest.fail("Source CLI must not spawn"))
    assert smoke.main() == 1
    assert capsys.readouterr().err == "SENDER_SMOKE_REQUIRES_FROZEN_EXECUTABLE\n"


@pytest.mark.parametrize("ready,stopped,result,code", [
    (False, True, {"ok": False, "code": "NETWORK_UNAVAILABLE", "retry_after": None}, "SENDER_HANDSHAKE_FAILED"),
    (True, False, {"ok": False, "code": "CONTEXT_CHANGED", "retry_after": None}, "SENDER_CHILD_NOT_REAPED"),
    (True, True, {"ok": True, "result": {}}, "SENDER_HANDSHAKE_FAILED"),
])
def test_missing_handshake_unreaped_child_or_unexpected_result_fail_closed(monkeypatch, ready, stopped, result, code):
    class BrokenOwner:
        alive = not stopped

        def perform(self, request, *, authorize, **_):
            assert request == {}, "Smoke must never contain credentials or an outbound payload"
            if ready:
                assert authorize() is False
            return result

        def close(self):
            return stopped

    monkeypatch.setattr(smoke, "OwnedRequest", BrokenOwner)
    with pytest.raises(smoke.SpawnSmokeError, match=code):
        smoke.run_spawn_smoke()


def test_frozen_cli_emits_only_fixed_acceptance_or_failure(monkeypatch, capsys):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(smoke, "run_spawn_smoke", lambda: None)
    assert smoke.main() == 0
    output = capsys.readouterr()
    assert json.loads(output.out) == smoke.RECEIPT
    assert not output.err

    def failure():
        raise RuntimeError("Synthetic private exception detail must not appear")

    monkeypatch.setattr(smoke, "run_spawn_smoke", failure)
    assert smoke.main() == 1
    output = capsys.readouterr()
    assert not output.out
    assert output.err == "SENDER_SPAWN_SMOKE_FAILED\n"
