"""Finite real storage-owner recovery with synthetic files and security backend."""
import os
from pathlib import Path
import stat
import subprocess

import pytest

from app import env_files, runtime_settings as runtime, workspace_settings as workspace


WS = 'persistence-fixture'


def file_state(path):
    return (path.read_bytes(), stat.S_IMODE(path.stat().st_mode)) if path.exists() else None


def secret_state(tool):
    with workspace._lock:
        cache = dict(workspace._secret_cache)
    return dict(tool.items), cache


def seed(tmp_path, monkeypatch, *, existing=True):
    monkeypatch.setattr(env_files, '_repo_root', lambda: tmp_path / 'secondary')
    env_path = tmp_path / 'device.env'
    config_path = workspace.workspace_config_path(WS)
    if existing:
        env_path.write_bytes(b'# untouched\r\nUNKNOWN_VENDOR=value\r\nVIDEO_OUTPUT_PATH=old-file\r\n')
        env_path.chmod(0o640)
        config_path.parent.mkdir(parents=True)
        config_path.write_bytes(b'{ "vendor": [1, 2], "llm_provider": "deepseek", "llm_model": "old" } \n')
        config_path.chmod(0o640)
        workspace.save_workspace_secret(WS, 'llm_api_key', 'synthetic-old')
    workspace.save_workspace_secret('unrelated', 'llm_api_key', 'synthetic-unrelated')
    with workspace._lock:
        # Deliberately different from the backend: a cache read cannot be the inverse.
        workspace._secret_cache[(WS, 'llm_api_key')] = 'synthetic-cache-before'
        workspace._secret_cache[('unrelated', 'llm_api_key')] = 'synthetic-unrelated-cache'
    monkeypatch.setenv('VIDEO_OUTPUT_PATH', '')
    monkeypatch.delenv('DRAFT_BACKGROUND_MUSIC_PATH', raising=False)
    payload = {'llm_provider': 'deepseek', 'llm_model': 'synthetic-new-model',
        'llm_api_key': 'synthetic-new', 'video_output_path': str(tmp_path / 'new-output'),
        'draft_background_music_path': str(tmp_path / 'new-bgm.wav')}
    return env_path, config_path, payload


@pytest.mark.parametrize('existing', [True, False])
def test_combined_post_publication_failure_restores_raw_files_absence_environment_backend_cache(
        tmp_path, monkeypatch, workspace_secrets_tool_double, existing):
    env_path, config_path, payload = seed(tmp_path, monkeypatch, existing=existing)
    before = file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double)
    real_commit = runtime.commit_prepared_dotenv_values
    failure = PermissionError('synthetic failure after actual env file and process publication')
    def commit_then_fail(prepared):
        real_commit(prepared)
        assert os.environ['VIDEO_OUTPUT_PATH'] == payload['video_output_path']
        assert workspace.read_workspace_secret_fresh(WS, 'llm_api_key') == 'synthetic-new'
        raise failure
    monkeypatch.setattr(runtime, 'commit_prepared_dotenv_values', commit_then_fail)
    with pytest.raises(PermissionError) as caught:
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    assert caught.value is failure
    assert (file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double)) == before
    assert os.environ['VIDEO_OUTPUT_PATH'] == ''
    assert 'DRAFT_BACKGROUND_MUSIC_PATH' not in os.environ
    assert list(env_path.parent.glob('device.env.*.tmp')) == []
    assert list(config_path.parent.glob('config.json.*.tmp')) == []


def test_preparation_reads_fresh_backend_without_mutating_authoritative_files_process_or_cache(
        tmp_path, monkeypatch, workspace_secrets_tool_double):
    env_path, config_path, payload = seed(tmp_path, monkeypatch)
    before = file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double), dict(os.environ)
    secrets = workspace.prepare_workspace_secret_changes(WS, {'llm_api_key': 'synthetic-new'})
    assert secrets.originals['llm_api_key'] == 'synthetic-old'
    config = workspace.prepare_workspace_config(WS, {'llm_model': 'synthetic-new-model'})
    device = env_files.prepare_dotenv_values(env_path, {'VIDEO_OUTPUT_PATH': payload['video_output_path']})
    assert (file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double), dict(os.environ)) == before
    workspace.discard_prepared_workspace_config(config)
    env_files.discard_prepared_dotenv_values(device)
    workspace_secrets_tool_double.locked = True
    with pytest.raises(workspace.WorkspaceSecretStoreError):
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    assert (file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double), dict(os.environ)) == before


