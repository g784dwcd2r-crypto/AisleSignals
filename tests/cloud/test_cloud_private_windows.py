"""Policy tests everywhere; actual ACL/handle/process tests only on Windows.

All files, permission changes, links and child processes use synthetic temporary
data. These tests never open a real connection file or contact a service.
"""

import ctypes as C
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts import cloud_private_windows as storage


USER = "S-1-5-21-100-200-300-1001"
OTHER = "S-1-5-21-100-200-300-1002"
EVERYONE = "S-1-1-0"
NATIVE = pytest.mark.skipif(os.name != "nt", reason="Native Windows ACL/NTFS/handle behavior requires Windows CI")


def allow(sid=USER, mask=0x1F01FF, *, flags=0):
    return storage.Ace(0, flags, mask, sid)


def test_import_has_no_native_library_side_effect(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Import must not load a Windows library")
    monkeypatch.setattr(C, "WinDLL", forbidden, raising=False)
    name = "synthetic_windows_storage_import"
    spec = importlib.util.spec_from_file_location(name, storage.__file__)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
        assert callable(module.write_new)
    finally:
        sys.modules.pop(name)


@pytest.mark.skipif(os.name == "nt", reason="Other-platform refusal")
@pytest.mark.parametrize("operation", [
    lambda: storage.validate_path("C:\\private\\config.json"),
    lambda: storage.ensure_new_destination("C:\\private\\config.json"),
    lambda: storage.write_new("C:\\private\\config.json", b"synthetic"),
    lambda: storage.read_bytes("C:\\private\\config.json", 4096),
    lambda: storage.reserve_sequence("C:\\private\\sequence", 123),
])
def test_native_operations_refuse_other_platforms(operation):
    with pytest.raises(storage.UnsafeWindowsPath, match="requires Windows"):
        operation()


@pytest.mark.parametrize("value", [r"C:\Users\Alice\private\connection.json", r"d:\Private folder\连接.json", "C:/private/sequence"])
def test_local_path_syntax(value):
    assert storage._lexical_path(value).is_absolute()


@pytest.mark.parametrize("value", [
    "", "C:", "C:\\", "C:relative.json", "relative.json", r"\rooted\file", r"\\server\share\file",
    r"\\?\C:\private\file", r"\\.\C:\private\file", r"C:\private\..\file", r"C:\private\.\file",
    r"C:\private\file:stream", r"C:\private\file.", "C:\\private\\file ", r"C:\private\NUL",
    r"C:\private\CON.json", r"C:\private\com1.log", r"C:\private\LPT³.txt", r"C:\private\a?b",
    "C:\\private\\a\x00b", "C:\\private\\a\nb", "C:\\private\\a\x7fb", "C:\\a\\\\b",
    "C:\\" + "a" * 256, "C:\\" + "a\\" * 65 + "b", "C:\\" + "x" * 2049,
])
def test_ambiguous_or_redirectable_path_syntax_is_rejected(value):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._lexical_path(value)


def test_private_acl_allows_only_current_user_and_os_administrators():
    storage._check_acl(USER, [allow(), allow(storage.SYSTEM), allow(storage.ADMINISTRATORS)], USER)


@pytest.mark.parametrize("owner", [USER, storage.SYSTEM, storage.ADMINISTRATORS, storage.TRUSTED_INSTALLER])
def test_owner_rights_resolves_only_to_validated_trusted_ancestor_owner(owner):
    # Matches the native CI ACL: inheritable OWNER RIGHTS full control plus
    # explicit SYSTEM/Administrators. Flags 3 apply to this object as well.
    entries = [allow(storage.SYSTEM), allow(storage.ADMINISTRATORS), allow(storage.OWNER_RIGHTS, flags=3)]
    storage._check_acl(owner, entries, USER, directory=True)


def test_owner_rights_for_current_owner_is_private_but_other_grants_still_reject():
    storage._check_acl(USER, [allow(storage.OWNER_RIGHTS)], USER)
    storage._check_acl(USER, [allow(storage.OWNER_RIGHTS)], USER, directory=True, private_parent=True)
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [allow(storage.OWNER_RIGHTS), allow(EVERYONE, storage.GENERIC_READ)], USER)
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [allow(storage.OWNER_RIGHTS), allow("S-1-3-5")], USER)


