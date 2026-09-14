"""Private Windows storage for the optional cloud companion, using only stdlib.

No Windows API is loaded at import time. Operations require a local NTFS volume.
Existing ACLs are inspected through held handles, never repaired. The process
user, SYSTEM and local administrators are the trust boundary (as for an owner
private POSIX file); this does not protect against code running as that user.
"""

from contextlib import contextmanager, ExitStack
import ctypes as C
from dataclasses import dataclass
import os
from pathlib import Path, PureWindowsPath
import re
import secrets


DWORD = C.c_uint32
WORD = C.c_uint16
BYTE = C.c_ubyte
BOOL = C.c_int32
HANDLE = C.c_void_p
PTR = C.c_void_p
MAX_SEQUENCE = 9_007_199_254_740_991
SYSTEM = "S-1-5-18"
ADMINISTRATORS = "S-1-5-32-544"
OWNER_RIGHTS = "S-1-3-4"
TRUSTED_INSTALLER = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
READ_CONTROL = 0x00020000
GENERIC_READ = 0x80000000
GENERIC_WRITE = 0x40000000
CREATE_NEW = 1
OPEN_EXISTING = 3
OPEN_ALWAYS = 4
REPARSE = 0x00000400
DIRECTORY = 0x00000010
OPEN_REPARSE_POINT = 0x00200000
BACKUP_SEMANTICS = 0x02000000
WRITE_THROUGH = 0x80000000
# Read/traverse/list plus ADD_SUBDIRECTORY are permitted on ancestors. ADD_FILE
# aliases FILE_WRITE_DATA and can authorize in-place reparse assignment, so it
# is rejected even when delete sharing is disabled. Every new component is
# independently owner/ACL checked. The immediate parent must not allow additions.
ANCESTOR_READ = 0x80000000 | 0x20000000 | 0x00100000 | READ_CONTROL | 0x80 | 0x20 | 0x08 | 0x01


class UnsafeWindowsPath(ValueError):
    """A path or opened object cannot establish private local storage."""


class FILETIME(C.Structure):
    _fields_ = [("low", DWORD), ("high", DWORD)]


class FILE_INFO(C.Structure):
    _fields_ = [("attributes", DWORD), ("created", FILETIME), ("accessed", FILETIME),
                ("written", FILETIME), ("volume", DWORD), ("size_high", DWORD),
                ("size_low", DWORD), ("links", DWORD), ("index_high", DWORD), ("index_low", DWORD)]


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("length", DWORD), ("descriptor", PTR), ("inherit", BOOL)]


class OVERLAPPED(C.Structure):
    _fields_ = [("internal", C.c_size_t), ("internal_high", C.c_size_t),
                ("offset", DWORD), ("offset_high", DWORD), ("event", HANDLE)]


class ACL(C.Structure):
    _fields_ = [("revision", BYTE), ("reserved", BYTE), ("size", WORD),
                ("count", WORD), ("reserved2", WORD)]


class ACE_HEADER(C.Structure):
    _fields_ = [("kind", BYTE), ("flags", BYTE), ("size", WORD)]


@dataclass(frozen=True)
class Ace:
    kind: int
    flags: int
    mask: int
    sid: str


def _lexical_path(value):
    """Pure syntax policy, also exercised on non-Windows test hosts."""
    if not isinstance(value, str) or not value or len(value) > 2048:
        raise UnsafeWindowsPath("Choose a bounded local Windows path.")
    value = value.replace("/", "\\")
    if (not re.match(r"^[A-Za-z]:\\", value) or value.startswith("\\") or
            any(ord(char) < 32 or ord(char) == 127 for char in value)):
        raise UnsafeWindowsPath("Use a local drive path without device namespaces or streams.")
    parts = value[3:].split("\\")
    if not parts or len(parts) > 64 or any(
        not part or part in {".", ".."} or len(part) > 255 or part[-1] in " ." or
        any(char in part for char in '<>:"|?*') or
        re.fullmatch(r"(?:CON|PRN|AUX|NUL|COM[1-9¹²³]|LPT[1-9¹²³])(?:\..*)?", part, re.I)
        for part in parts
    ):
        raise UnsafeWindowsPath("Choose a file path without reserved names or traversal.")
    return PureWindowsPath(value)


