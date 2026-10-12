"""Real macOS Seatbelt probes on synthetic data. No model or production policy."""

import errno
import hashlib
import json
import os
import selectors
import socket
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path

import PIL
import pytest

from app.media_script import (
    PngOutputContract, ScriptExecutionLimits, ScriptInputRevision, ScriptToolchain,
    prepare_media_script, seal_media_script, verify_media_script_outputs,
)
from app.granted_subprocess import _terminate_and_verify
from app.host_resource_probes import HostProcessProbe


@pytest.fixture(scope="module")
def toolchain():
    return ScriptToolchain.from_paths(python=Path(sys.executable),
        stdlib=Path(sysconfig.get_path("stdlib")),
        python_library=Path(sys.base_prefix) / "lib" / "libpython3.12.dylib",
        pillow=Path(PIL.__file__).parent, version=f"Python {sys.version.split()[0]}; Pillow {PIL.__version__}")


@pytest.fixture
def prepare(tmp_path, toolchain):
    source = tmp_path / "input.png"
    from PIL import Image
    Image.new("RGBA", (4, 4), "blue").save(source)
    count = 0

    def build(code, parameters=None):
        nonlocal count
        count += 1
        sealed = seal_media_script(script_source=code, language="python", parameters=parameters or {},
            input_revisions=(ScriptInputRevision("input.png", "r1", hashlib.sha256(source.read_bytes()).hexdigest()),),
            requested_outputs=(PngOutputContract("result.png", 4096, 32, 32),), toolchain=toolchain)
        return prepare_media_script(sealed=sealed, input_paths={"input.png": source},
            job_directory=tmp_path / f"job-{count}",
            limits=ScriptExecutionLimits(5, 4096, 4096, 4096))
    return build


def execute(prepared):
    # QA mechanism only: no fake scheduler/grant/policy and no production claim.
    return subprocess.run(prepared.argv, env=prepared.env, stdin=subprocess.DEVNULL,
        capture_output=True, start_new_session=True, timeout=5)


pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS Seatbelt evidence only")


@pytest.fixture
def socket_directory():
    # AF_UNIX on macOS has a 104-byte path limit.
    with tempfile.TemporaryDirectory(prefix="ms-", dir="/tmp") as directory:
        yield Path(directory)


def test_real_png_algorithm_and_session_identity(prepare):
    prepared = prepare("""import json, os
from PIL import Image, ImageDraw
image = Image.open(inputs['input.png']).resize((16, 16))
ImageDraw.Draw(image).rectangle((2,2,8,8), fill='red')
ImageDraw.Draw(image).text((0,0),'x',fill='white')
image.save(outputs['result.png'])
print(json.dumps({'pid':os.getpid(), 'sid':os.getsid(0), 'pgid':os.getpgrp()}))
""")
    result = execute(prepared)
    assert result.returncode == 0, result.stderr.decode()
    identity = json.loads(result.stdout)
    assert identity["pid"] == identity["sid"] == identity["pgid"]
    output, = verify_media_script_outputs(prepared)
    assert (output.width, output.height) == (16, 16)
    assert output.bytes < 4096 and len(output.sha256) == 64


def test_filesystem_denials_and_symlink_escape(prepare, tmp_path):
    outside = tmp_path / "other-job"
    outside.mkdir()
    fake_secret = outside / "fake.env"
    fake_secret.write_text("synthetic-secret")
    prepared = prepare("""import json, os
denied=[]
for name, path, mode in [('other-read',parameters['other'],'r'),
                         ('other-write',parameters['other'],'w'),
                         ('input-write',inputs['input.png'],'w'),
                         ('script-write',__file__,'w'),
                         ('unrequested-output',outputs['result.png']+'.extra','w'),
                         ('parent-write',parameters['escape'],'w'),
                         ('symlink-read',outputs['result.png'],'r'),
                         ('symlink-write',outputs['result.png'],'w')]:
    try:
        with open(path,mode) as file:
            file.read() if mode=='r' else file.write('bad')
    except OSError as e:
        denied.append([name,e.errno])
print(json.dumps(denied))
""", {"other": str(fake_secret), "escape": str(tmp_path / "escape.png")})
    (prepared.directory / "out" / "result.png").symlink_to(fake_secret)
    result = execute(prepared)
    assert result.returncode == 0, result.stderr.decode()
    denials = json.loads(result.stdout)
    assert len(denials) == 8
    assert all(code in {errno.EPERM, errno.EACCES} for _, code in denials)
    assert fake_secret.read_text() == "synthetic-secret"
    assert not (tmp_path / "escape.png").exists()