def test_second_secret_save_failure_restores_first_new_key(tmp_path, monkeypatch, workspace_secrets_tool_double):
    env_path = tmp_path / 'device.env'
    workspace.save_workspace_secret(WS, 'deepseek_api_key', 'synthetic-old-fallback')
    before = secret_state(workspace_secrets_tool_double)
    real_security = workspace._run_security
    def refuse_second(arguments):
        if arguments[0] == 'add-generic-password' and arguments[arguments.index('-s') + 1].endswith('.deepseek_api_key'):
            return subprocess.CompletedProcess(arguments, 36, '', 'synthetic second writer refusal')
        return real_security(arguments)
    monkeypatch.setattr(workspace, '_run_security', refuse_second)
    with pytest.raises(workspace.WorkspaceSecretStoreError):
        runtime.apply_runtime_settings_save(WS, env_path, {'llm_api_key': 'synthetic-new-one',
            'deepseek_api_key': 'synthetic-new-two', 'llm_provider': 'deepseek', 'video_output_path': str(tmp_path / 'output')})
    assert secret_state(workspace_secrets_tool_double) == before
    assert not env_path.exists() and not workspace.workspace_config_path(WS).exists()


def test_provider_delete_later_refusal_restores_prior_key_and_success_preserves_other_workspace(
        tmp_path, monkeypatch, workspace_secrets_tool_double):
    workspace.save_workspace_config(WS, {'llm_provider': 'deepseek', 'llm_api_key_provider': 'deepseek'})
    for name in workspace.WORKSPACE_SECRET_FIELDS:
        workspace.save_workspace_secret(WS, name, 'synthetic-old-' + name)
        workspace.read_workspace_secret(WS, name)
    workspace.save_workspace_secret('unrelated', 'llm_api_key', 'synthetic-other')
    before = secret_state(workspace_secrets_tool_double), file_state(workspace.workspace_config_path(WS))
    real_security = workspace._run_security
    def refuse_later_delete(arguments):
        if arguments[0] == 'delete-generic-password' and arguments[arguments.index('-s') + 1].endswith('.llm_api_key'):
            return subprocess.CompletedProcess(arguments, 36, '', 'synthetic later deletion refusal')
        return real_security(arguments)
    monkeypatch.setattr(workspace, '_run_security', refuse_later_delete)
    callbacks = []
    with pytest.raises(workspace.WorkspaceSecretStoreError):
        runtime.apply_workspace_runtime_settings_update(WS, {'llm_key_action': 'delete'}, lambda: callbacks.append(True))
    assert (secret_state(workspace_secrets_tool_double), file_state(workspace.workspace_config_path(WS))) == before
    assert callbacks == []
    monkeypatch.setattr(workspace, '_run_security', real_security)
    assert runtime.apply_workspace_runtime_settings_update(WS, {'llm_key_action': 'delete'}, lambda: callbacks.append(True))
    assert callbacks == [True]
    assert all(workspace.read_workspace_secret_fresh(WS, name) is None for name in workspace.WORKSPACE_SECRET_FIELDS)
    assert 'llm_api_key_provider' not in workspace.load_workspace_config(WS)
    assert workspace.read_workspace_secret_fresh('unrelated', 'llm_api_key') == 'synthetic-other'