def _path(path):
    if os.name != "nt":
        raise UnsafeWindowsPath("The Windows storage adapter requires Windows.")
    raw = os.fspath(path)
    if not isinstance(raw, str):
        raise UnsafeWindowsPath("Choose a text local Windows path.")
    # Reject traversal before abspath can erase it. Reject drive-relative paths,
    # which depend on hidden per-drive current-directory state.
    if ".." in PureWindowsPath(raw).parts or (PureWindowsPath(raw).drive and not PureWindowsPath(raw).root):
        raise UnsafeWindowsPath("Choose a local path without traversal.")
    return Path(str(_lexical_path(os.path.abspath(os.path.expanduser(raw)))))


def _check_acl(owner, aces, user, *, directory=False, private_parent=False):
    trusted = {user, SYSTEM, ADMINISTRATORS}
    owners = trusted | {TRUSTED_INSTALLER} if directory and not private_parent else {user}
    if owner not in owners or aces is None or len(aces) > 1024:
        raise UnsafeWindowsPath("Storage ownership or permissions are not private.")
    for ace in aces:
        if ace.flags & 0x08:  # INHERIT_ONLY: not effective on this object.
            continue
        if ace.kind not in {0, 1}:
            raise UnsafeWindowsPath("Unsupported effective Windows permission entry.")
        if ace.kind == 1:  # Conservative: a deny never adds access.
            continue
        # OWNER RIGHTS represents this object's actual owner, not a separate
        # trustee. Resolve only after the opened object's owner passed the
        # above policy; this must never make a foreign owner acceptable.
        trustee = owner if ace.sid == OWNER_RIGHTS else ace.sid
        if trustee in trusted or (directory and not private_parent and trustee == TRUSTED_INSTALLER):
            continue
        allowed = (ANCESTOR_READ | (0 if private_parent else 0x04)) if directory else 0
        if ace.mask & ~allowed:
            raise UnsafeWindowsPath("Storage permits access by another Windows principal.")


def _sequence(raw, now_ms):
    if type(now_ms) is not int or not 0 <= now_ms <= MAX_SEQUENCE:
        raise ValueError("The connection clock is invalid.")
    previous = -1
    if raw is not None:
        if not isinstance(raw, bytes) or not re.fullmatch(rb"(?:0|[1-9][0-9]{0,15})\n", raw):
            raise ValueError("The connection sequence is invalid; existing state was preserved.")
        previous = int(raw)
    result = max(previous + 1, now_ms)
    if result > MAX_SEQUENCE:
        raise ValueError("The connection sequence is exhausted.")
    return result


def _error(code):
    # Never include a supplied path, native formatted message, or credential.
    message = "Windows private storage operation failed."
    error_type = (FileNotFoundError if code in {2, 3} else FileExistsError if code in {80, 183}
                  else BlockingIOError if code in {32, 33} else OSError)
    return error_type(code, message)


