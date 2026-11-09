"""CI partitions every current file once without starting pytest's media suites."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def runner():
    path = Path(__file__).resolve().parents[3] / 'scripts' / 'run-backend-tests.py'
    spec = importlib.util.spec_from_file_location('backend_test_runner', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_current_portable_and_native_files_are_complete_disjoint_partitions(runner):
    discovered = runner.test_files()
    native = [path for path in discovered if path in runner.DARWIN_FILES]
    portable = [path for path in discovered if path not in runner.DARWIN_FILES]
    shards = runner.partition_files(portable, suite='portable', total=4)
    assert shards[0] == ['tests/test_creation_execution.py']
    assert shards[1] == ['tests/test_montage_pipeline.py', 'tests/test_twenty_two_cut_delivery.py']
    all_shards = shards + runner.partition_files(native, suite='darwin', total=1)
    flattened = [path for shard in all_shards for path in shard]
    assert len(flattened) == len(set(flattened))
    assert set(flattened) == set(discovered)
    assert set(native) == runner.DARWIN_FILES
    assert all(all_shards)
    assert abs(len(shards[2]) - len(shards[3])) <= 1
    assert 'tests/test_backend_test_sharding.py' in shards[2] + shards[3]
    assert not set(native).intersection(shards[0] + shards[1] + shards[2] + shards[3])


def test_new_metadata_files_remain_automatic_and_do_not_move_heavy_suites(runner):
    files = [path for path in runner.test_files() if path not in runner.DARWIN_FILES]
    before = runner.partition_files(files, suite='portable', total=4)
    extra = 'tests/test_new_metadata_consumer.py'
    after = runner.partition_files(files + [extra], suite='portable', total=4)
    assert before[:2] == after[:2]
    assert [path for shard in after for path in shard].count(extra) == 1
    assert set(path for shard in after for path in shard) == set(files) | {extra}


@pytest.mark.parametrize('code,expected', [(0, 0), (3, 3), (-15, 143)])
def test_runner_preserves_pytest_progress_junit_and_exit_status(runner, monkeypatch, tmp_path, capsys, code, expected):
    report = tmp_path / 'report' / 'junit.xml'
    monkeypatch.setattr('sys.argv', ['run-backend-tests.py', '--suite', 'portable', '--shard', '1',
                                   '--total', '4', '--junitxml', str(report)])
    calls = []
    def run(argv, *, cwd):
        calls.append((argv, cwd))
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr(runner, 'subprocess', SimpleNamespace(run=run))
    assert runner.main() == expected
    payload = json.loads(capsys.readouterr().out)
    assert payload['files'] == ['tests/test_creation_execution.py']
    argv, cwd = calls.pop()
    assert not calls and cwd == runner.API
    assert argv[:4] == [runner.sys.executable, '-m', 'pytest', '-v']
    assert f'--junitxml={report}' in argv and '--tb=short' in argv and '--durations=20' in argv
    assert argv[-1] == 'tests/test_creation_execution.py' and report.parent.is_dir()


@pytest.mark.parametrize('missing', ['native', 'heavy'])
def test_missing_required_suite_is_an_error_not_silent_omission(runner, monkeypatch, tmp_path, missing):
    removed = next(iter(runner.DARWIN_FILES)) if missing == 'native' else 'tests/test_creation_execution.py'
    discovered = [path for path in runner.test_files() if path != removed]
    monkeypatch.setattr(runner, 'test_files', lambda: discovered)
    monkeypatch.setattr('sys.argv', ['run-backend-tests.py', '--suite', 'portable', '--shard', '1',
                                   '--total', '4', '--junitxml', str(tmp_path/'unused.xml'), '--dry-run'])
    with pytest.raises(SystemExit) as rejected:
        runner.main()
    assert rejected.value.code == 2