def test_restore_refusal_retains_original_failure_recovers_other_owner_and_invalidates_only_affected_cache(
        tmp_path, monkeypatch, workspace_secrets_tool_double):
    env_path, config_path, payload = seed(tmp_path, monkeypatch)
    before_files = file_state(env_path), file_state(config_path)
    original = PermissionError('synthetic device replacement refused')
    real_replace = env_files.os.replace
    def refuse_device(source, target):
        if Path(target) == env_path:
            raise original
        return real_replace(source, target)
    monkeypatch.setattr(env_files.os, 'replace', refuse_device)
    real_security = workspace._run_security
    def refuse_restore(arguments):
        if arguments[0] == 'add-generic-password' and arguments[arguments.index('-w') + 1] == 'synthetic-old':
            return subprocess.CompletedProcess(arguments, 36, '', 'synthetic concrete restore refusal')
        return real_security(arguments)
    monkeypatch.setattr(workspace, '_run_security', refuse_restore)
    with pytest.raises(runtime.RuntimeSettingsSaveError) as caught:
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    error = caught.value
    assert error.original is original and [name for name, _ in error.failures] == ['工作区密钥']
    assert isinstance(error.__cause__, ExceptionGroup) and error.__cause__.exceptions[0] is original
    assert '恢复未完成' in str(error) and 'synthetic-old' not in str(error) and 'synthetic-new' not in str(error)
    assert (file_state(env_path), file_state(config_path)) == before_files
    assert workspace.read_workspace_secret_fresh(WS, 'llm_api_key') == 'synthetic-new'
    with workspace._lock:
        assert (WS, 'llm_api_key') not in workspace._secret_cache
        assert workspace._secret_cache[('unrelated', 'llm_api_key')] == 'synthetic-unrelated-cache'


@pytest.mark.parametrize('other_writer', ['workspace', 'secret'])
def test_recovery_does_not_overwrite_another_writer(tmp_path, monkeypatch, workspace_secrets_tool_double, other_writer):
    env_path, config_path, payload = seed(tmp_path, monkeypatch)
    real_replace = env_files.os.replace
    def another_writer_then_refuse(source, target):
        if Path(target) == env_path:
            if other_writer == 'workspace':
                config_path.write_bytes(b'{"llm_model": "synthetic-other-writer"}')
            else:
                workspace.save_workspace_secret(WS, 'llm_api_key', 'synthetic-other-writer')
            raise PermissionError('synthetic device refusal after outside change')
        return real_replace(source, target)
    monkeypatch.setattr(env_files.os, 'replace', another_writer_then_refuse)
    with pytest.raises(runtime.RuntimeSettingsSaveError, match='恢复未完成'):
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    if other_writer == 'workspace':
        assert config_path.read_bytes() == b'{"llm_model": "synthetic-other-writer"}'
        assert workspace.read_workspace_secret_fresh(WS, 'llm_api_key') == 'synthetic-old'
    else:
        assert workspace.read_workspace_secret_fresh(WS, 'llm_api_key') == 'synthetic-other-writer'
        assert workspace.load_workspace_config(WS)['llm_model'] == 'old'


def test_cleanup_refusal_cannot_mask_the_original_replace_error(tmp_path, monkeypatch):
    env_path = tmp_path / 'device.env'
    original = PermissionError('synthetic original replacement refused')
    monkeypatch.setattr(env_files.os, 'replace', lambda *args: (_ for _ in ()).throw(original))
    real_unlink = Path.unlink
    def refuse_owned_cleanup(path, *args, **kwargs):
        if path.parent == tmp_path and path.name.startswith('device.env.'):
            raise PermissionError('synthetic owned temporary cleanup refused')
        return real_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', refuse_owned_cleanup)
    with pytest.raises(runtime.RuntimeSettingsSaveError) as caught:
        runtime.apply_runtime_settings_update(env_path, {'video_output_path': str(tmp_path / 'output')}, lambda: pytest.fail('must not activate'))
    assert caught.value.original is original
    assert [name for name, _ in caught.value.failures] == ['设备临时文件']
    assert not env_path.exists()
    monkeypatch.setattr(Path, 'unlink', real_unlink)
    for path in tmp_path.glob('device.env.*.tmp'):
        path.unlink()


