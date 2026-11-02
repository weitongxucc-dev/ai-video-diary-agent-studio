"""Main consumes the installer's single lifecycle and guarded settings boundary."""
import ast
import inspect
import json
import os
import tempfile
import threading

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.config import Settings, model_catalog_configuration
from app.model_installer import ModelInstaller
from app.model_runtime import CHINESE_CLIP_VIT_B16
from app.runtime_health import model_catalog_from_settings


@pytest.fixture
def isolated_host(tmp_path,monkeypatch):
    for function in (main._publish_runtime_dependencies, main._activate_runtime_reload, main._publish_runtime_routes):
        for node in ast.walk(ast.parse(inspect.getsource(function))):
            if isinstance(node,ast.Global):
                for name in node.names:
                    monkeypatch.setattr(main,name,getattr(main,name))
    monkeypatch.setattr(main.app.router, "routes", list(main.app.router.routes))
    monkeypatch.setattr(main.app, "openapi_schema", main.app.openapi_schema)
    for name in ("execution_worker", "material_ingestion", "device_runtime", "automation_runtime", "assistant_continuation_driver"):
        monkeypatch.setattr(main, name, None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    monkeypatch.setenv("TMPDIR", os.environ["TMPDIR"])
    monkeypatch.setattr(main,'creation_execution_mode','legacy')
    monkeypatch.setattr(main,'material_execution_mode','legacy')
    env_path=tmp_path/'accepted.env'
    env_path.write_text('')
    monkeypatch.setenv('AI_VIDEO_DIARY_DOTENV',str(env_path))
    monkeypatch.setenv('MODEL_ROOT_PATH',str(tmp_path/'models'))
    for key, relative in {
        "DATABASE_PATH": "old.sqlite3", "VIDEO_INBOX_PATH": "inbox", "VIDEO_OUTPUT_PATH": "output",
        "DRAFT_OUTPUT_PATH": "drafts", "TEMPORARY_ROOT_PATH": "temporary",
        "JIANYING_DRAFT_ROOT_PATH": "jianying", "HERMES_RADAR_PATH": "hermes", "OBSIDIAN_VAULT_PATH": "vault",
    }.items():
        monkeypatch.setenv(key, str(tmp_path / relative))
    for key in ('MODEL_RUNTIME_PYTHON_PATH','VISUAL_MODEL_WORKER_PATH','VOICE_MODEL_WORKER_PATH',
                'UNDERSTANDING_RUNTIME_PYTHON_PATH','UNDERSTANDING_WORKER_PATH'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setattr(main,'runtime_env_path',env_path)
    current=Settings.from_env()
    main._publish_runtime_dependencies(main.prepare_runtime_dependencies(current))
    catalog=model_catalog_from_settings(current)
    assert catalog.model_root_path.is_relative_to(tmp_path)
    monkeypatch.setattr(main,'model_catalog',catalog)
    entered,release=threading.Event(),threading.Event()
    class ControlledInstaller(ModelInstaller):
        def storage_preflight(self,spec):return {}
        def _ensure_runtime(self,spec):pass
        def _download_model(self,job_id,spec):
            entered.set()
            assert release.wait(10)
            return []
        def _publish_model(self,spec,files):pass
    installer=ControlledInstaller(catalog,on_complete=lambda:main._reload_runtime_dependencies())
    monkeypatch.setattr(main,'model_installer',installer)
    try:
        yield installer,entered,release,env_path,catalog
    finally:
        release.set()
        if installer._active_thread is not None:
            installer._active_thread.join(10)
            assert not installer._active_thread.is_alive()
        main._stop_device_runtime()
        main._stop_material_ingestion()
        main._stop_execution_worker()
        if main.automation_runtime is not None:
            main.automation_runtime.stop()
        if main.assistant_continuation_driver is not None:
            main.assistant_continuation_driver.stop()


def test_startup_recovers_once_but_reload_never_recovers_or_replaces_installer(isolated_host,monkeypatch):
    installer,_,_,_,catalog=isolated_host
    installer.jobs_path.mkdir(parents=True)
    identity='a'*32
    path=installer.jobs_path/(identity+'.json')
    path.write_text(json.dumps({'id':identity,'status':'running','original':'keep'}))
    assert main.app.router.on_startup.count(main.recover_model_installations) == 1
    main.recover_model_installations()
    recovered=path.read_bytes()
    assert installer.get_job(identity)['status']=='interrupted'
    def forbidden():raise AssertionError('reload cannot recover installation jobs')
    monkeypatch.setattr(installer,'recover_interrupted_jobs',forbidden)
    main._reload_runtime_dependencies()
    assert main.model_installer is installer and path.read_bytes()==recovered


def test_live_same_catalog_reload_and_on_complete_keep_job_ready(isolated_host):
    installer,entered,release,_,catalog=isolated_host
    job=installer.start(CHINESE_CLIP_VIT_B16.id,license_accepted=True)
    assert entered.wait(2)
    before=installer.get_job(job['id'])
    main._reload_runtime_dependencies()
    assert main.model_installer is installer
    assert installer.get_job(job['id'])==before
    assert installer.catalog is catalog
    release.set()
    installer._active_thread.join(10)
    done=installer.get_job(job['id'])
    assert done['status']=='ready' and '重载失败' not in done['message']


def test_live_path_change_refuses_before_any_device_or_workspace_write(isolated_host,monkeypatch,tmp_path):
    installer,entered,release,env_path,_=isolated_host
    job=installer.start(CHINESE_CLIP_VIT_B16.id,license_accepted=True)
    assert entered.wait(2)
    before=env_path.read_bytes()
    def forbidden(*args):raise AssertionError('blocked catalog change must not write workspace settings')
    monkeypatch.setattr(main,'apply_workspace_runtime_settings_update',forbidden)
    client=TestClient(main.app,client=('127.0.0.1',50000))
    response=client.post('/api/settings/runtime',json={'model_root_path':str(tmp_path/'other'), 'llm_model':'blocked-model'})
    assert response.status_code==409,response.text
    assert env_path.read_bytes()==before and installer.get_job(job['id'])['status']=='running'


def test_scheduled_reload_refuses_before_any_settings_write(isolated_host,monkeypatch):
    installer,_,_,env_path,_=isolated_host
    monkeypatch.setattr(main,'creation_execution_mode','scheduled')
    def forbidden(*args):raise AssertionError('scheduled refusal must happen before persistence')
    monkeypatch.setattr(main,'apply_workspace_runtime_settings_update',forbidden)
    before=env_path.read_bytes()
    response=TestClient(main.app,client=('127.0.0.1',50000)).post('/api/settings/runtime',json={'llm_model':'blocked'})
    assert response.status_code==503,response.text
    assert env_path.read_bytes()==before and main.model_installer is installer


def test_idle_settings_update_persists_and_rebinds_same_owner_without_recovery(isolated_host, tmp_path):
    installer, _, _, env_path, _ = isolated_host
    new_root = tmp_path / "next-models"
    jobs = new_root / ".install-jobs"
    jobs.mkdir(parents=True)
    identity = "b" * 32
    path = jobs / (identity + ".json")
    path.write_text(json.dumps({"id": identity, "status": "running", "original": "keep"}))
    before = path.read_bytes()
    response = TestClient(main.app, client=("127.0.0.1", 50000)).post(
        "/api/settings/runtime", json={"model_root_path": str(new_root)}
    )
    assert response.status_code == 200, response.text
    assert f"MODEL_ROOT_PATH={new_root}" in env_path.read_text()
    assert main.model_installer is installer
    assert installer.catalog.model_root_path == main.model_catalog.model_root_path == new_root
    assert path.read_bytes() == before


@pytest.mark.parametrize("explicit_workers", [False, True])
def test_prospective_and_from_env_use_exact_same_six_path_resolution(monkeypatch,tmp_path,explicit_workers):
    monkeypatch.setattr('app.config.load_dotenv_files',lambda:None)
    values={'MODEL_ROOT_PATH':str(tmp_path/'models'),'MODEL_RUNTIME_PYTHON_PATH':str(tmp_path/'python'),
        'VISUAL_MODEL_WORKER_PATH':str(tmp_path/'visual.py'),'VOICE_MODEL_WORKER_PATH':str(tmp_path/'voice.py'),
        'UNDERSTANDING_RUNTIME_PYTHON_PATH':str(tmp_path/'understanding-python'),
        'UNDERSTANDING_WORKER_PATH':str(tmp_path/'understanding.py')}
    if not explicit_workers:
        for name in tuple(values):
            if name != "MODEL_ROOT_PATH":
                monkeypatch.delenv(name, raising=False)
                del values[name]
    for name,value in values.items():
        monkeypatch.setenv(name,value)
    parsed=Settings.from_env()
    expected=model_catalog_configuration(values)
    assert {name:getattr(parsed,name) for name in expected}==expected