@pytest.mark.parametrize("family,kind", [(socket.AF_INET,socket.SOCK_STREAM),
    (socket.AF_INET6,socket.SOCK_STREAM), (socket.AF_UNIX,socket.SOCK_STREAM),
    (socket.AF_INET,socket.SOCK_DGRAM)])
def test_real_network_denial_with_reachable_control(prepare, socket_directory, family, kind):
    with socket.socket(family, kind) as listener:
        address = str(socket_directory / "c.sock") if family == socket.AF_UNIX else (("::1",0) if family == socket.AF_INET6 else ("127.0.0.1",0))
        listener.bind(address)
        if kind == socket.SOCK_STREAM:
            listener.listen(2)
        target = listener.getsockname()
        with socket.socket(family, kind) as control:
            control.connect(target)
            if kind == socket.SOCK_STREAM:
                conn, _ = listener.accept()
                conn.close()
            else:
                control.send(b"control")
                assert listener.recv(32) == b"control"
        prepared = prepare("""import json, socket
try:
    with socket.socket(parameters['family'],parameters['kind']) as sock:
        address=parameters['address']
        sock.connect(tuple(address) if isinstance(address,list) else address)
        if parameters['kind']==socket.SOCK_DGRAM: sock.send(b'bad')
except OSError as e:
    print(json.dumps({'errno':e.errno}))
""", {"family": int(family), "kind": int(kind), "address": target})
        result = execute(prepared)
        assert result.returncode == 0, result.stderr.decode()
        assert json.loads(result.stdout)["errno"] in {errno.EPERM, errno.EACCES}
        listener.settimeout(0.05)
        with pytest.raises(socket.timeout):
            listener.accept() if kind == socket.SOCK_STREAM else listener.recv(32)


def test_os_denies_fork_exec_and_session_group_escape(prepare):
    prepared = prepare("""import json, os, subprocess
denials=[]
# Fork is attempted only under Seatbelt; an unexpected child exits immediately.
try:
    pid=os.fork()
    if pid==0: os._exit(91)
    os.waitpid(pid,0)
except OSError as e: denials.append(['fork',e.errno])
for name, function in [('subprocess',lambda:subprocess.run(['/usr/bin/true'],check=True)),
                       ('spawn',lambda:os.posix_spawn('/usr/bin/true',['true'],{})),
                       ('exec-true',lambda:os.execv('/usr/bin/true',['true'])),
                       ('exec-python',lambda:os.execv(__import__('sys').executable,[__import__('sys').executable,'-c',"print('escape')"])),
                       ('setsid',os.setsid),('setpgid',lambda:os.setpgid(0,0))]:
    try: function()
    except OSError as e: denials.append([name,e.errno])
print(json.dumps({'denials':denials,'pid':os.getpid(),'sid':os.getsid(0),'pgid':os.getpgrp()}))
""")
    result = execute(prepared)
    assert result.returncode == 0, result.stderr.decode()
    data = json.loads(result.stdout)
    assert {name for name, _ in data["denials"]} == {"fork", "subprocess", "spawn", "exec-true", "exec-python", "setsid", "setpgid"}
    assert all(code in {errno.EPERM,errno.EACCES} for _, code in data["denials"])
    assert data["pid"] == data["sid"] == data["pgid"]


