from threading import RLock
from types import SimpleNamespace

import pytest

from app.device_preparation_application import DevicePreparationApplication, DevicePreparationConflict
from app.device_resource_preparation import OWNER_TYPE


@pytest.mark.parametrize('state', ['failed', 'cancelled', 'done'])
@pytest.mark.parametrize('own_driver', [False, True])
def test_terminal_preparation_close_does_not_take_over_another_driver(state, own_driver):
    calls = []
    def publish(**_):
        calls.append('publish')
        return SimpleNamespace(profile_id='profile', revision=2, digest='digest')
    driver = SimpleNamespace(scheduler=SimpleNamespace(dispatch_owner=(OWNER_TYPE, 'plan') if own_driver else ('creation_job', 'other')))
    app = DevicePreparationApplication(service=SimpleNamespace(status=lambda **_: {'state': state, 'plan_id': 'plan'}, finalize=publish),
        database_path='isolated', lifecycle_lock=RLock(), current_runtime=lambda: driver,
        replace_runtime=lambda _: pytest.fail('must not replace driver'), stop_runtime=lambda: calls.append('stop'),
        bind_runtime=lambda *_: pytest.fail('must not bind driver'))
    if state == 'done' and not own_driver:
        with pytest.raises(DevicePreparationConflict, match='其他工作'):
            app.finalize('original', 'actor')
        assert calls == []
    else:
        result = app.finalize('original', 'actor')
        assert result['profile_published'] is (state == 'done')
        assert calls == (['stop'] if own_driver else []) + (['publish'] if state == 'done' else [])
