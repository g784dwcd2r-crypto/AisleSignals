# Windows storage for laptop pairing

This standard-library adapter prepares the optional cloud connection tool for
Windows. It stores the device credential and heartbeat sequence locally. It does
not read CCTV, run detection, install a service, upload evidence or provide an
event outbox. A heartbeat still does not prove that cameras are monitored.

Implementation and policy tests are complete. **Native Windows acceptance is
pending the Windows CI run and a supported laptop check.** Skipped tests on macOS
are not evidence of Windows compatibility.

## Integration contract

Keep `cloud_private_windows.py` beside `cloud_companion.py` in the downloadable
connection-tool ZIP. The coordinating integration selects this module only when
`os.name == "nt"`. Importing it on macOS/Linux loads no Windows libraries;
calling its storage operations there fails explicitly. It has no dependencies
beyond Python's standard library.

| Function | Behavior |
| --- | --- |
| `validate_path(path) -> pathlib.Path` | Check local path syntax and every existing component. Does not create anything. Existing files must pass private-object checks. |
| `ensure_new_destination(path) -> pathlib.Path` | Create only missing directories with a private ACL; reject an occupied destination before asking for or consuming an enrolment code. This does not reserve the filename against another process running as the same user. |
| `write_new(path, raw_bytes) -> None` | Create a new file with an explicit private ACL, write at most 4096 bytes and flush it. Existing files are never truncated or repaired. |
| `read_bytes(path, max_bytes) -> bytes` | Validate the opened object before a bounded read; the maximum must be an integer from 1 through 65536. JSON and credential validation remain the caller's responsibility. |
| `reserve_sequence(path, now_ms) -> int` | Reserve and persist a value greater than the previous value and at least `now_ms`, bounded to JavaScript's maximum safe integer. The caller supplies integer milliseconds. |

`ValueError` (including `UnsafeWindowsPath`) means an unsupported/unsafe path,
permissions or content. `FileExistsError` means an occupied safe creation target;
an unsafe occupied target may instead raise `ValueError` or `OSError`.
`BlockingIOError` means a sharing/lock conflict. Other native I/O failures raise
`OSError` with a constant message and numeric code; they do not include paths or
file contents. Do not retry by weakening permissions or deleting state. Existing
companion error handling should present the failure without logging credentials.

## Storage boundary

Use a dedicated directory under the signed-in user's local profile on a fixed
NTFS drive, for example `%LOCALAPPDATA%\AisleSignals\connection.json`. The CLI
accepts a normal local pathname; it does not expand `%VARIABLE%` syntax itself.
Network shares, mapped/removable drives, non-NTFS volumes, device namespaces,
alternate data streams, traversal, reserved DOS names and ambiguous trailing
spaces/dots are rejected. OneDrive placeholders, junctions and other reparse
points are not accepted. If the profile is redirected, choose another supported
local private directory; do not remove these checks.

Each ancestor is opened without write or delete sharing and held throughout the
operation. These handles request `FILE_LIST_DIRECTORY` as well as attribute and
security reads: attribute-only opens do not participate in NTFS sharing checks.
The adapter checks the final path reported by the handle, object
type, owner and DACL. Holding the handles prevents renaming/deleting checked
ancestors. The ACL policy separately rejects other-principal `FILE_WRITE_DATA`
(`FILE_ADD_FILE`) and `FILE_WRITE_ATTRIBUTES`, which could authorize in-place
reparse assignment without a rename. Only file handles retain read/write sharing
for the lock protocol; native tests must confirm that directory read-only sharing
still permits creating child objects. File handles must not be inheritable;
credential, counter and lock files must have exactly one hard link.

The current process user, SYSTEM and local administrators form the private-file
trust boundary. TrustedInstaller may also own or control an ancestor. Other
principals may read/traverse ancestors, but cannot delete or change them. Only
ancestors above the final private directory may grant sibling-directory creation
(`FILE_ADD_SUBDIRECTORY`, `0x4`), never file creation (`0x2`):
every subsequently opened component must still pass the owner and ACL checks.
The final parent must be current-user-owned and must not permit other principals
to add files. Effective unsupported ACE types and null DACLs fail closed.

