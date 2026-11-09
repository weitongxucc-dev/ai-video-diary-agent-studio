"""Application info metadata; build probe is controlled, no Git/process execution."""
import subprocess
import wave

import pytest
from fastapi.testclient import TestClient

import app.main as main
from app.auth import SupabaseAuthContext
from app.config import Settings
from app.device_runtime import DeviceRuntime
from app.workflow import LangGraphEpisodeWorkflow


def test_application_info_metadata_redacts_and_preserves_build_probe_contract(tmp_path, monkeypatch):
    forbidden_calls = []

    def forbidden(*args, **kwargs):
        forbidden_calls.append('execution')
        pytest.fail('application info metadata forbids process/workflow/runtime/media execution')

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    monkeypatch.setattr(wave, 'open', forbidden)
    monkeypatch.setattr(LangGraphEpisodeWorkflow, 'run', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'start', forbidden)
    monkeypatch.setattr(DeviceRuntime, 'run_once', forbidden)
    monkeypatch.setattr(main, '_creation_runtime', forbidden)
    inbox, output, binaries = (tmp_path/name for name in ('inbox', 'output', 'bin'))
    for path in (inbox, output, binaries):
        path.mkdir()
    monkeypatch.setenv('PATH', str(binaries))
    settings = Settings(database_path=str(tmp_path/'absent.sqlite3'), video_inbox_path=str(inbox),
        video_output_path=str(output), model_root_path=str(tmp_path/'models'), temporary_root_path=str(tmp_path/'temporary'),
        obsidian_vault_path=None, hermes_radar_path=None, local_whisper_python_path=None,
        local_whisper_cli_path=None, local_whisper_model_path=None,
        llm_provider='minimax', llm_api_key='fixture-llm-secret', llm_api_key_provider='minimax',
        llm_base_url='https://fixture.invalid/v1', llm_model='fixture-model',
        product_hunt_token='fixture-product-secret', topic_source_urls=(),
        asr_provider='tencentcloud', tencentcloud_secret_id='fixture-asr-id', tencentcloud_secret_key='fixture-asr-secret',
        supabase_url=None, supabase_publishable_key=None)
    monkeypatch.setattr(main, 'settings', settings)
    monkeypatch.setattr(main, 'auth_context', SupabaseAuthContext(settings))
    root = tmp_path/'build-fixture'
    anchor = root/'apps/api/app/main.py'
    anchor.parent.mkdir(parents=True)
    anchor.write_text('# isolated file-position anchor\n', encoding='utf-8')
    (root/'apps/api/pyproject.toml').write_text('[project]\nname = "fixture"\nversion = "9.8.7"\n', encoding='utf-8')
    monkeypatch.setattr(main, '__file__', str(anchor))
    probes = []

    def git_probe(argv, **kwargs):
        assert argv == ['git', 'log', '-1', '--format=%H,%aI']
        assert kwargs == {'capture_output': True, 'text': True, 'timeout': 5, 'check': False, 'cwd': root}
        probes.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout='fixture-commit,fixture-date\n', stderr='')

    monkeypatch.setattr(subprocess, 'run', git_probe)
    client = TestClient(main.app)  # No lifespan; actual workflow object metadata only.

    def tree():
        return {str(path.relative_to(tmp_path)): path.read_bytes() if path.is_file() else None
                for path in tmp_path.rglob('*')}

    before = tree()
    status = client.get('/api/system/status')
    assert status.status_code == 200, status.text
    data = status.json()
    assert data['storage'] == {'database_path': str(tmp_path/'absent.sqlite3'), 'database_exists': False}
    assert data['video']['inbox_path'] == str(inbox) and data['video']['output_path'] == str(output)
    assert data['obsidian']['configured'] is False and data['hermes_radar']['configured'] is False
    assert data['asr']['configured'] is True and data['llm']['configured'] is True
    for secret in ('fixture-llm-secret', 'fixture-product-secret', 'fixture-asr-id', 'fixture-asr-secret'):
        assert secret not in status.text
    workflow = client.get('/api/workflow/status')
    assert workflow.status_code == 200, workflow.text
    assert workflow.json()['framework'] == 'LangGraph'
    assert workflow.json()['node_names'] and workflow.json()['human_review_nodes']
    version = client.get('/api/version')
    assert version.status_code == 200, version.text
    assert set(version.json()) == {'version', 'build_date', 'git_commit', 'phases_completed'}
    assert (version.json()['version'], version.json()['git_commit'], version.json()['build_date']) == (
        '9.8.7', 'fixture-commit', 'fixture-date')
    assert version.json()['phases_completed']
    assert len(probes) == 1 and forbidden_calls == []
    assert tree() == before