def test_environment_tmp_and_input_alias_are_restricted(prepare, monkeypatch):
    monkeypatch.setenv("FAKE_PRIVATE_TOKEN", "synthetic-only")
    prepared = prepare("""import ctypes,json,os
denials=[]
try:
    with open(os.environ['TMPDIR']+'/scratch','wb') as file: file.write(b'no')
except OSError as e: denials.append(['tmp',e.errno])
try:
    os.link(inputs['input.png'],outputs['result.png'])
except OSError as e: denials.append(['input-link',e.errno])
lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
size=ctypes.c_size_t(8)
value=ctypes.c_uint64()
status=lib.sysctlbyname(b'hw.memsize',ctypes.byref(value),ctypes.byref(size),None,0)
print(json.dumps({'denials':denials,'environment':dict(os.environ),
                  'sysctl_status':status,'sysctl_errno':ctypes.get_errno()}))
""")
    result = execute(prepared)
    assert result.returncode == 0, result.stderr.decode()
    data = json.loads(result.stdout)
    assert {name for name, _ in data["denials"]} == {"tmp", "input-link"}
    assert all(code in {errno.EPERM, errno.EACCES} for _,code in data["denials"]), data
    # CoreFoundation may insert this public locale/UID encoding marker when
    # ctypes loads libSystem. It is not inherited from the parent environment.
    assert set(data["environment"]) <= {"TMPDIR", "LC_ALL", "LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert "FAKE_PRIVATE_TOKEN" not in data["environment"]
    # macOS may permit public hardware queries intrinsically; no broad
    # sysctl-read permission is added. Private procargs are probed separately.
    item, = prepared.sealed.input_revisions
    assert hashlib.sha256((prepared.directory/'inputs'/item.name).read_bytes()).hexdigest() == item.sha256


def test_private_procargs_are_denied_on_synthetic_process(prepare):
    control = subprocess.Popen([sys.executable,'-I','-B','-S','-c','import time; time.sleep(5)'],
        env={'FAKE_PRIVATE_TOKEN':'synthetic-only'}, stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        prepared = prepare("""import ctypes,json
lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
mib=(ctypes.c_int*3)(1,49,parameters['pid'])
length=ctypes.c_size_t(65536)
buffer=ctypes.create_string_buffer(65536)
status=lib.sysctl(mib,3,buffer,ctypes.byref(length),None,0)
print(json.dumps({'status':status,'errno':ctypes.get_errno()}))
""", {'pid':control.pid})
        result = execute(prepared)
        assert result.returncode == 0, result.stderr.decode()
        data = json.loads(result.stdout)
        print('synthetic procargs probe:', data)
        assert data['status'] == -1 and data['errno'] in {errno.EPERM,errno.EACCES}, data
    finally:
        control.terminate()
        control.wait(timeout=2)


@pytest.mark.parametrize('termination',['cancel','timeout'])
def test_registered_group_release_on_cancel_and_timeout(prepare, termination):
    prepared = prepare("import os,time; print(os.getpid(),flush=True); time.sleep(5)")
    process = subprocess.Popen(prepared.argv,env=prepared.env,stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
    probe = HostProcessProbe()
    identity = probe.identity(process.pid)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout,selectors.EVENT_READ)
            assert selector.select(2), 'worker did not reach isolated code'
            assert int(process.stdout.readline()) == process.pid
        if termination == 'timeout':
            with pytest.raises(subprocess.TimeoutExpired):
                process.wait(timeout=0.03)
        _terminate_and_verify(process,identity,probe)
        assert probe.children_released((identity,)) is True
        with pytest.raises(ProcessLookupError):
            os.killpg(process.pid,0)
    finally:
        _terminate_and_verify(process,identity,probe)
        process.stdout.close()
        process.stderr.close()


def test_single_output_fsize_is_enforced_by_os(prepare):
    prepared = prepare("""import json
try:
    with open(outputs['result.png'],'wb',buffering=0) as file:
        for i in range(8): file.write(b'x'*1024)
except OSError as e: print(json.dumps({'errno':e.errno}))
""")
    result = execute(prepared)
    assert result.returncode == 0, result.stderr.decode()
    assert json.loads(result.stdout)['errno'] == errno.EFBIG
    assert (prepared.directory/'out'/'result.png').stat().st_size <= 4096