An `OWNER RIGHTS` ACE (`S-1-3-4`) refers to the object's actual owner. The adapter
resolves it only after validating that owner under the same rules above. It does
not grant trust to arbitrary trustees or permit a foreign-owned credential or
final parent. This accommodates Windows' owner-relative ACLs, including the
administrator-owned ancestor observed in native CI, without rewriting any ACL.

New objects get a protected DACL allowing only the current user, SYSTEM and
administrators. Existing owners and ACLs are never changed to make a path pass.
This boundary does not resist administrator access, malware executing as the
same account, or disk access outside the running OS. Disk encryption is a
separate device control. The adapter deliberately does not implement DPAPI.

## Reservation and failure behavior

Reservations use a stable private `<sequence>.lock` file and a nonblocking
exclusive `LockFileEx` lock. A competing process fails explicitly instead of
reusing a sequence. Closing an owned process releases its handles and locks.

The counter must contain canonical decimal digits and one newline. Empty,
malformed, oversized or exhausted state is preserved and rejected. Clock
rollback never decreases a valid saved value. A new counter is written to a
private same-directory temporary file and flushed before `MoveFileExW` replaces
the old name with write-through requested. The validated counter's existing
owner/DACL are copied to the replacement. There is no cross-volume copy fallback.
The lock stays held until the operation completes; a failed pre-replacement
flush or rename leaves the old counter untouched and removes the temporary file.

This provides restart-safe reservations using NTFS/Windows flush and rename
semantics. It is not an independently tested guarantee against arbitrary disk,
firmware or sudden-power-loss failure. A crash may leave a private temporary
file; it is not treated as a valid counter. New credential creation that fails
during writing can leave an incomplete new file; it fails closed on the next
read and is never silently overwritten. Recovery requires an explicit decision
by the account owner, not a counter reset or automatic reenrolment.

## Verification

From the repository root:

```text
python -m pytest tests/cloud/test_cloud_private_windows.py -q
```

The macOS authoring run used the existing isolated Python 3.12 environment:

```text
../AisleSignals-camera-release/.venv/bin/python -m pytest tests/cloud/test_cloud_private_windows.py -q
```

Result: **103 passed, 19 skipped**. The skipped cases need actual Windows NTFS:
private creation, preservation of occupied files and ACLs, unsafe ACL refusal,
hard links, junctions, held-directory rename prevention, refusal of an empty
ancestor granting another principal reparse-capable write access before creating
any child directory, write-handle exclusion while child creation remains possible,
bounded reads,
cross-process restart/locking, concurrent reservations, malformed ledgers and
injected flush/rename failures. All fixtures use disposable synthetic data.
Native tests must run before declaring Windows pairing supported; a successful
browser or API test cannot replace them.

## API sources

- [CreateFileW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-createfilew): exclusive creation, explicit security attributes, reparse-point opening and handle sharing.
- [GetSecurityInfo](https://learn.microsoft.com/en-us/windows/win32/api/aclapi/nf-aclapi-getsecurityinfo) and [file access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-security-and-access-rights): handle-based ownership and DACL inspection.
- [Windows security identifiers](https://learn.microsoft.com/en-us/windows-server/identity/ad-ds/manage/understand-security-identifiers): `OWNER RIGHTS` (`S-1-3-4`) represents the current object owner; it is resolved only after ownership validation.
- [NTFS sharing-check algorithm](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fsa/8c0e3f4f-0729-49f4-a14d-7f7add593819) and [file access rights](https://learn.microsoft.com/en-us/windows/win32/fileio/file-access-rights-constants): a held directory needs read-data/list access to participate in share accounting; attribute-only handles do not fence another writer or rename.
- [FSCTL_SET_REPARSE_POINT](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-fsa/4aeefef8-92c3-4abc-af7a-a610caf8a165): why ancestor `FILE_WRITE_DATA` and `FILE_WRITE_ATTRIBUTES` grants are rejected even with delete sharing disabled.
- [GetFileInformationByHandle](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfileinformationbyhandle) and [GetFinalPathNameByHandleW](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getfinalpathnamebyhandlew): opened-object identity, link count and final path.
- [LockFileEx](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-lockfileex): exclusive bounded lock acquisition and process/handle lifetime.
- [FlushFileBuffers](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-flushfilebuffers) and [MoveFileExW](https://learn.microsoft.com/en-us/windows/win32/api/winbase/nf-winbase-movefileexw): flush and same-volume replacement semantics.