class _Native:
    def __init__(self):
        if os.name != "nt":
            raise UnsafeWindowsPath("The Windows storage adapter requires Windows.")
        self.kernel = C.WinDLL("kernel32", use_last_error=True)
        self.advapi = C.WinDLL("advapi32", use_last_error=True)
        self._bind()
        token = HANDLE()
        self.ok(self.OpenProcessToken(self.GetCurrentProcess(), 8, C.byref(token)))
        try:
            length = DWORD()
            self.GetTokenInformation(token, 1, None, 0, C.byref(length))
            if not 0 < length.value <= 65536:
                raise UnsafeWindowsPath("Cannot establish the Windows storage owner.")
            data = C.create_string_buffer(length.value)
            self.ok(self.GetTokenInformation(token, 1, data, length, C.byref(length)))
            self.user = self.sid(C.cast(data, C.POINTER(PTR))[0])
        finally:
            self.close(token)
        thread_token = HANDLE()
        if self.OpenThreadToken(self.GetCurrentThread(), 8, True, C.byref(thread_token)):
            self.close(thread_token)
            raise UnsafeWindowsPath("Impersonated Windows storage is unsupported.")
        if C.get_last_error() != 1008:  # ERROR_NO_TOKEN
            raise _error(C.get_last_error())

    def _bind(self):
        p = C.POINTER
        declarations = {
            "CreateFileW": (HANDLE, [C.c_wchar_p, DWORD, DWORD, PTR, DWORD, DWORD, HANDLE]),
            "CreateDirectoryW": (BOOL, [C.c_wchar_p, PTR]),
            "CloseHandle": (BOOL, [HANDLE]), "LocalFree": (PTR, [PTR]),
            "GetCurrentProcess": (HANDLE, []), "GetCurrentThread": (HANDLE, []),
            "GetFileType": (DWORD, [HANDLE]), "GetHandleInformation": (BOOL, [HANDLE, p(DWORD)]),
            "GetFileInformationByHandle": (BOOL, [HANDLE, p(FILE_INFO)]),
            "GetFinalPathNameByHandleW": (DWORD, [HANDLE, C.c_wchar_p, DWORD, DWORD]),
            "GetDriveTypeW": (DWORD, [C.c_wchar_p]),
            "GetVolumeInformationW": (BOOL, [C.c_wchar_p, C.c_wchar_p, DWORD, p(DWORD), p(DWORD), p(DWORD), C.c_wchar_p, DWORD]),
            "ReadFile": (BOOL, [HANDLE, PTR, DWORD, p(DWORD), PTR]),
            "WriteFile": (BOOL, [HANDLE, PTR, DWORD, p(DWORD), PTR]),
            "FlushFileBuffers": (BOOL, [HANDLE]),
            "LockFileEx": (BOOL, [HANDLE, DWORD, DWORD, DWORD, DWORD, p(OVERLAPPED)]),
            "UnlockFileEx": (BOOL, [HANDLE, DWORD, DWORD, DWORD, p(OVERLAPPED)]),
            "MoveFileExW": (BOOL, [C.c_wchar_p, C.c_wchar_p, DWORD]),
            "DeleteFileW": (BOOL, [C.c_wchar_p]),
        }
        security = {
            "OpenProcessToken": (BOOL, [HANDLE, DWORD, p(HANDLE)]),
            "OpenThreadToken": (BOOL, [HANDLE, DWORD, BOOL, p(HANDLE)]),
            "GetTokenInformation": (BOOL, [HANDLE, DWORD, PTR, DWORD, p(DWORD)]),
            "IsValidSid": (BOOL, [PTR]), "GetLengthSid": (DWORD, [PTR]),
            "ConvertSidToStringSidW": (BOOL, [PTR, p(PTR)]),
            "ConvertStringSecurityDescriptorToSecurityDescriptorW": (BOOL, [C.c_wchar_p, DWORD, p(PTR), p(DWORD)]),
            "GetSecurityInfo": (DWORD, [HANDLE, DWORD, DWORD, p(PTR), p(PTR), p(PTR), p(PTR), p(PTR)]),
            "GetSecurityDescriptorLength": (DWORD, [PTR]),
            "GetSecurityDescriptorControl": (BOOL, [PTR, p(WORD), p(DWORD)]),
            "IsValidAcl": (BOOL, [PTR]), "GetAce": (BOOL, [PTR, DWORD, p(PTR)]),
        }
        for library, items in ((self.kernel, declarations), (self.advapi, security)):
            for name, (restype, argtypes) in items.items():
                fn = getattr(library, name)
                fn.restype, fn.argtypes = restype, argtypes
                setattr(self, name, fn)

    @staticmethod
    def ok(value):
        if not value:
            raise _error(C.get_last_error())
        return value

    def close(self, handle):
        self.ok(self.CloseHandle(handle))

    def free(self, pointer):
        if self.LocalFree(pointer):
            raise _error(C.get_last_error())

    def sid(self, pointer):
        if not pointer or not self.IsValidSid(pointer):
            raise UnsafeWindowsPath("Invalid Windows storage security descriptor.")
        result = PTR()
        self.ok(self.ConvertSidToStringSidW(pointer, C.byref(result)))
        try:
            return C.wstring_at(result)
        finally:
            self.free(result)

    @staticmethod
    def name(path):
        return "\\\\?\\" + str(path)

    def volume(self, path):
        root = path.anchor
        if self.GetDriveTypeW(root) != 3:
            raise UnsafeWindowsPath("Use a fixed local NTFS drive.")
        flags, filesystem = DWORD(), C.create_unicode_buffer(32)
        self.ok(self.GetVolumeInformationW(root, None, 0, None, None, C.byref(flags), filesystem, len(filesystem)))
        if filesystem.value.upper() != "NTFS" or not flags.value & 0x08:
            raise UnsafeWindowsPath("Private storage requires local NTFS permissions.")

    @contextmanager
    def attributes(self, *, directory=False, descriptor=None):
        if descriptor is not None:
            # Preserve the already-validated ledger's owner and ACL on replacement.
            memory = C.create_string_buffer(descriptor)
            yield SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), C.cast(memory, PTR), False)
            return
        flags = "OICI" if directory else ""
        sddl = f"O:{self.user}D:P(A;{flags};FA;;;SY)(A;{flags};FA;;;BA)(A;{flags};FA;;;{self.user})"
        memory, length = PTR(), DWORD()
        self.ok(self.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, C.byref(memory), C.byref(length)))
        try:
            yield SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), memory, False)
        finally:
            self.free(memory)

    def security(self, handle):
        owner, dacl, descriptor = PTR(), PTR(), PTR()
        status = self.GetSecurityInfo(handle, 1, 0x01 | 0x04, C.byref(owner), None, C.byref(dacl), None, C.byref(descriptor))
        if status:
            raise _error(status)
        try:
            owner_sid = self.sid(owner)
            if not dacl or not self.IsValidAcl(dacl):
                raise UnsafeWindowsPath("A private Windows DACL is required.")
            acl = C.cast(dacl, C.POINTER(ACL)).contents
            if acl.count > 1024:
                raise UnsafeWindowsPath("Unsupported Windows permission list.")
            entries = []
            for index in range(acl.count):
                address = PTR()
                self.ok(self.GetAce(dacl, index, C.byref(address)))
                header = C.cast(address, C.POINTER(ACE_HEADER)).contents
                if header.flags & 0x08:
                    continue
                if header.kind not in {0, 1} or header.size < 16:
                    raise UnsafeWindowsPath("Unsupported effective Windows permission entry.")
                sid_address = address.value + 8
                sid = self.sid(sid_address)
                if 8 + self.GetLengthSid(sid_address) > header.size:
                    raise UnsafeWindowsPath("Invalid Windows permission entry.")
                mask = C.cast(address.value + 4, C.POINTER(DWORD)).contents.value
                entries.append(Ace(header.kind, header.flags, mask, sid))
            length = self.GetSecurityDescriptorLength(descriptor)
            control, revision = WORD(), DWORD()
            self.ok(self.GetSecurityDescriptorControl(descriptor, C.byref(control), C.byref(revision)))
            if not 0 < length <= 65536 or not control.value & 0x8000:
                raise UnsafeWindowsPath("Invalid Windows storage security descriptor.")
            return owner_sid, entries, C.string_at(descriptor, length)
        finally:
            self.free(descriptor)

    def check(self, handle, path, *, directory=False, private_parent=False):
        info, flags = FILE_INFO(), DWORD()
        self.ok(self.GetFileInformationByHandle(handle, C.byref(info)))
        self.ok(self.GetHandleInformation(handle, C.byref(flags)))
        if (self.GetFileType(handle) != 1 or flags.value & 1 or
                bool(info.attributes & DIRECTORY) != directory or
                info.attributes & (REPARSE | 0x1000 | 0x40000 | 0x400000) or
                (not directory and info.links != 1)):
            raise UnsafeWindowsPath("Use a regular local object without links or inherited handles.")
        name = C.create_unicode_buffer(4096)
        size = self.GetFinalPathNameByHandleW(handle, name, len(name), 0)
        if not size:
            raise _error(C.get_last_error())
        if size >= len(name) or name.value.casefold().rstrip("\\") != self.name(path).casefold().rstrip("\\"):
            raise UnsafeWindowsPath("The opened Windows path was redirected.")
        owner, entries, descriptor = self.security(handle)
        _check_acl(owner, entries, self.user, directory=directory, private_parent=private_parent)
        return info, descriptor

    @contextmanager
    def opened(self, path, *, access=GENERIC_READ, disposition=OPEN_EXISTING, directory=False, private_parent=False, descriptor=None):
        flags = OPEN_REPARSE_POINT | (BACKUP_SEMANTICS if directory else 0)
        if access & GENERIC_WRITE:
            flags |= WRITE_THROUGH
        # Directory data writes can mutate reparse state in place. Keep only
        # read sharing on held ancestors; ordinary file handles still share
        # reads/writes so independent processes can coordinate via LockFileEx.
        sharing = 1 if directory else 3
        with self.attributes(directory=directory, descriptor=descriptor) as attrs:
            handle = self.CreateFileW(self.name(path), access | READ_CONTROL, sharing, C.byref(attrs), disposition, flags, None)
            error = C.get_last_error() if handle == C.c_void_p(-1).value else 0
        if handle == C.c_void_p(-1).value:
            raise _error(error)
        try:
            info, security = self.check(handle, path, directory=directory, private_parent=private_parent)
            yield handle, info, security
        finally:
            self.close(handle)

    @contextmanager
    def parent(self, path, *, create=False, allow_missing=False):
        self.volume(path)
        with ExitStack() as stack:
            current = Path(path.anchor)
            stack.enter_context(self.opened(current, access=0x80, directory=True, private_parent=current == path.parent))
            for part in path.parent.parts[1:]:
                current = current / part
                try:
                    stack.enter_context(self.opened(current, access=0x80, directory=True, private_parent=current == path.parent))
                except FileNotFoundError:
                    if not create:
                        if allow_missing:
                            yield False
                            return
                        raise
                    with self.attributes(directory=True) as attrs:
                        if not self.CreateDirectoryW(self.name(current), C.byref(attrs)):
                            code = C.get_last_error()
                            if code != 183:
                                raise _error(code)
                    stack.enter_context(self.opened(current, access=0x80, directory=True, private_parent=current == path.parent))
            yield True

    def read(self, handle, info, maximum):
        if (info.size_high << 32) | info.size_low > maximum:
            raise ValueError("The private storage file is too large.")
        buffer, count = C.create_string_buffer(maximum + 1), DWORD()
        self.ok(self.ReadFile(handle, buffer, len(buffer), C.byref(count), None))
        if count.value > maximum:
            raise ValueError("The private storage file is too large.")
        return buffer.raw[:count.value]

    def write(self, handle, raw):
        offset = 0
        while offset < len(raw):
            data, count = C.create_string_buffer(raw[offset:]), DWORD()
            self.ok(self.WriteFile(handle, data, len(raw) - offset, C.byref(count), None))
            if not count.value or count.value > len(raw) - offset:
                raise OSError("Windows private storage write was incomplete.")
            offset += count.value
        self.ok(self.FlushFileBuffers(handle))


