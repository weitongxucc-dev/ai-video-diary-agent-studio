"""Native due-driver pause/resume; automation business is a controlled tick."""
import threading

import pytest

from app.automation_runtime import AutomationRuntime
from app.production import ProductionRepository
from app.source_repository import SourceRepository


def runtime(tmp_path):
    database = tmp_path / "studio.sqlite3"
    production = ProductionRepository(database, tmp_path / "library")
    return AutomationRuntime(production, SourceRepository(database),
        verify_identity=lambda *_: "ready", list_templates=lambda *_: [],
        preflight=lambda *_: None, preview_response=lambda *_: None, plan_response=lambda *_: None,
        execution_status=lambda *_: None, sync_connector=lambda *_: None,
        inspect_topic_route=lambda *_: None, execution_available=lambda: False)


def test_busy_tick_pause_refuses_without_stop_then_original_loop_ticks_again(tmp_path):
    owner = runtime(tmp_path)
    entered, release, continued = threading.Event(), threading.Event(), threading.Event()
    calls = []
    def tick():
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            assert release.wait(3)
        else:
            continued.set()
    owner.service.tick = tick
    owner.start()
    thread = owner.thread
    try:
        assert entered.wait(2)
        assert owner.pause(0.01) is False
        assert not owner.stop_event.is_set() and not owner._paused.is_set()
        release.set()
        owner._wake.set()
        assert continued.wait(2)
        assert owner.thread is thread and thread.is_alive()
    finally:
        release.set()
        owner.stop()
    assert not thread.is_alive()


def test_quiescent_tick_resume_reuses_loop_and_terminal_stop_is_distinct(tmp_path):
    owner = runtime(tmp_path)
    ticked = threading.Event()
    owner.service.tick = ticked.set
    owner.start()
    try:
        assert ticked.wait(2)
        assert owner.pause(1)
        thread = owner.thread
        ticked.clear()
        assert owner._paused.is_set() and not owner.stop_event.is_set()
        owner.resume()
        assert ticked.wait(2)
        assert owner.thread is thread
    finally:
        owner.stop()
    assert not owner.thread.is_alive()
    with pytest.raises(RuntimeError, match="已终止"):
        owner.resume()
