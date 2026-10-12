"""Durable script-resource binding and bounded dispatch, without running a policy fixture."""

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import media_script
from app.device_scheduler import Grant
from app.granted_subprocess import GrantedCommandCancelled, GrantedCommandTimeout
from app.host_resource_probes import HostBudget, HostResourceProbe, MediaScriptBudget
from app.media_script import (
    MediaScriptError, PngOutputContract, ScriptExecutionLimits, ScriptInputRevision,
    ScriptToolchain, media_script_resource_facts, seal_media_script,
)


@pytest.fixture
def binding(tmp_path):
    # No interpreter launch: this is a resource/dispatch contract fixture.
    root = tmp_path/'job'
    (root/'inputs').mkdir(parents=True)
    source = root/'inputs'/'input.png'
    source.write_bytes(b'synthetic-input')
    sealed = seal_media_script(script_source='print(parameters)',language='python',parameters={'x':1},
        input_revisions=(ScriptInputRevision('input.png','r1',hashlib.sha256(source.read_bytes()).hexdigest()),),
        requested_outputs=(PngOutputContract('out.png',512,16,16),),
        toolchain=ScriptToolchain(Path('/missing/python'),Path('/missing/lib'),Path('/missing/libpython'),
                                  Path('/missing/PIL'),'fixture','a'*64))
    prepared = SimpleNamespace(sealed=sealed,directory=root,argv=('never-launch',),env={},
        limits=ScriptExecutionLimits(.5,50,60,1024))
    facts = media_script_resource_facts(sealed=sealed, limits=prepared.limits,
                                       input_sizes={'input.png':source.stat().st_size}, threads=1)
    database=tmp_path/'permit.sqlite'
    grant=Grant('request','workspace','creation_job','job','task_media:whole-job','b'*64,1,1,'token',database)
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE TABLE device_work_requests (id TEXT,workspace_id TEXT,owner_type TEXT,owner_id TEXT,unit_key TEXT,input_digest TEXT,attempt_number INTEGER,last_grant_generation INTEGER,state TEXT,resources_json TEXT)')
        connection.execute('INSERT INTO device_work_requests VALUES (?,?,?,?,?,?,?,?,?,?)',
            (grant.request_id,grant.workspace_id,grant.owner_type,grant.owner_id,grant.unit_key,
             grant.input_digest,1,1,'granted',json.dumps(facts)))
    budget=MediaScriptBudget(512,256,2,1024,2,32,32,2048,4096,1024,1024,1,2,2048,4,.1,4096,8192)
    probe=HostResourceProbe(database,disk_paths=(tmp_path,),budget=HostBudget(50,100,100,100,1000,1000,media_script=budget))
    checks=[]
    def assert_grant(current):
        assert current is grant
        checks.append(current.input_digest)
    return prepared,grant,probe,SimpleNamespace(resources=probe,assert_grant=assert_grant),checks


def test_durable_script_digest_is_distinct_from_whole_job_and_identity_is_preserved(binding,monkeypatch):
    prepared,grant,probe,scheduler,checks=binding
    assert grant.input_digest != prepared.sealed.digest
    assert probe._grant_resources(grant)['script_digest']==prepared.sealed.digest
    assert probe._grant_resources(replace(grant,generation=2)) is None
    assert probe._grant_resources(replace(grant,input_digest='c'*64)) is None
    assert probe._grant_resources(replace(grant,workspace_id='foreign')) is None
    monkeypatch.setattr(media_script,'validate_prepared_media_script',lambda value:None)
    calls=[]
    monkeypatch.setattr(media_script,'run_granted_command',lambda *a,**kw:
        calls.append((a,kw)) or SimpleNamespace(returncode=0))
    monkeypatch.setattr(media_script,'verify_media_script_outputs',lambda value:('measured',))
    assert media_script.run_media_script(prepared=prepared,scheduler=scheduler,grant=grant)==('measured',)
    assert checks==[grant.input_digest]
    (args,kwargs),=calls
    assert args[0] is scheduler and args[1] is grant
    assert kwargs['timeout_seconds']==.5 and kwargs['max_stdout_bytes']==50
    assert kwargs['max_stderr_bytes']==60 and kwargs['max_output_file_bytes']==512