def validate_path(path):
    """Validate existing components without creating anything; return a Path."""
    target, native = _path(path), _Native()
    with native.parent(target, allow_missing=True) as exists:
        if exists:
            try:
                with native.opened(target):
                    pass
            except FileNotFoundError:
                pass
    return target


def ensure_new_destination(path):
    """Check and create only missing private directories before code redemption."""
    target, native = _path(path), _Native()
    with native.parent(target, create=True):
        try:
            with native.opened(target):
                pass
        except FileNotFoundError:
            return target
        raise FileExistsError("A connection file already exists.")


def write_new(path, raw_bytes):
    """Create a new credential file. Never truncate or change an existing file."""
    if not isinstance(raw_bytes, bytes) or len(raw_bytes) > 4096:
        raise ValueError("Use at most 4096 bytes of connection data.")
    target, native = _path(path), _Native()
    with native.parent(target, create=True):
        with native.opened(target, access=GENERIC_WRITE, disposition=CREATE_NEW) as (handle, _, _):
            native.write(handle, raw_bytes)


def read_bytes(path, max_bytes):
    """Inspect the opened object, then read a bounded number of bytes."""
    if type(max_bytes) is not int or not 1 <= max_bytes <= 65536:
        raise ValueError("Use a bounded private storage read.")
    target, native = _path(path), _Native()
    with native.parent(target):
        with native.opened(target) as (handle, info, _):
            return native.read(handle, info, max_bytes)


