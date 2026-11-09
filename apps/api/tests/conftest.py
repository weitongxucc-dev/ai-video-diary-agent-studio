import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from workspace_secret_test_fixtures import workspace_secrets_tool_double as workspace_secrets_tool_double
from quality_native_test_fixtures import licensed_quality_native as licensed_quality_native

# The explicit test-session composition runs before per-test monkeypatches.
# Keep it away from every developer/app database and output tree.
_test_directory = TemporaryDirectory(prefix="ai-video-tests-", dir=os.environ.get("TMPDIR"))
_test_root = Path(_test_directory.name)
(_test_root / "empty.env").touch()
for name, relative in {
    "DATABASE_PATH": "data/studio.sqlite3",
    "VIDEO_INBOX_PATH": "inbox",
    "VIDEO_OUTPUT_PATH": "output",
    "DRAFT_OUTPUT_PATH": "drafts",
    "JIANYING_DRAFT_ROOT_PATH": "jianying",
    "MODEL_ROOT_PATH": "models",
    "TEMPORARY_ROOT_PATH": "temporary",
}.items():
    path = _test_root / relative
    (path.parent if name == "DATABASE_PATH" else path).mkdir(parents=True, exist_ok=True)
    os.environ[name] = str(path)
os.environ.update({
    "DEVICE_RESOURCE_MODE": "experiment",
    "DEVICE_RESOURCE_EXPERIMENT_ID": "pytest-isolated-fixture",
    "AI_VIDEO_DIARY_DOTENV": str(_test_root / "empty.env"),
    "NATIVE_OCR_WORKER_PATH": "",
    "NATIVE_PORTRAIT_WORKER_PATH": "",
    "LLM_PROVIDER": "deepseek",
    "LLM_API_KEY": "test-only-key",
    "LLM_API_KEY_PROVIDER": "deepseek",
    "LLM_BASE_URL": "http://127.0.0.1:9",
    "DEEPSEEK_API_KEY": "test-only-key",
    "DEEPSEEK_BASE_URL": "http://127.0.0.1:9",
    "TENCENTCLOUD_SECRET_ID": "",
    "TENCENTCLOUD_SECRET_KEY": "",
    "PRODUCT_HUNT_TOKEN": "",
    "SUPABASE_URL": "",
    "SUPABASE_PUBLISHABLE_KEY": "",
    "SUPABASE_JWT_ISSUER": "",
})


def pytest_sessionstart(session):
    from app import device_paths
    # Explicit test machine context: no test may acquire the real application lock.
    device_paths.application_data_root = lambda: _test_root / "device-execution"
    from app.main import create_app

    create_app()


def pytest_sessionfinish(session, exitstatus):
    from app.main import stop_creation_execution_worker

    stop_creation_execution_worker()
    _test_directory.cleanup()


@pytest.fixture
def granted_media(tmp_path):
    """Bound real synthetic-media tests to persisted jobs and a real device grant."""
    from contextlib import contextmanager
    import time
    from app.creation_execution import CpuRenderGrantContext
    from app.host_resource_probes import HostProcessProbe
    from test_task_media_grants import case

    sequence = 0

    @contextmanager
    def acquire(paths=(), *, kind="task_audio"):
        nonlocal sequence
        sequence += 1
        root = tmp_path / f"media-grant-{sequence}"
        root.mkdir()
        repository, actor, _, _, build, media = case(root)
        original = media.facts
        sources = list(dict.fromkeys(Path(path).resolve() for path in paths))

        def facts(*args):
            identity, resources = original(*args)
            resources.update(max_output_bytes=32 * 1024**2,
                kind=kind,
                input_count=len(sources), input_bytes=sum(path.stat().st_size for path in sources if path.is_file()),
                inputs=[{"path": str(path), "duration_seconds": None, "sample_rate": None, "channels": None} for path in sources])
            return identity, resources

        media.facts = facts
        runtime = build(media)
        runtime.scheduler.processes = HostProcessProbe()
        job = media.submit(runtime, actor, "task", "synthetic_media", {}, "audio_listen")
        grant = runtime.scheduler.pick_and_grant()
        assert grant is not None and grant.owner_id == job.id
        # Synthetic media fixture metadata only; not real host/RSS calibration.
        if kind == "creation_render":
            from types import SimpleNamespace
            runtime.scheduler.resources.budget = SimpleNamespace(
                cpu_render=SimpleNamespace(additional_disk_bytes=64 * 1024**2), presenter_cpu_render=None)
            def granted_resources(current):
                assert current == grant
                with repository._connect() as conn:
                    row = conn.execute("SELECT resources_json FROM device_work_requests WHERE id=? AND workspace_id=? "
                        "AND owner_id=? AND state IN ('granted','yield_requested')",
                        (current.request_id,current.workspace_id,current.owner_id)).fetchone()
                return json.loads(row[0]) if row else None
            runtime.scheduler.resources._grant_resources = granted_resources
        context = CpuRenderGrantContext(runtime.scheduler, grant, time.monotonic() + 30, 32 * 1024**2, 1)
        succeeded = False
        try:
            yield context
            succeeded = True
        finally:
            def checkpoint(conn, current):
                conn.execute("UPDATE job_runs SET status=? WHERE id=?", ("succeeded" if succeeded else "failed", job.id))
                return f"synthetic-media:{job.id}"
            if succeeded:
                runtime.scheduler.complete_unit(grant, checkpoint_database_path=repository.database_path,
                    checkpoint_write=checkpoint)
            else:
                runtime.scheduler.fail_unit(grant, reason="synthetic_media_failed", failure_write=checkpoint)
            runtime.scheduler.assert_released(grant)
            assert runtime.stop(1)

    return acquire


@pytest.fixture
def granted_montage_renderer(granted_media, monkeypatch):
    from app.montage import MontageRenderer, CreationTaskMontagePlan
    original = MontageRenderer.render

    def render(renderer, plan, audio_path, **kwargs):
        if not isinstance(plan, CreationTaskMontagePlan) or kwargs.get("grant") is not None:
            return original(renderer, plan, audio_path, **kwargs)
        paths = [Path(audio_path), *(Path(clip.source_path) for clip in plan.clips if Path(clip.source_path).is_file())]
        with granted_media(paths, kind="creation_render") as context:
            return original(renderer, plan, audio_path, scheduler=context.scheduler, grant=context.grant,
                max_output_bytes=context.max_output_bytes, threads=context.threads,
                deadline_monotonic=context.deadline_monotonic, **kwargs)

    monkeypatch.setattr(MontageRenderer, "render", render)