@pytest.mark.parametrize("owner,directory,private_parent", [
    (OTHER, False, False), (OTHER, True, False), (OTHER, True, True),
    (EVERYONE, True, False), (storage.OWNER_RIGHTS, True, False),
    (storage.ADMINISTRATORS, False, False), (storage.SYSTEM, True, True),
])
def test_owner_rights_cannot_bypass_actual_owner_requirement(owner, directory, private_parent):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(owner, [allow(storage.OWNER_RIGHTS)], USER,
                           directory=directory, private_parent=private_parent)


@pytest.mark.parametrize("owner", [OTHER, EVERYONE, storage.SYSTEM, storage.ADMINISTRATORS])
def test_existing_leaf_must_be_owned_by_current_user(owner):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(owner, [allow()], USER)


@pytest.mark.parametrize("mask", [1, storage.GENERIC_READ, storage.GENERIC_WRITE, storage.READ_CONTROL, 0x100000, 0x1F01FF])
def test_other_principal_leaf_grants_are_not_private(mask):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [allow(), allow(EVERYONE, mask)], USER)


def test_ancestor_read_and_sibling_directory_creation_remain_allowed():
    for owner in [USER, storage.SYSTEM, storage.ADMINISTRATORS, storage.TRUSTED_INSTALLER]:
        storage._check_acl(owner, [allow(EVERYONE, storage.ANCESTOR_READ | 4)], USER, directory=True)
    storage._check_acl(USER, [allow(EVERYONE, storage.ANCESTOR_READ)], USER, directory=True, private_parent=True)


@pytest.mark.parametrize("mask", [0x02, 0x06, 0x40, 0x10000, 0x40000, 0x80000, 0x10, 0x100, 0x40000000, 0x10000000])
def test_ancestor_modification_grants_are_rejected(mask):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [allow(EVERYONE, mask)], USER, directory=True)


@pytest.mark.parametrize("mask", [2, 4, 6])
def test_immediate_parent_cannot_allow_other_principals_to_add_entries(mask):
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [allow(EVERYONE, mask)], USER, directory=True, private_parent=True)


def test_foreign_owner_null_and_unknown_effective_dacl_fail_closed():
    for owner, entries in [(OTHER, [allow()]), (USER, None), (USER, [storage.Ace(5, 0, 0, USER)])]:
        with pytest.raises(storage.UnsafeWindowsPath):
            storage._check_acl(owner, entries, USER, directory=True)
    # Inherit-only entries do not apply to the opened object. Explicit denies
    # cannot increase access, but we do not depend on ACE order to cancel grants.
    storage._check_acl(USER, [allow(EVERYONE, flags=8), storage.Ace(1, 0, 0xFFFFFFFF, EVERYONE)], USER)
    with pytest.raises(storage.UnsafeWindowsPath):
        storage._check_acl(USER, [storage.Ace(1, 0, 0xFFFFFFFF, EVERYONE), allow(EVERYONE)], USER)


def test_windows_structure_layouts_are_not_host_c_ulong_layouts():
    assert C.sizeof(storage.DWORD) == 4
    assert C.sizeof(storage.FILE_INFO) == 52
    assert C.sizeof(storage.ACL) == 8
    assert C.sizeof(storage.ACE_HEADER) == 4
    assert C.sizeof(storage.SECURITY_ATTRIBUTES) == (24 if C.sizeof(C.c_void_p) == 8 else 12)
    assert C.sizeof(storage.OVERLAPPED) == (32 if C.sizeof(C.c_void_p) == 8 else 20)


@pytest.mark.parametrize("raw", [b"", b"01\n", b"-1\n", b"1", b"1\r\n", b"1\n2\n", b" 1\n", b"nan\n", b"9999999999999999\n"])
def test_invalid_sequence_never_resets_existing_state(raw):
    with pytest.raises(ValueError):
        storage._sequence(raw, 100)


def test_sequence_handles_clock_rollback_without_reusing_value():
    assert storage._sequence(None, 0) == 0
    assert storage._sequence(b"1000\n", 999) == 1001
    assert storage._sequence(b"1000\n", 2000) == 2000
    assert storage._sequence(b"0\n", 0) == 1
    with pytest.raises(ValueError, match="exhausted"):
        storage._sequence(f"{storage.MAX_SEQUENCE}\n".encode(), 0)


