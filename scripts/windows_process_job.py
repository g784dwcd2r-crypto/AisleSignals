"""Windows 10/11 owned children, atomically contained before their first code runs.

The unnamed, non-inheritable job dies with its launcher. JOB_LIST at process
creation avoids the uncontained Popen-to-AssignProcessToJobObject interval.
No existing PID is opened, adopted or assigned to the job.

Microsoft contracts:
https://learn.microsoft.com/windows/win32/api/processthreadsapi/nf-processthreadsapi-updateprocthreadattribute
https://learn.microsoft.com/windows/win32/api/winnt/ns-winnt-jobobject_basic_limit_information
"""

import ctypes as C
import math
import os
import subprocess
import threading

DWORD = C.c_uint32
WORD = C.c_uint16
BOOL = C.c_int32
HANDLE = C.c_void_p
SIZE_T = C.c_size_t
WAIT_OBJECT_0 = 0
WAIT_TIMEOUT = 258
INFINITE = 0xFFFFFFFF
JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
PROC_THREAD_ATTRIBUTE_JOB_LIST = 0x0002000D
EXTENDED_STARTUPINFO_PRESENT = 0x00080000
CREATE_UNICODE_ENVIRONMENT = 0x00000400
CREATE_NO_WINDOW = 0x08000000


class BasicLimits(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_int64), ("PerJobUserTimeLimit", C.c_int64),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T), ("ActiveProcessLimit", DWORD),
                ("Affinity", SIZE_T), ("PriorityClass", DWORD), ("SchedulingClass", DWORD)]


