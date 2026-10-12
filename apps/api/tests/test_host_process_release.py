"""Exact-group release after birth-metadata short reads; no media/DB/lock."""
import errno
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest

from app.device_scheduler import ProcessIdentity
from app.host_resource_probes import HostProcessProbe


@pytest.mark.parametrize('failure',[OSError(errno.ESRCH,'birth race'),psutil.AccessDenied(123),RuntimeError('sysctl() size mismatch')])
def test_same_boot_birth_error_releases_only_when_registered_group_is_absent(monkeypatch,failure):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'exact-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    monkeypatch.setattr('app.host_resource_probes.sys.platform','darwin')
    def identity(pid):
        assert pid==child.pid
        raise failure
    calls=[]
    def absent(pgid,sig):
        calls.append((pgid,sig))
        raise ProcessLookupError(errno.ESRCH,'group absent')
    monkeypatch.setattr(probe,'identity',identity)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',absent)
    assert probe._state(child,group=True)=='released'
    assert calls==[(123,0)]


@pytest.mark.parametrize('group_failure',[None,PermissionError(errno.EPERM,'denied'),OSError(errno.EIO,'IO error'),OSError(errno.EINVAL,'invalid')])
def test_birth_error_with_existing_or_uncertain_group_remains_unknown(monkeypatch,group_failure):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'exact-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    def identity(pid):raise OSError(errno.ESRCH,'birth race')
    def group(pgid,sig):
        assert (pgid,sig)==(child.pid,0)
        if group_failure is not None:
            raise group_failure
    monkeypatch.setattr(probe,'identity',identity)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',group)
    assert probe.children_released((child,)) is None


@pytest.mark.parametrize('boot_failure',[None,OSError(errno.EIO,'boot unavailable')])
def test_unconfirmed_boot_never_uses_group_absence(monkeypatch,boot_failure):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'exact-start','same-boot')
    def boot():
        if boot_failure is not None:
            raise boot_failure
        return ''
    monkeypatch.setattr(probe,'_boot_id',boot)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',lambda *args:pytest.fail('boot must be confirmed first'))
    assert probe._state(child,group=True)=='unknown'


def test_known_pid_reuse_keeps_original_group_and_holder_rules(monkeypatch):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'old-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    monkeypatch.setattr(probe,'identity',lambda pid:ProcessIdentity(pid,'new-start','same-boot'))
    monkeypatch.setattr('app.host_resource_probes.os.killpg',lambda *args:pytest.fail('known PID reuse rule unchanged'))
    assert probe._state(child,group=True)=='unknown'
    assert probe._state(child,group=False)=='released'


def test_holder_metadata_error_does_not_probe_a_group(monkeypatch):
    probe=HostProcessProbe()
    holder=ProcessIdentity(123,'exact-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    def identity(pid):raise psutil.AccessDenied(pid)
    monkeypatch.setattr(probe,'identity',identity)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',lambda *args:pytest.fail('holder is not a registered child group'))
    assert probe._state(holder,group=False)=='unknown'


def test_unrelated_runtime_error_still_propagates(monkeypatch):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'exact-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    def identity(pid):raise RuntimeError('unrelated implementation error')
    monkeypatch.setattr(probe,'identity',identity)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',lambda *args:pytest.fail('not a birth short read'))
    with pytest.raises(RuntimeError,match='unrelated'):
        probe._state(child,group=True)


def test_real_python_group_reaped_after_short_read_is_released(monkeypatch):
    probe=HostProcessProbe()
    process=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.25)'],start_new_session=True,
        stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        child=probe.identity(process.pid)
        assert os.getpgid(child.pid)==child.pid
        def identity(pid):raise OSError(errno.ESRCH,'injected birth short read')
        monkeypatch.setattr(probe,'identity',identity)
        assert probe.children_released((child,)) is None
        process.wait(timeout=2)
        assert probe.children_released((child,)) is True
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)


def test_real_surviving_grandchild_group_blocks_release(monkeypatch):
    # The leader exits while its Python descendant keeps the registered PGID.
    code="import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(.5)']); print(p.pid,flush=True)"
    leader=subprocess.Popen([sys.executable,'-c',code],start_new_session=True,
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True)
    probe=HostProcessProbe()
    try:
        child=probe.identity(leader.pid)
        descendant=int(leader.stdout.readline().strip())
        assert os.getpgid(descendant)==child.pid
        leader.wait(timeout=2)
        def identity(pid):raise OSError(errno.ESRCH,'leader birth metadata unavailable')
        monkeypatch.setattr(probe,'identity',identity)
        assert probe.children_released((child,)) is None
        # No signal to unrelated/reused processes: only this test-created group.
        os.killpg(child.pid,signal.SIGTERM)
        deadline=time.monotonic()+2
        while probe.children_released((child,)) is not True and time.monotonic()<deadline:
            time.sleep(.01)
        assert probe.children_released((child,)) is True
    finally:
        if leader.stdout is not None:
            leader.stdout.close()
        if leader.poll() is None:
            leader.terminate()
            leader.wait(timeout=2)
        try:
            os.killpg(leader.pid,signal.SIGTERM)
        except ProcessLookupError:
            pass


@pytest.mark.parametrize('group,platform',[(False,'darwin'),(True,'linux')])
def test_short_read_runtime_error_outside_mac_child_group_rule_is_unchanged(monkeypatch,group,platform):
    probe=HostProcessProbe()
    child=ProcessIdentity(123,'exact-start','same-boot')
    monkeypatch.setattr(probe,'_boot_id',lambda:'same-boot')
    monkeypatch.setattr('app.host_resource_probes.sys.platform',platform)
    def identity(pid):raise RuntimeError('sysctl() size mismatch')
    monkeypatch.setattr(probe,'identity',identity)
    monkeypatch.setattr('app.host_resource_probes.os.killpg',lambda *args:pytest.fail('not the narrow macOS child-group rule'))
    with pytest.raises(RuntimeError,match='size mismatch'):
        probe._state(child,group=group)