@pytest.mark.parametrize("value", [True, -1, 0.5, "1", None, storage.MAX_SEQUENCE + 1])
def test_invalid_sequence_clock_rejected_before_native_io(value):
    with pytest.raises(ValueError):
        storage.reserve_sequence("synthetic-not-opened", value)


@pytest.mark.parametrize("value", [True, 0, -1, 65537, "4096"])
def test_read_limit_validated_before_native_io(value):
    with pytest.raises(ValueError):
        storage.read_bytes("synthetic-not-opened", value)


def test_new_write_bounds_validated_before_native_io():
    for raw in ["not bytes", bytearray(b"synthetic"), b"x" * 4097]:
        with pytest.raises(ValueError):
            storage.write_new("synthetic-not-opened", raw)


@pytest.mark.parametrize("code,kind", [(2, FileNotFoundError), (3, FileNotFoundError), (80, FileExistsError), (183, FileExistsError), (32, BlockingIOError), (33, BlockingIOError), (5, OSError)])
def test_native_failures_are_constant_and_have_no_filename(code, kind):
    failure = storage._error(code)
    assert isinstance(failure, kind)
    assert failure.filename is None
    assert failure.strerror == "Windows private storage operation failed."


@pytest.fixture
def native_target(tmp_path):
    if os.name != "nt":
        pytest.skip("Native Windows tests require Windows CI")
    # Resolve pytest's synthetic base, never a real credential location.
    return tmp_path.resolve() / "private" / "connection.json"


def security_bytes(path):
    """Test-only raw descriptor read; permits inspecting an intentionally bad ACL."""
    native = storage._Native()
    handle = native.CreateFileW(native.name(path), storage.READ_CONTROL, 3, None, storage.OPEN_EXISTING,
                                storage.OPEN_REPARSE_POINT | storage.BACKUP_SEMANTICS, None)
    if handle == C.c_void_p(-1).value:
        raise storage._error(C.get_last_error())
    try:
        return native.security(handle)[2]
    finally:
        native.close(handle)


def set_synthetic_acl(path, *, public_mask="FR", trustee="WD"):
    """Change only the explicit disposable path supplied by the native fixture."""
    native = storage._Native()
    setter = native.advapi.SetFileSecurityW
    setter.argtypes, setter.restype = [C.c_wchar_p, storage.DWORD, storage.PTR], storage.BOOL
    descriptor, length = storage.PTR(), storage.DWORD()
    sddl = f"O:{native.user}D:P(A;;FA;;;{native.user})(A;;FA;;;SY)(A;;FA;;;BA)(A;;{public_mask};;;{trustee})"
    native.ok(native.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(descriptor), C.byref(length)))
    try:
        native.ok(setter(native.name(path), 0x80000004, descriptor))
    finally:
        native.free(descriptor)


@NATIVE
def test_native_create_preflight_read_and_no_overwrite(native_target):
    assert storage.validate_path(native_target) == native_target
    assert not native_target.parent.exists()
    assert storage.ensure_new_destination(native_target) == native_target
    assert native_target.parent.is_dir() and not native_target.exists()
    storage.write_new(native_target, b"synthetic credential")
    assert storage.read_bytes(native_target, 4096) == b"synthetic credential"
    previous_acl = security_bytes(native_target)
    for operation in [lambda: storage.ensure_new_destination(native_target), lambda: storage.write_new(native_target, b"replacement")]:
        with pytest.raises(FileExistsError):
            operation()
    assert native_target.read_bytes() == b"synthetic credential"
    assert security_bytes(native_target) == previous_acl


@NATIVE
def test_native_public_leaf_is_rejected_without_repair(native_target):
    storage.write_new(native_target, b"synthetic original")
    set_synthetic_acl(native_target)
    previous_acl = security_bytes(native_target)
    for operation in [lambda: storage.validate_path(native_target), lambda: storage.read_bytes(native_target, 4096)]:
        with pytest.raises(storage.UnsafeWindowsPath):
            operation()
    assert native_target.read_bytes() == b"synthetic original"
    assert security_bytes(native_target) == previous_acl


@NATIVE
def test_native_owner_rights_acl_is_accepted_without_rewriting_it(native_target):
    storage.write_new(native_target, b"synthetic owner-rights credential")
    set_synthetic_acl(native_target, public_mask="FA", trustee=storage.OWNER_RIGHTS)
    before = security_bytes(native_target)
    assert storage.validate_path(native_target) == native_target
    assert storage.read_bytes(native_target, 4096) == b"synthetic owner-rights credential"
    with pytest.raises(FileExistsError):
        storage.write_new(native_target, b"must not replace")
    assert security_bytes(native_target) == before
    assert native_target.read_bytes() == b"synthetic owner-rights credential"