class IOCounters(C.Structure):
    _fields_ = [(name, C.c_uint64) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class ExtendedLimits(C.Structure):
    _fields_ = [("BasicLimitInformation", BasicLimits), ("IoInfo", IOCounters),
                ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class StartupInfo(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", C.c_wchar_p), ("lpDesktop", C.c_wchar_p),
                ("lpTitle", C.c_wchar_p), ("dwX", DWORD), ("dwY", DWORD),
                ("dwXSize", DWORD), ("dwYSize", DWORD), ("dwXCountChars", DWORD),
                ("dwYCountChars", DWORD), ("dwFillAttribute", DWORD), ("dwFlags", DWORD),
                ("wShowWindow", WORD), ("cbReserved2", WORD), ("lpReserved2", C.c_void_p),
                ("hStdInput", HANDLE), ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class StartupInfoEx(C.Structure):
    _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", C.c_void_p)]


class ProcessInformation(C.Structure):
    _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", DWORD), ("dwThreadId", DWORD)]


def kernel_api():
    if os.name != "nt":
        raise OSError("Windows job containment is available only on Windows 10 or newer.")
    api = C.WinDLL("kernel32", use_last_error=True)
    signatures = {
        "CreateJobObjectW": ([C.c_void_p, C.c_wchar_p], HANDLE),
        "SetInformationJobObject": ([HANDLE, C.c_int, C.c_void_p, DWORD], BOOL),
        "SetHandleInformation": ([HANDLE, DWORD, DWORD], BOOL),
        "CloseHandle": ([HANDLE], BOOL),
        "InitializeProcThreadAttributeList": ([C.c_void_p, DWORD, DWORD, C.POINTER(SIZE_T)], BOOL),
        "UpdateProcThreadAttribute": ([C.c_void_p, DWORD, SIZE_T, C.c_void_p, SIZE_T, C.c_void_p, C.c_void_p], BOOL),
        "DeleteProcThreadAttributeList": ([C.c_void_p], None),
        "CreateProcessW": ([C.c_wchar_p, C.c_wchar_p, C.c_void_p, C.c_void_p, BOOL, DWORD,
                            C.c_void_p, C.c_wchar_p, C.POINTER(StartupInfoEx), C.POINTER(ProcessInformation)], BOOL),
        "WaitForSingleObject": ([HANDLE, DWORD], DWORD),
        "GetExitCodeProcess": ([HANDLE, C.POINTER(DWORD)], BOOL),
        "TerminateProcess": ([HANDLE, C.c_uint], BOOL),
    }
    for name, (arguments, result) in signatures.items():
        function = getattr(api, name)
        function.argtypes, function.restype = arguments, result
    return api


def checked(value):
    if not value:
        raise C.WinError(C.get_last_error())
    return value


class WindowsChild:
    """The Popen lifecycle subset needed by the supervisor, using owned handles."""

    def __init__(self, api, handle, pid, args):
        self.api, self.handle, self.pid, self.args = api, handle, pid, args
        self.returncode = None

    def poll(self):
        if self.returncode is None:
            result = self.api.WaitForSingleObject(self.handle, 0)
            if result == WAIT_TIMEOUT:
                return None
            if result != WAIT_OBJECT_0:
                raise C.WinError(C.get_last_error())
            code = DWORD()
            checked(self.api.GetExitCodeProcess(self.handle, C.byref(code)))
            checked(self.api.CloseHandle(self.handle))
            self.handle, self.returncode = None, code.value
        return self.returncode

    def wait(self, timeout=None):
        if self.poll() is not None:
            return self.returncode
        milliseconds = INFINITE if timeout is None else min(INFINITE - 1, max(0, math.ceil(timeout * 1000)))
        result = self.api.WaitForSingleObject(self.handle, milliseconds)
        if result == WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired(self.args, timeout)
        if result != WAIT_OBJECT_0:
            raise C.WinError(C.get_last_error())
        return self.poll()

    def terminate(self):
        if self.poll() is None and not self.api.TerminateProcess(self.handle, 1):
            error = C.get_last_error()
            if self.poll() is None:
                raise C.WinError(error)

    kill = terminate

    def __del__(self):
        if getattr(self, "handle", None):
            self.api.CloseHandle(self.handle)
            self.handle = None


class WindowsProcessJob:
    def __init__(self):
        self.api = kernel_api()
        self.handle = checked(self.api.CreateJobObjectW(None, None))
        self._lock = threading.RLock()
        try:
            checked(self.api.SetHandleInformation(self.handle, 1, 0))
            limits = ExtendedLimits()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            checked(self.api.SetInformationJobObject(self.handle, 9, C.byref(limits), C.sizeof(limits)))
        except BaseException:
            self.api.CloseHandle(self.handle)
            self.handle = None
            raise

    def spawn(self, command, *, cwd, env):
        """Atomically create a process in this job; fail without adopting a PID."""
        if not command or any(not isinstance(arg, str) or "\0" in arg for arg in command):
            raise ValueError("Use a nonempty list of valid process arguments.")
        if any(not isinstance(k, str) or not isinstance(v, str) or "\0" in k + v for k, v in env.items()):
            raise ValueError("Use valid Unicode environment entries.")
        if not os.path.isabs(command[0]):
            raise ValueError("An absolute owned executable path is required.")
        with self._lock:
            if self.handle is None:
                raise OSError("The Windows process job is already closed.")
            size = SIZE_T()
            self.api.InitializeProcThreadAttributeList(None, 1, 0, C.byref(size))
            if not size.value:
                raise C.WinError(C.get_last_error())
            attributes = C.create_string_buffer(size.value)
            checked(self.api.InitializeProcThreadAttributeList(attributes, 1, 0, C.byref(size)))
            information = ProcessInformation()
            try:
                jobs = (HANDLE * 1)(self.handle)
                checked(self.api.UpdateProcThreadAttribute(attributes, 0, PROC_THREAD_ATTRIBUTE_JOB_LIST,
                                                          C.byref(jobs), C.sizeof(jobs), None, None))
                startup = StartupInfoEx()
                startup.StartupInfo.cb = C.sizeof(startup)
                startup.lpAttributeList = C.cast(attributes, C.c_void_p)
                command_line = C.create_unicode_buffer(subprocess.list2cmdline(command))
                environment = C.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + "\0\0")
                flags = EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW
                checked(self.api.CreateProcessW(command[0], command_line, None, None, False, flags,
                                                environment, str(cwd), C.byref(startup), C.byref(information)))
                checked(self.api.CloseHandle(information.hThread))
                information.hThread = None
                child = WindowsChild(self.api, information.hProcess, information.dwProcessId, command)
                information.hProcess = None
                return child
            except BaseException:
                if information.hProcess:
                    # Creation/return failed after Windows gave us an owned
                    # handle. Stop that child only; never recover by PID lookup.
                    result = self.api.WaitForSingleObject(information.hProcess, 0)
                    if result != WAIT_OBJECT_0:
                        checked(self.api.TerminateProcess(information.hProcess, 1))
                        if self.api.WaitForSingleObject(information.hProcess, 5000) != WAIT_OBJECT_0:
                            raise OSError("The failed owned Windows child did not stop.")
                raise
            finally:
                if information.hThread:
                    checked(self.api.CloseHandle(information.hThread))
                if information.hProcess:
                    checked(self.api.CloseHandle(information.hProcess))
                self.api.DeleteProcThreadAttributeList(attributes)

    def close(self):
        with self._lock:
            if self.handle is not None:
                checked(self.api.CloseHandle(self.handle))
                self.handle = None

    def __del__(self):
        if getattr(self, "handle", None):
            self.api.CloseHandle(self.handle)
            self.handle = None
