"""Release archive checks reject altered bytes and accidental external links."""
import json
import socket
from pathlib import Path

import pytest

from scripts.package_bundle import MANIFEST, inventory, verify_inventory
from scripts.smoke_bundle import wait_for_server_exit


def sealed(tmp_path: Path):
    (tmp_path / "app.js").write_text("known release bytes")
    (tmp_path / MANIFEST).write_text(json.dumps({"files": inventory(tmp_path)}))
    verify_inventory(tmp_path)


@pytest.mark.parametrize("change", ["modified", "added", "removed"])
def test_manifest_rejects_changed_release_files(tmp_path, change):
    sealed(tmp_path)
    if change == "modified":
        (tmp_path / "app.js").write_text("changed release bytes")
    elif change == "added":
        (tmp_path / "unexpected.js").write_text("untracked executable content")
    else:
        (tmp_path / "app.js").unlink()
    with pytest.raises(ValueError, match="differ"):
        verify_inventory(tmp_path)


def test_manifest_rejects_external_symlinks(tmp_path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("must not be included")
    try:
        (bundle / "escape").symlink_to(outside)
    except OSError:
        pytest.skip("This host does not grant symlink creation")
    with pytest.raises(ValueError, match="Unsafe bundle link"):
        inventory(bundle)


def test_packaged_shutdown_check_refuses_a_lingering_server_without_stopping_it():
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(4)
        port = listener.getsockname()[1]
        with pytest.raises(AssertionError, match="still running"):
            wait_for_server_exit(port, timeout=0.05)
        assert listener.fileno() >= 0
    wait_for_server_exit(port, timeout=1)