@pytest.mark.parametrize('field',['script_digest','max_stdout_bytes','timeout_seconds','input_bytes','width_px'])
def test_changed_execution_cannot_use_existing_durable_resources(binding,monkeypatch,field):
    prepared,grant,probe,scheduler,checks=binding
    with sqlite3.connect(grant.database_path) as connection:
        resources=probe._grant_resources(grant)
        resources[field]='d'*64 if field=='script_digest' else resources[field]+1
        connection.execute('UPDATE device_work_requests SET resources_json=?',(json.dumps(resources),))
    monkeypatch.setattr(media_script,'validate_prepared_media_script',lambda value:None)
    monkeypatch.setattr(media_script,'run_granted_command',lambda *a,**kw:pytest.fail('changed script dispatched'))
    with pytest.raises(MediaScriptError):
        media_script.run_media_script(prepared=prepared,scheduler=scheduler,grant=grant)


@pytest.mark.parametrize('error',[GrantedCommandCancelled,GrantedCommandTimeout])
def test_cancel_and_timeout_remain_executor_outcomes(binding,monkeypatch,error):
    import threading
    prepared,grant,probe,scheduler,checks=binding
    monkeypatch.setattr(media_script,'validate_prepared_media_script',lambda value:None)
    cancel=threading.Event()
    def fail(*args,**kwargs):
        assert kwargs['cancel_event'] is cancel and kwargs['timeout_seconds']==.5
        raise error('fixture executor stopped')
    monkeypatch.setattr(media_script,'run_granted_command',fail)
    monkeypatch.setattr(media_script,'verify_media_script_outputs',lambda value:pytest.fail('failed execution registered output'))
    with pytest.raises(error):
        media_script.run_media_script(prepared=prepared,scheduler=scheduler,grant=grant,cancel_event=cancel)


@pytest.mark.parametrize('change',['code','input_revision','control'])
def test_sealed_changes_are_rejected_before_dispatch(binding,monkeypatch,change):
    prepared,grant,probe,scheduler,checks=binding
    if change=='code':
        prepared.sealed=replace(prepared.sealed,script_source='print("changed")')
    elif change=='input_revision':
        original,=prepared.sealed.input_revisions
        prepared.sealed=replace(prepared.sealed,input_revisions=(replace(original,revision='r2'),))
    else:
        path=prepared.directory/'control.json'
        path.write_bytes(b'changed')
        prepared.control_hashes=(('control.json',hashlib.sha256(b'original').hexdigest()),)
    monkeypatch.setattr(media_script,'run_granted_command',lambda *a,**kw:pytest.fail('changed seal dispatched'))
    with pytest.raises(MediaScriptError):
        media_script.run_media_script(prepared=prepared,scheduler=scheduler,grant=grant)


def test_pre_admission_resource_projection_never_reads_or_hashes_files(binding,monkeypatch):
    prepared,grant,probe,scheduler,checks=binding
    with monkeypatch.context() as isolated:
        isolated.setattr(Path,'stat',lambda *a,**kw:pytest.fail('pre-admission filesystem scan'))
        isolated.setattr(Path,'open',lambda *a,**kw:pytest.fail('pre-admission file read'))
        facts=media_script_resource_facts(sealed=prepared.sealed,limits=prepared.limits,
            input_sizes={'input.png':len(b'synthetic-input')},threads=1)
    assert facts['input_bytes']==len(b'synthetic-input')


def test_complete_runtime_recheck_follows_existing_grant_binding(binding,monkeypatch):
    prepared,grant,probe,scheduler,checks=binding
    prepared.control_hashes=()
    calls=[]
    def recheck(**kwargs):
        assert checks==[grant.input_digest]
        assert probe._grant_resources(grant)['script_digest']==prepared.sealed.digest
        calls.append(kwargs)
        return prepared.sealed.toolchain
    monkeypatch.setattr(ScriptToolchain,'from_paths',recheck)
    monkeypatch.setattr(media_script,'run_granted_command',lambda *a,**kw:SimpleNamespace(returncode=0))
    monkeypatch.setattr(media_script,'verify_media_script_outputs',lambda value:())
    assert media_script.run_media_script(prepared=prepared,scheduler=scheduler,grant=grant)==()
    assert len(calls)==1
