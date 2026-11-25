"""Candidate writes through the mounted HTTP routes and isolated SQLite."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from uuid import uuid4

import pytest

import app.main as main
from app.creation.contracts import CreationTask
from app.production import ProductionRepository
from test_typed_assistant_edits_api import metadata_http_state as metadata_http_state  # noqa: F401


def _binding(version_id, sequence):
    return {"expected_timeline_version_id": version_id, "expected_timeline_sequence": sequence}


def _crop(version_id, sequence, x):
    return {**_binding(version_id, sequence), "request_id": str(uuid4()), "operation_type": "ChangeCrop",
            "clip_id": "clip", "fit_mode": "fill", "crop_x": x, "crop_y": .5}


def _ok(response):
    assert response.status_code == 200, response.text
    return response.json()


def test_candidate_crop_reopens_without_moving_other_heads_or_formal_history(metadata_http_state, monkeypatch):
    state = metadata_http_state
    ws, task_id, base = state.task.workspace_id, state.task.id, state.base
    url = state.url + "/timeline/candidates"
    history = state.url + "/timeline/versions"
    formal_history = _ok(state.client.get(history))
    formal_montage = state.repo.get_creation_task_montage_plan(ws, task_id)
    source_snapshot = base.snapshot["montage"]
    candidate_a, candidate_b = str(uuid4()), str(uuid4())
    url_a, url_b = f"{url}/{candidate_a}", f"{url}/{candidate_b}"
    crop_a, crop_b = url_a + "/edits/exact", url_b + "/edits/exact"
    fork = {"candidate_id": candidate_a, **_binding(base.id, base.sequence)}
    assert _ok(state.client.post(url, json=fork))["head_version_id"] == base.id
    assert _ok(state.client.post(url, json={**fork, "candidate_id": candidate_b}))["head_version_id"] == base.id
    first_request = _crop(base.id, base.sequence, .2)
    first_a = _ok(state.client.post(crop_a, json=first_request))["version"]
    first_b = _ok(state.client.post(crop_b, json=_crop(base.id, base.sequence, .8)))["version"]
    assert (first_a["parent_version_id"], first_b["parent_version_id"]) == (base.id, base.id)
    assert first_a["sequence"] == first_b["sequence"] == base.sequence + 1
    assert first_a["id"] != first_b["id"]
    assert state.client.post(crop_b, json=_crop(first_a["id"], first_a["sequence"], .3)).status_code == 409

    # Two writes on the same branch head cannot both succeed.
    requests = [_crop(first_a["id"], first_a["sequence"], x) for x in (.3, .4)]
    with ThreadPoolExecutor(max_workers=2) as workers:
        concurrent = list(workers.map(lambda body: state.client.post(crop_a, json=body), requests))
    assert sorted(response.status_code for response in concurrent) == [200, 409]
    winner = next(index for index, response in enumerate(concurrent) if response.status_code == 200)
    second_a = concurrent[winner].json()["version"]
    assert second_a["parent_version_id"] == first_a["id"] and second_a["sequence"] == first_a["sequence"] + 1
    assert second_a["clips"][0]["crop_x"] == requests[winner]["crop_x"]
    assert _ok(state.client.get(url_b))["version"] == first_b
    assert _ok(state.client.get(history)) == formal_history
    assert state.repo.task_records.get_creation_task(ws, task_id) == state.task
    assert state.repo.get_creation_task_montage_plan(ws, task_id) == formal_montage
    assert state.repo.edit_records.list_edit_operations(ws, task_id) == []
    assert state.repo.job_records.list_job_runs(ws, task_id) == []
    assert state.client.get(f"{history}/{second_a['id']}").status_code == 404
    assert state.client.get(f"{history}/{second_a['id']}/artifacts/video?preview=true").status_code == 404

    reopened = ProductionRepository(state.repo.database_path, state.repo.video_library_path)
    monkeypatch.setattr(main, "production_repo", reopened)
    assert _ok(state.client.get(url_a))["version"] == second_a
    assert _ok(state.client.get(url_b))["version"] == first_b
    assert {item["id"]: item["head_version_id"] for item in _ok(state.client.get(url))["items"]} == {
        candidate_a: second_a["id"], candidate_b: first_b["id"],
    }
    # Replay returns the exact result even if that result is no longer the head.
    replay = _ok(state.client.post(crop_a, json=first_request))
    assert replay["version"] == first_a and replay["head_version_id"] == second_a["id"]
    assert state.client.post(crop_a, json={**first_request, "crop_x": .6}).status_code == 409
    assert state.client.post(crop_a, json=_crop(first_a["id"], first_a["sequence"], .6)).status_code == 409
    assert state.client.post(crop_a, json=_crop(second_a["id"], first_a["sequence"], .6)).status_code == 409
    with reopened.connection_factory() as conn:
        candidate = reopened.timeline_records.get_candidate(ws, task_id, candidate_a, connection=conn)
        saved = reopened.timeline_records.get_candidate_version(candidate, second_a["id"], connection=conn)
    saved_montage = saved.snapshot["montage"]
    assert saved.snapshot["subtitle_track_ids"] == base.snapshot["subtitle_track_ids"]
    assert saved_montage["narration_audio_path"] == source_snapshot["narration_audio_path"]
    assert saved_montage["cues"] == source_snapshot["cues"]
    assert saved_montage["clips"][0] == {**source_snapshot["clips"][0], **second_a["clips"][0]}
    with pytest.raises(ValueError, match="候选时间线不能直接发布"):
        reopened.timeline_records.create_timeline_version(saved)

    monkeypatch.setattr(main, "_wake_assistant_continuations", lambda: None)
    assert state.client.post(state.url + "/edits/exact", json=_crop(second_a["id"], second_a["sequence"], .6)).status_code == 409
    adopted = _ok(state.client.post(state.url + "/edits/exact", json=_crop(base.id, base.sequence, .6)))["operation"]
    assert _ok(state.client.get(f"{history}/{adopted['result_version_id']}"))["version"]["sequence"] == first_b["sequence"]
    assert _ok(state.client.get(history))["total_count"] == formal_history["total_count"] + 1
    assert state.client.post(url, json={**fork, "candidate_id": str(uuid4())}).status_code == 409
    assert _ok(state.client.get(url_a))["version"] == second_a
    assert _ok(state.client.get(url_b))["version"] == first_b


def test_candidate_routes_reject_cross_task_workspace_and_member(metadata_http_state):
    state = metadata_http_state
    ws, task_id, base = state.task.workspace_id, state.task.id, state.base
    url = state.url + "/timeline/candidates"
    candidate_id = str(uuid4())
    fork = {"candidate_id": candidate_id, **_binding(base.id, base.sequence)}
    _ok(state.client.post(url, json=fork))
    crop = _crop(base.id, base.sequence, .2)
    owner = state.member.member_id
    plan = replace(state.repo.plan_records.get_creation_plan(ws, state.task.plan_id), id="other-plan")
    sibling = CreationTask("other-task", ws, plan.id, "other work", "knowledge_explanation", owner_user_id=owner)
    state.repo.plan_confirmation.confirm_creation_plan(plan, sibling, verified_owner_user_id=owner)
    sibling_url = "/api/creation-tasks/other-task/timeline/candidates"
    assert state.client.get(f"{sibling_url}/{candidate_id}").status_code == 404
    assert state.client.post(f"{sibling_url}/{candidate_id}/edits/exact", json=crop).status_code == 404
    assert state.client.post(sibling_url, json=fork).status_code == 409
    assert state.client.get(f"{url}/{candidate_id}", headers={"x-test-workspace": "outside"}).status_code == 404

    state.repo.task_records.set_creation_task_visibility(ws, task_id, "shared")
    _reader, reader_token = state.members.join(state.members.create_invite_code(), "reader")
    reader = {"X-Local-Member-Token": reader_token}
    assert state.client.get(url, headers=reader).status_code == 403
    assert state.client.get(f"{url}/{candidate_id}", headers=reader).status_code == 403
    assert state.client.post(url, headers=reader, json=fork).status_code == 403
    assert state.client.post(f"{url}/{candidate_id}/edits/exact", headers=reader, json=crop).status_code == 403
    assert state.client.post(f"{url}/{candidate_id}/edits/exact", json={**crop, "operation_type": "TrimClip"}).status_code == 422
    assert _ok(state.client.get(f"{url}/{candidate_id}"))["head_version_id"] == base.id
