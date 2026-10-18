"""Assembly checks only; synthetic profiles never sample or run heavy work."""
from dataclasses import asdict
import json

import pytest

import app.main as main
from app.host_memory import MemoryPolicy
from app.production import ProductionRepository
from test_device_runtime_wiring import _CALIBRATED_ENV
from test_material_visual_query_resources import _budget, _environment


@pytest.mark.parametrize('mode', ['interactive', 'scheduled'])
def test_one_pipeline_receives_explicit_memory_policy_query_budget_and_real_poll(tmp_path, monkeypatch, mode):
    repository = ProductionRepository(tmp_path / 'runtime.sqlite3', tmp_path / 'library')
    for name, value in (_CALIBRATED_ENV | _environment()).items():
        monkeypatch.setenv(name, value)
    policy = MemoryPolicy('darwin', 5., 2., 'synthetic://assembly-only')
    monkeypatch.setenv('DEVICE_HOST_MEMORY_POLICY_JSON', json.dumps(asdict(policy)))
    monkeypatch.setattr(main, 'material_execution_mode', mode)
    runtime, service = main._build_device_components(repository=repository)
    assert runtime.running is False and runtime.poll_interval_seconds == 1.
    assert runtime.scheduler.resources.budget.memory_policy == policy
    assert runtime.scheduler.resources.budget.material_visual_query == _budget()
    if mode == 'scheduled':
        assert service.scheduler is runtime.scheduler
    else:
        assert service is None
