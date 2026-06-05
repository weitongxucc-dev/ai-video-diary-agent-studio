from threading import Event

from app.video_jobs import VideoJobRunner


def test_video_job_runner_rejects_duplicate_episode_until_active_job_finishes():
    release = Event()
    completed = Event()
    runner = VideoJobRunner()

    assert runner.submit("ep_1", lambda: release.wait(timeout=2)) is True
    assert runner.submit("ep_1", lambda: None) is False
    assert runner.is_active("ep_1") is True

    release.set()
    runner.submit("ep_2", completed.set)
    assert completed.wait(timeout=2) is True
    assert runner.is_active("ep_1") is False
