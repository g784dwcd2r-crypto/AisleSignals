"""Native Windows lifecycle regressions; no camera or application DB is used."""

import ctypes
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from scripts import windows_process_job as jobs


def test_windows_structure_layout_is_explicit_for_64_bit_handles():
    if ctypes.sizeof(ctypes.c_void_p) == 8:
        assert ctypes.sizeof(jobs.ExtendedLimits) == 144
        assert ctypes.sizeof(jobs.StartupInfo) == 104
        assert ctypes.sizeof(jobs.StartupInfoEx) == 112
        assert ctypes.sizeof(jobs.ProcessInformation) == 24
        assert ctypes.sizeof(jobs.SecurityAttributes) == 24


native = pytest.mark.skipif(os.name != "nt", reason="Requires Windows kernel Job Objects")


@native
@pytest.mark.parametrize("nested", [False, True])
def test_abrupt_owner_exit_stops_only_its_child_even_inside_existing_job(tmp_path, nested):
    root = Path(__file__).resolve().parents[2]
    child_script = tmp_path / "synthetic_child.py"
    child_script.write_text("import time\nwhile True: time.sleep(1)\n")
    marker = tmp_path / "owned-child.json"
    owner_script = tmp_path / "synthetic_owner.py"
    owner_script.write_text("""
import json, os, sys, time
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from scripts.windows_process_job import WindowsProcessJob
job = WindowsProcessJob()
child = job.spawn([sys.executable, sys.argv[2]], cwd=Path(sys.argv[2]).parent, env=os.environ.copy())
marker = Path(sys.argv[3])
temporary = marker.with_suffix('.pending')
temporary.write_text(json.dumps({'pid':child.pid}))
temporary.replace(marker)
while True: time.sleep(1)
""")
    # This process is deliberately outside the owner's private containment.
    unrelated = subprocess.Popen([sys.executable, str(child_script)])
    outer = jobs.WindowsProcessJob() if nested else None
    command = [sys.executable, str(owner_script), str(root), str(child_script), str(marker)]
    owner = outer.spawn(command, cwd=tmp_path, env=os.environ.copy()) if outer else subprocess.Popen(command)
    child_handle = None
    api = jobs.kernel_api()
    api.OpenProcess.argtypes = [jobs.DWORD, jobs.BOOL, jobs.DWORD]
    api.OpenProcess.restype = jobs.HANDLE
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            assert owner.poll() is None
            time.sleep(.05)
        assert marker.exists(), "Owned process never completed atomic job assignment"
        pid = json.loads(marker.read_text())["pid"]
        # Hold a process handle for an exact death check, never identify a later
        # unrelated process by a potentially reused PID.
        child_handle = jobs.checked(api.OpenProcess(0x00100000 | 0x1000 | 1, False, pid))
        assert api.WaitForSingleObject(child_handle, 0) == jobs.WAIT_TIMEOUT
        owner.terminate()  # Windows TerminateProcess bypasses finally/atexit.
        owner.wait(timeout=10)
        assert api.WaitForSingleObject(child_handle, 10000) == jobs.WAIT_OBJECT_0
        assert unrelated.poll() is None
    finally:
        if owner.poll() is None:
            owner.terminate()
            owner.wait(timeout=10)
        if child_handle:
            if api.WaitForSingleObject(child_handle, 0) == jobs.WAIT_TIMEOUT:
                jobs.checked(api.TerminateProcess(child_handle, 1))
                assert api.WaitForSingleObject(child_handle, 5000) == jobs.WAIT_OBJECT_0
            jobs.checked(api.CloseHandle(child_handle))
        if outer:
            outer.close()
        unrelated.terminate()
        unrelated.wait(timeout=10)


@native
def test_assignment_setup_failure_creates_no_child(tmp_path, monkeypatch):
    job = jobs.WindowsProcessJob()
    marker = tmp_path / "must-not-run"
    try:
        monkeypatch.setattr(job.api, "UpdateProcThreadAttribute", lambda *_: False)
        with pytest.raises(OSError):
            job.spawn([sys.executable, "-c", "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('ran')", str(marker)],
                      cwd=tmp_path, env=os.environ.copy())
        assert not marker.exists()
    finally:
        job.close()


@native
def test_graceful_close_reaps_child_and_closed_job_cannot_spawn(tmp_path):
    job = jobs.WindowsProcessJob()
    child = job.spawn([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, env=os.environ.copy())
    try:
        assert child.poll() is None
        job.close()
        assert child.wait(timeout=10) is not None
        with pytest.raises(OSError, match="closed"):
            job.spawn([sys.executable, "-c", "pass"], cwd=tmp_path, env=os.environ.copy())
    finally:
        job.close()


@native
def test_no_console_child_has_valid_null_streams_and_can_configure_uvicorn(tmp_path):
    script = tmp_path / "synthetic_logging_child.py"
    marker = tmp_path / "logging-ready.json"
    script.write_text("""
import json, logging.config, sys, time
from pathlib import Path
from uvicorn.config import LOGGING_CONFIG
assert sys.stdin is not None and sys.stdout is not None and sys.stderr is not None
assert sys.stdin.read() == ''
assert isinstance(sys.stdout.isatty(), bool) and isinstance(sys.stderr.isatty(), bool)
sys.stdout.write('Synthetic stdout check\\n'); sys.stdout.flush()
sys.stderr.write('Synthetic stderr check\\n'); sys.stderr.flush()
logging.config.dictConfig(LOGGING_CONFIG)
marker = Path(sys.argv[1])
temporary = marker.with_suffix('.pending')
temporary.write_text(json.dumps({'uvicorn_logging_ready': True}))
temporary.replace(marker)
while True: time.sleep(1)
""")
    job = jobs.WindowsProcessJob()
    child = job.spawn([sys.executable, str(script), str(marker)], cwd=tmp_path, env=os.environ.copy())
    try:
        deadline = time.monotonic() + 15
        while not marker.exists() and time.monotonic() < deadline:
            assert child.poll() is None, "No-console child could not configure its standard streams/logging"
            time.sleep(.05)
        assert marker.exists() and json.loads(marker.read_text())["uvicorn_logging_ready"]
        # This also proves HANDLE_LIST did not pass the private job handle:
        # otherwise closing its parent handle could not terminate this child.
        job.close()
        assert child.wait(timeout=10) is not None
    finally:
        job.close()
