"""OS file locks and durable replacement used by the existing local stores."""
from __future__ import annotations

import errno
import os


def lock_file(descriptor: int, *, shared: bool = False, blocking: bool = False) -> None:
    if os.name == "nt":
        import msvcrt
        import pywintypes
        import win32con
        import win32file

        flags = 0 if shared else win32con.LOCKFILE_EXCLUSIVE_LOCK
        if not blocking:
            flags |= win32con.LOCKFILE_FAIL_IMMEDIATELY
        try:
            win32file.LockFileEx(msvcrt.get_osfhandle(descriptor), flags, -1, -1, pywintypes.OVERLAPPED())
        except pywintypes.error as exc:
            if exc.winerror == 33:  # ERROR_LOCK_VIOLATION
                raise BlockingIOError(errno.EWOULDBLOCK, "File lock is held") from exc
            import ctypes

            raise ctypes.WinError(exc.winerror) from exc
    else:
        import fcntl

        flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        fcntl.flock(descriptor, flags)


def unlock_file(descriptor: int) -> None:
    if os.name == "nt":
        import msvcrt
        import pywintypes
        import win32file

        win32file.UnlockFileEx(msvcrt.get_osfhandle(descriptor), -1, -1, pywintypes.OVERLAPPED())
    else:
        import fcntl

        fcntl.flock(descriptor, fcntl.LOCK_UN)


def durable_replace(source, destination) -> None:
    """Publish a flushed file; preserve failure rather than omit durability."""
    if os.name == "nt":
        import ctypes
        import pywintypes
        import win32file

        try:
            win32file.MoveFileEx(
                os.fspath(source), os.fspath(destination),
                win32file.MOVEFILE_REPLACE_EXISTING | win32file.MOVEFILE_WRITE_THROUGH,
            )
        except pywintypes.error as exc:
            raise ctypes.WinError(exc.winerror) from exc
    else:
        os.replace(source, destination)
        directory = os.open(os.path.dirname(os.path.abspath(destination)), os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