@NATIVE
def test_native_mutable_parent_is_rejected_without_acl_changes(native_target):
    storage.ensure_new_destination(native_target)
    set_synthetic_acl(native_target.parent, public_mask="FA")
    previous_acl = security_bytes(native_target.parent)
    with pytest.raises(storage.UnsafeWindowsPath):
        storage.write_new(native_target, b"must not write")
    assert not native_target.exists()
    assert security_bytes(native_target.parent) == previous_acl


@NATIVE
def test_native_empty_ancestor_write_data_is_rejected_before_child_creation(native_target, monkeypatch):
    # ADD_FILE on a directory aliases FILE_WRITE_DATA and can authorize
    # FSCTL_SET_REPARSE_POINT without delete sharing. Reject this ACL before
    # creating anything below the empty ancestor; no exploit/impersonation needed.
    storage.ensure_new_destination(native_target)
    ancestor = native_target.parent
    assert not list(ancestor.iterdir())
    set_synthetic_acl(ancestor, public_mask="0x2")
    before = security_bytes(ancestor)
    target = ancestor / "not-created" / "connection.json"
    native = storage._Native()
    def unexpected_creation(*args):
        pytest.fail("Unsafe ancestor must be refused before any child creation")
    monkeypatch.setattr(native, "CreateDirectoryW", unexpected_creation)
    monkeypatch.setattr(storage, "_Native", lambda: native)
    with pytest.raises(storage.UnsafeWindowsPath):
        storage.write_new(target, b"must not write")
    assert not list(ancestor.iterdir())
    assert security_bytes(ancestor) == before


@NATIVE
def test_native_hard_link_is_rejected_and_original_preserved(native_target):
    storage.write_new(native_target, b"synthetic original")
    linked = native_target.with_name("linked.json")
    os.link(native_target, linked)
    for path in [native_target, linked]:
        with pytest.raises(storage.UnsafeWindowsPath):
            storage.read_bytes(path, 4096)
    assert linked.read_bytes() == native_target.read_bytes() == b"synthetic original"


@NATIVE
def test_native_directory_junction_cannot_redirect_create(native_target):
    storage.ensure_new_destination(native_target)
    other = native_target.parent.with_name("unrelated")
    other.mkdir()
    link = native_target.parent / "junction"
    result = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(other)], capture_output=True, timeout=10)
    assert result.returncode == 0, "Synthetic local NTFS junction creation failed"
    before = security_bytes(other)
    try:
        with pytest.raises((storage.UnsafeWindowsPath, OSError)):
            storage.write_new(link / "connection.json", b"must not write")
        assert not (other / "connection.json").exists()
        assert security_bytes(other) == before
    finally:
        os.rmdir(link)


@NATIVE
def test_native_held_ancestor_prevents_directory_swap(native_target):
    storage.ensure_new_destination(native_target)
    native = storage._Native()
    renamed = native_target.parent.with_name("renamed")
    with native.parent(native_target):
        with pytest.raises(OSError) as failure:
            os.rename(native_target.parent, renamed)
        assert failure.value.winerror in {5, 32}
        with native.opened(native_target, access=storage.GENERIC_WRITE, disposition=storage.CREATE_NEW) as (handle, _, _):
            native.write(handle, b"synthetic original")
    assert native_target.read_bytes() == b"synthetic original"
    assert not renamed.exists()


@NATIVE
def test_native_held_directory_rejects_data_writer_but_allows_child_creation(native_target):
    storage.ensure_new_destination(native_target)
    native = storage._Native()
    with native.parent(native_target):
        handle = native.CreateFileW(native.name(native_target.parent), 0x02, 3, None, storage.OPEN_EXISTING,
                                    storage.BACKUP_SEMANTICS | storage.OPEN_REPARSE_POINT, None)
        error = C.get_last_error()
        if handle != C.c_void_p(-1).value:
            native.close(handle)
            pytest.fail("A held directory must not admit a FILE_WRITE_DATA handle")
        assert error == 32  # ERROR_SHARING_VIOLATION, not an ACL denial.
        # A new child is a separate object: creation must remain functional.
        nested = native_target.parent / "child" / "connection.json"
        storage.write_new(nested, b"synthetic child")
        assert storage.read_bytes(nested, 4096) == b"synthetic child"