def reserve_sequence(path, now_ms):
    """Reserve before sending: stable exclusive lock, flushed counter, rename.

    A competing process receives BlockingIOError; it never reuses a reservation.
    Malformed existing state is preserved. No cross-volume or copy fallback.
    """
    _sequence(None, now_ms)
    target, native = _path(path), _Native()
    lock_path = target.with_name(target.name + ".lock")
    _lexical_path(str(lock_path))
    with native.parent(target, create=True):
        with native.opened(lock_path, access=GENERIC_READ | GENERIC_WRITE, disposition=OPEN_ALWAYS) as (lock, _, _):
            overlap = OVERLAPPED()
            native.ok(native.LockFileEx(lock, 3, 0, 1, 0, C.byref(overlap)))
            try:
                raw, descriptor = None, None
                try:
                    with native.opened(target) as (handle, info, descriptor):
                        raw = native.read(handle, info, 64)
                except FileNotFoundError:
                    pass
                sequence = _sequence(raw, now_ms)
                temporary = target.with_name(".aislesignals-sequence-" + secrets.token_hex(16) + ".tmp")
                created = False
                try:
                    with native.opened(temporary, access=GENERIC_WRITE, disposition=CREATE_NEW, descriptor=descriptor) as (handle, _, _):
                        created = True
                        native.write(handle, f"{sequence}\n".encode("ascii"))
                    native.ok(native.MoveFileExW(native.name(temporary), native.name(target), 0x01 | 0x08))
                    created = False
                finally:
                    if created:
                        native.ok(native.DeleteFileW(native.name(temporary)))
                return sequence
            finally:
                native.ok(native.UnlockFileEx(lock, 0, 1, 0, C.byref(overlap)))
