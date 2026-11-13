"""Windows text-pipe process owned by a non-inherited, kill-on-close Job Object.

CreateProcessW is used for STARTUPINFOEX's explicit handle list. pywin32 owns
job operations. The primary thread stays suspended until job assignment.
"""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import io
import math
import os
import subprocess
import threading
import time
import weakref


def _create_process(command, environment, cwd, handles):
    if any("\0" in argument for argument in command) or "\0" in os.fspath(cwd):
        raise ValueError("Embedded null byte in child command or directory")
    if any(not key or "=" in key[1:] or "\0" in key or "\0" in value for key, value in environment.items()):
        raise ValueError("Invalid child environment")
    class StartupInfo(ctypes.Structure):
        _fields_ = [("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR),
                    ("lpDesktop", wintypes.LPWSTR), ("lpTitle", wintypes.LPWSTR),
                    *[(name, wintypes.DWORD) for name in (
                        "dwX", "dwY", "dwXSize", "dwYSize", "dwXCountChars",
                        "dwYCountChars", "dwFillAttribute", "dwFlags")],
                    ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
                    ("lpReserved2", ctypes.c_void_p),
                    *[(name, wintypes.HANDLE) for name in ("hStdInput", "hStdOutput", "hStdError")]]

    class StartupInfoEx(ctypes.Structure):
        _fields_ = [("StartupInfo", StartupInfo), ("lpAttributeList", ctypes.c_void_p)]

    class ProcessInfo(ctypes.Structure):
        _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                    ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t,
                                               ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel.DeleteProcThreadAttributeList.restype = None
    kernel.CreateProcessW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
                                     wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
                                     ctypes.POINTER(StartupInfoEx), ctypes.POINTER(ProcessInfo)]
    kernel.CreateProcessW.restype = wintypes.BOOL
    size = ctypes.c_size_t()
    kernel.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
    if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER: size query.
        raise ctypes.WinError(ctypes.get_last_error())
    attributes = ctypes.create_string_buffer(size.value)
    if not kernel.InitializeProcThreadAttributeList(attributes, 1, 0, ctypes.byref(size)):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        inherited = (wintypes.HANDLE * len(handles))(*handles)
        if not kernel.UpdateProcThreadAttribute(attributes, 0, 0x20002, inherited, ctypes.sizeof(inherited), None, None):
            raise ctypes.WinError(ctypes.get_last_error())
        startup = StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100  # STARTF_USESTDHANDLES
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = handles
        startup.lpAttributeList = ctypes.cast(attributes, ctypes.c_void_p)
        info = ProcessInfo()
        env = ctypes.create_unicode_buffer("\0".join(f"{key}={value}" for key, value in sorted(environment.items(), key=lambda item: item[0].upper())) + "\0\0")
        command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(command))
        # EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT |
        # CREATE_SUSPENDED | CREATE_NO_WINDOW. No shell is involved.
        if not kernel.CreateProcessW(None, command_line, None, None, True, 0x08080404,
                                     env, os.fspath(cwd), ctypes.byref(startup), ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        return info.hProcess, info.hThread, info.dwProcessId
    finally:
        kernel.DeleteProcThreadAttributeList(attributes)


def _close_handles(job, process):
    import win32api

    job.Close()
    win32api.CloseHandle(process)


class WindowsJobProcess:
    """The pipe/poll/wait interface consumed by the existing assistant runner."""

    def __init__(self, command, *, env, cwd):
        import msvcrt
        import win32api
        import win32job
        import win32process

        self.args = command
        self.returncode = None
        self._owner = os.getpid()
        self._lock = threading.RLock()
        self._closed = False
        self._job = win32job.CreateJobObject(None, "")
        process = thread = None
        try:
            limits = win32job.QueryInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation)
            limits["BasicLimitInformation"]["LimitFlags"] = win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            win32job.SetInformationJobObject(self._job, win32job.JobObjectExtendedLimitInformation, limits)
            with ExitStack() as cleanup:
                pipes = [tuple(cleanup.enter_context(os.fdopen(fd, mode, buffering=0))
                               for fd, mode in zip(os.pipe(), ("rb", "wb"))) for _ in range(3)]
                child_streams = (pipes[0][0], pipes[1][1], pipes[2][1])
                handles = [msvcrt.get_osfhandle(stream.fileno()) for stream in child_streams]
                for handle in handles:
                    os.set_handle_inheritable(handle, True)
                try:
                    process, thread, self.pid = _create_process(command, env, cwd, handles)
                finally:
                    for handle in handles:
                        os.set_handle_inheritable(handle, False)
                win32job.AssignProcessToJobObject(self._job, process)
                self._process = process
                self.stdin = io.TextIOWrapper(pipes[0][1], encoding="utf-8", write_through=True)
                self.stdout = io.TextIOWrapper(pipes[1][0], encoding="utf-8")
                self.stderr = io.TextIOWrapper(pipes[2][0], encoding="utf-8")
                for stream in child_streams:
                    stream.close()
                win32process.ResumeThread(thread)
                cleanup.pop_all()
            self._finalizer = weakref.finalize(self, _close_handles, self._job, process)
        except BaseException:
            if process is not None:
                win32process.TerminateProcess(process, 1)
                win32api.CloseHandle(process)
            self._job.Close()
            raise
        finally:
            if thread is not None:
                win32api.CloseHandle(thread)

    def assert_owned(self):
        if self._owner != os.getpid():
            raise RuntimeError("Assistant job belongs to a different host process")

    def poll(self):
        import win32event
        import win32process

        with self._lock:
            if self.returncode is None and win32event.WaitForSingleObject(self._process, 0) == win32event.WAIT_OBJECT_0:
                self.returncode = win32process.GetExitCodeProcess(self._process)
            return self.returncode

    def wait(self, timeout=None):
        import win32event

        if self.poll() is None:
            milliseconds = win32event.INFINITE if timeout is None else max(0, math.ceil(timeout * 1000))
            if win32event.WaitForSingleObject(self._process, milliseconds) == win32event.WAIT_TIMEOUT:
                raise subprocess.TimeoutExpired(self.args, timeout)
        return self.poll()

    def group_released(self):
        import win32job

        self.assert_owned()
        with self._lock:
            return self._closed or win32job.QueryInformationJobObject(
                self._job, win32job.JobObjectBasicAccountingInformation)["ActiveProcesses"] == 0

    def terminate_group(self, deadline):
        import win32job

        self.assert_owned()
        with self._lock:
            if not self.group_released():
                win32job.TerminateJobObject(self._job, 1)
            while not self.group_released():
                if time.monotonic() >= deadline:
                    raise RuntimeError("Assistant job still contains active processes")
                time.sleep(min(.01, max(0, deadline - time.monotonic())))
            self.wait(timeout=max(0, deadline - time.monotonic()))

    def close(self):
        with self._lock:
            if self._closed:
                return
            if not self.group_released():
                raise RuntimeError("Assistant job release has not been proved")
            self.wait(timeout=0)
            for stream in (self.stdin, self.stdout, self.stderr):
                stream.close()
            self._finalizer()
            self._closed = True