@NATIVE
def test_native_oversized_read_refuses_without_modifying_content(native_target):
    storage.write_new(native_target, b"synthetic data")
    with pytest.raises(ValueError, match="too large"):
        storage.read_bytes(native_target, 4)
    assert native_target.read_bytes() == b"synthetic data"


@NATIVE
def test_native_sequence_survives_clock_rollback_and_process_restart(native_target):
    storage.ensure_new_destination(native_target)
    sequence = native_target.with_name("sequence")
    assert storage.reserve_sequence(sequence, 2000) == 2000
    before_acl = security_bytes(sequence)
    assert storage.reserve_sequence(sequence, 1000) == 2001
    assert security_bytes(sequence) == before_acl
    code = "from scripts.cloud_private_windows import reserve_sequence; import sys; print(reserve_sequence(sys.argv[1], 0))"
    result = subprocess.run([sys.executable, "-c", code, str(sequence)], cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "2002"
    assert sequence.read_bytes() == b"2002\n"


@NATIVE
def test_native_cross_process_lock_contention_does_not_advance_counter(native_target):
    storage.ensure_new_destination(native_target)
    sequence = native_target.with_name("sequence")
    storage.reserve_sequence(sequence, 1000)
    native = storage._Native()
    with native.parent(sequence):
        with native.opened(sequence.with_name("sequence.lock"), access=storage.GENERIC_READ | storage.GENERIC_WRITE) as (handle, _, _):
            overlap = storage.OVERLAPPED()
            native.ok(native.LockFileEx(handle, 3, 0, 1, 0, C.byref(overlap)))
            try:
                code = "from scripts.cloud_private_windows import reserve_sequence; import sys\ntry: reserve_sequence(sys.argv[1], 0)\nexcept BlockingIOError: sys.exit(0)\nelse: sys.exit(2)"
                result = subprocess.run([sys.executable, "-c", code, str(sequence)], cwd=Path(__file__).parents[2], capture_output=True, text=True, timeout=15)
                assert result.returncode == 0, result.stderr
                assert sequence.read_bytes() == b"1000\n"
            finally:
                native.ok(native.UnlockFileEx(handle, 0, 1, 0, C.byref(overlap)))
    assert storage.reserve_sequence(sequence, 0) == 1001


@NATIVE
def test_native_concurrent_reservations_are_unique_or_explicitly_busy(native_target):
    storage.ensure_new_destination(native_target)
    sequence = native_target.with_name("sequence")
    storage.reserve_sequence(sequence, 1000)
    def reserve(_):
        try:
            return storage.reserve_sequence(sequence, 0)
        except BlockingIOError:
            return None
    with ThreadPoolExecutor(max_workers=6) as pool:
        outcomes = list(pool.map(reserve, range(12)))
    successes = sorted(value for value in outcomes if value is not None)
    assert successes and successes == list(range(1001, 1001 + len(successes)))
    assert sequence.read_bytes() == f"{successes[-1]}\n".encode()


@NATIVE
@pytest.mark.parametrize("raw", [b"", b"01\n", b"42", b"corrupt\n"])
def test_native_malformed_counter_preserved(native_target, raw):
    storage.write_new(native_target, raw)
    previous_acl = security_bytes(native_target)
    with pytest.raises(ValueError):
        storage.reserve_sequence(native_target, 100)
    assert native_target.read_bytes() == raw
    assert security_bytes(native_target) == previous_acl


@NATIVE
@pytest.mark.parametrize("operation", ["FlushFileBuffers", "MoveFileExW"])
def test_native_failed_flush_or_replace_preserves_old_counter(native_target, monkeypatch, operation):
    storage.reserve_sequence(native_target, 100)
    native = storage._Native()
    before_acl = security_bytes(native_target)
    def failure(*args):
        C.set_last_error(5)
        return 0
    monkeypatch.setattr(native, operation, failure)
    monkeypatch.setattr(storage, "_Native", lambda: native)
    with pytest.raises(OSError):
        storage.reserve_sequence(native_target, 0)
    assert native_target.read_bytes() == b"100\n"
    assert security_bytes(native_target) == before_acl
    assert not list(native_target.parent.glob(".aislesignals-sequence-*.tmp"))