@pytest.mark.parametrize('owner', ['device', 'workspace'])
def test_preparation_refuses_user_symlink_target(tmp_path, owner):
    user_file = tmp_path / 'user-original'
    user_file.write_text('{}')
    path = tmp_path / 'device.env' if owner == 'device' else workspace.workspace_config_path(WS)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(user_file)
    with pytest.raises(RuntimeError, match='not a regular file'):
        if owner == 'device':
            env_files.prepare_dotenv_values(path, {'VIDEO_OUTPUT_PATH': 'synthetic-output'})
        else:
            workspace.prepare_workspace_config(WS, {'llm_model': 'synthetic-new'})
    assert path.is_symlink() and user_file.read_text() == '{}'


def test_post_commit_cleanup_refusal_is_not_retried_and_ownership_recovery_still_runs(
        tmp_path, monkeypatch, workspace_secrets_tool_double):
    env_path, config_path, payload = seed(tmp_path, monkeypatch)
    before = file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double)
    candidate_temporary = None
    checks = []
    real_commit = runtime.commit_prepared_dotenv_values
    def arm_after_commit(prepared):
        nonlocal candidate_temporary
        real_commit(prepared)
        candidate_temporary = prepared.temporary
    monkeypatch.setattr(runtime, 'commit_prepared_dotenv_values', arm_after_commit)
    real_lstat = Path.lstat
    refusal = PermissionError('synthetic candidate cleanup read refused')
    def refuse_cleanup_read(path, *args, **kwargs):
        if path == candidate_temporary:
            checks.append(path)
            raise refusal
        return real_lstat(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'lstat', refuse_cleanup_read)
    with pytest.raises(runtime.RuntimeSettingsSaveError) as caught:
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    assert caught.value.original is refusal and len(checks) == 1
    assert [name for name, _ in caught.value.failures] == ['设备临时文件']
    assert (file_state(env_path), file_state(config_path), secret_state(workspace_secrets_tool_double)) == before


def test_failed_secret_writer_with_unconfirmed_effect_is_explicit_incomplete_recovery(
        tmp_path, monkeypatch, workspace_secrets_tool_double):
    env_path, config_path, payload = seed(tmp_path, monkeypatch)
    before_files = file_state(env_path), file_state(config_path)
    real_security = workspace._run_security
    def mutate_then_deny(arguments):
        result = real_security(arguments)
        if arguments[0] == 'add-generic-password':
            return subprocess.CompletedProcess(arguments, 36, '', 'synthetic unconfirmed writer result')
        return result
    monkeypatch.setattr(workspace, '_run_security', mutate_then_deny)
    with pytest.raises(runtime.RuntimeSettingsSaveError, match='恢复未完成') as caught:
        runtime.apply_runtime_settings_save(WS, env_path, payload)
    assert isinstance(caught.value.original, workspace.WorkspaceSecretStoreError)
    assert workspace.read_workspace_secret_fresh(WS, 'llm_api_key') == 'synthetic-new'
    assert (file_state(env_path), file_state(config_path)) == before_files
    with workspace._lock:
        assert (WS, 'llm_api_key') not in workspace._secret_cache


@pytest.mark.parametrize('unsupported_original', ['', '\nsynthetic-ambiguous-key\n'])
def test_unrecoverable_original_secret_refuses_before_authoritative_writes(
        tmp_path, workspace_secrets_tool_double, unsupported_original):
    key = (WS, workspace._service_name(WS, 'llm_api_key'))
    workspace_secrets_tool_double.items[key] = unsupported_original
    before = dict(workspace_secrets_tool_double.items)
    env_path = tmp_path / 'device.env'
    with pytest.raises(workspace.WorkspaceSecretStoreError, match='无法确认可恢复'):
        runtime.apply_runtime_settings_save(WS, env_path, {'llm_api_key': 'synthetic-new',
            'llm_model': 'synthetic-new-model', 'video_output_path': str(tmp_path / 'output')})
    assert workspace_secrets_tool_double.items == before
    assert not env_path.exists() and not workspace.workspace_config_path(WS).exists()
