"""Stable application execution identity, independent of workspace/profile paths.

The current contract coordinates processes of the same operating-system account.
It does not claim mutual exclusion across separate OS accounts.
"""
from pathlib import Path
import json
import os
import sys

APPLICATION_ID = 'com.aivideodiary.studio'
LOCK_FILENAME = 'device-permit.lock'


def application_data_root() -> Path:
    if os.name == 'posix':
        import pwd
        home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    else:
        home = Path.home()
    if sys.platform == 'darwin':
        return home / 'Library' / 'Application Support' / APPLICATION_ID
    if sys.platform == 'win32':
        return home / 'AppData' / 'Local' / APPLICATION_ID
    return home / '.local' / 'share' / APPLICATION_ID


def device_lock_path() -> Path:
    return application_data_root().resolve() / LOCK_FILENAME


def require_legacy_locks_released(paths) -> None:
    """Refuse migration over any known held/unknown old permit; never recover it."""
    import fcntl
    destination = device_lock_path()
    for raw in set(Path(path).expanduser().resolve() for path in paths):
        if raw == destination:
            continue
        descriptor = None
        try:
            if raw.exists():
                descriptor = os.open(raw, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise RuntimeError('旧设备许可仍被持有；请先正常释放原运行时，不能切换锁路径绕过。') from exc
            ledger = Path(str(raw) + '.state')
            if ledger.exists() and json.loads(ledger.read_bytes()).get('state') != 'free':
                raise RuntimeError('旧设备许可记录尚未确认释放；请沿原许可恢复核查，不能切换锁路径。')
        finally:
            if descriptor is not None:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
                os.close(descriptor)
