from dataclasses import asdict, replace
from copy import deepcopy
from shutil import copy2
from uuid import uuid4

import pytest

from app.creation.contracts import CreationTask, TimelineVersion
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.production import ProductionRepository
from app.content_identity_http import ContentIdentityHttpAdapter
from test_task_media_grants import _visual_scope_case


def test_adopt_only_bound_crop_and_undo_through_formal_history(tmp_path, monkeypatch):
    main, repo, actor, _, runtime, client, original, track, _ = _visual_scope_case(tmp_path, monkeypatch)
    task = '/api/creation-tasks/task'
    root = task + '/timeline/candidates'
    formal = {'id': 'timeline', 'sequence': 1}

    def forbidden_runtime():
        pytest.fail('candidate adoption and history undo cannot acquire a media runtime')

    monkeypatch.setattr(main, '_creation_runtime', forbidden_runtime)

    def ok(response, status=200):
        assert response.status_code == status, response.text
        return response.json()

    def binding(version):
        return {'expected_timeline_version_id': version['id'], 'expected_timeline_sequence': version['sequence']}

    def fork(base=formal):
        candidate = str(uuid4())
        ok(client.post(root, json={**binding(base), 'candidate_id': candidate}))
        return root + '/' + candidate

    def crop(path, version, x, *, clip='clip_1'):
        return ok(client.post(path + '/edits/exact', json={**binding(version), 'request_id': str(uuid4()),
            'operation_type': 'ChangeCrop', 'clip_id': clip, 'fit_mode': 'fill', 'crop_x': x, 'crop_y': .5}))['version']

    def target(version, base=formal):
        return {**binding(version), 'base_version_id': base['id'], 'base_sequence': base['sequence'], 'clip_id': 'clip_1'}

    def prepare(path, version):
        return client.post(path + '/adoption/prepare', json=target(version))

    def confirmation(path, version):
        summary = ok(prepare(path, version))
        return {**target(version), 'request_id': str(uuid4()), 'selection_digest': summary['selection_digest']}

    def confirm(path, inputs):
        return client.post(path + '/adoption/confirm', json=inputs)

    def counts():
        with repo._connect() as conn:
            return (
                conn.execute('SELECT COUNT(*) FROM timeline_versions WHERE candidate_id IS NULL').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM edit_operations').fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM edit_proposals').fetchone()[0],
                conn.execute("SELECT COUNT(*) FROM task_events WHERE event_type='timeline_candidate_adopted'").fetchone()[0],
                conn.execute('SELECT COUNT(*) FROM device_work_requests').fetchone()[0],
            )

    try:
        # Like a formal edit, the live montage projection has a different write timestamp.
        _, live = repo.montage_plan_adoption.save_creation_task_montage_plan('local', 'task', original['id'], original)
        assert live == {**original, 'updated_at': live['updated_at']} and live['updated_at'] != original['updated_at']
        repo._save_asset(replace(repo.get_asset('local', 'asset'), owner_user_id='other-member'))
        asset = repo.set_asset_visibility('local', 'asset', 'shared')
        a, b = fork(), fork()
        a_version = crop(a, formal, .35)
        old_a = confirmation(a, a_version)
        a_version = crop(a, a_version, .4)
        a_input = confirmation(a, a_version)
        b_version = crop(b, formal, .8)
        b_before_extra_edit = confirmation(b, b_version)
        b_version = crop(b, b_version, .65, clip='clip_2')
        unchanged = counts()
        assert confirm(a, old_a).status_code == 409
        assert confirm(b, b_before_extra_edit).status_code == 409
        assert '以外' in ok(prepare(b, b_version), 409)['detail']
        b_version = crop(b, b_version, .5, clip='clip_2')
        b_input = confirmation(b, b_version)
        assert ok(prepare(b, b_version))['after']['crop_x'] == .8
        assert confirm(a, b_input).status_code == 409
        assert confirm(b, {**b_input, 'selection_digest': '0' * 64}).status_code == 409
        assert confirm(b, {**b_input, 'expected_timeline_sequence': 99}).status_code == 409
        plan = repo.plan_records.get_creation_plan('local', 'plan')
        sibling = CreationTask('sibling', 'local', 'sibling-plan', 'other work', 'knowledge_explanation',
            owner_user_id=actor.member_id)
        repo.plan_confirmation.confirm_creation_plan(replace(plan, id=sibling.plan_id), sibling,
            verified_owner_user_id=actor.member_id)
        assert confirm(b.replace('/task/', '/sibling/'), b_input).status_code == 404
        with monkeypatch.context() as isolated:
            isolated.setattr(ContentIdentityHttpAdapter, 'assistant_member', lambda *_: 'other-member')
            assert confirm(b, b_input).status_code in {403, 404}
        repo.set_asset_visibility('local', 'asset', 'private')
        revoked = confirm(b, b_input)
        assert revoked.status_code in {403, 409}, revoked.text
        repo.set_asset_visibility('local', 'asset', 'shared')
        repo.update_asset_use_state('local', 'asset', 'excluded')

        assert confirm(b, b_input).status_code == 409
        segment = replace(asset.segments[0], end_seconds=5)
        repo._save_asset(replace(asset, segments=[segment]))
        assert '范围' in ok(confirm(b, b_input), 409)['detail']
        repo._save_asset(asset)
        assert counts() == unchanged
        assert repo.get_creation_task_montage_plan('local', 'task') == live

        result = ok(confirm(b, b_input))
        assert result['candidate_version_id'] == b_version['id']
        assert result['result_sequence'] == 2
        assert counts() == (2, 1, 1, 1, 0)
        assert ok(confirm(b, b_input)) == result
        assert confirm(b, {**b_input, 'request_id': str(uuid4())}).status_code == 409
        assert confirm(a, a_input).status_code == 409
        assert ok(client.get(a))['adoption'] is None
        assert ok(client.get(b))['adoption'] == result

        reopened = ProductionRepository(repo.database_path, repo.video_library_path)
        operation = reopened.edit_records.get_edit_operation('local', result['operation_id'])
        assert operation.operation_type == 'ChangeCrop' and operation.status == 'applied'
        assert operation.payload['candidate_adoption']['candidate_version_id'] == b_version['id']
        version = reopened.timeline_records.get_timeline_version('local', result['result_version_id'])
        assert version.parent_version_id == 'timeline'
        assert version.snapshot['montage']['clips'] == [original['clips'][0],
            {**original['clips'][1], 'crop_x': .8}, *original['clips'][2:]]
        for key in ('audio_timeline', 'narration_audio_path', 'cues', 'graphic_overlays', 'duration_seconds'):
            assert version.snapshot['montage'][key] == original[key]
        assert version.snapshot['montage']['output_path'] is None
        carried, = reopened.subtitle_records.list_subtitle_tracks('local', 'task', timeline_version_id=version.id)
        assert [asdict(cue) for cue in carried.cues] == [asdict(cue) for cue in track.cues]
        history = ok(client.get(task + '/timeline/versions'))
        assert b_version['id'] not in str(history) and a_version['id'] not in str(history)
        restore = ok(client.post(task + '/timeline/undo', json={**binding({'id': version.id, 'sequence': 2}),
            'request_id': str(uuid4()), 'target_operation_id': operation.id}))
        assert restore['operation']['operation_type'] == 'UndoTimelineVersion'
        assert reopened.get_creation_task_montage_plan('local', 'task')['clips'] == original['clips']
        assert reopened.timeline_records.get_timeline_version('local', 'timeline').snapshot['montage'] == original
        assert ok(client.get(a))['version'] == a_version and ok(client.get(b))['version'] == b_version
        assert ok(confirm(b, b_input)) == result
        restored = {'id': restore['operation']['result_version_id'], 'sequence': 3}
        c = fork(restored)
        c_version = crop(c, restored, .6)
        c_target = target(c_version, restored)
        c_selection = ok(client.post(c + '/adoption/prepare', json=c_target))
        assert confirm(c, {**c_target, 'selection_digest': c_selection['selection_digest'],
            'request_id': b_input['request_id']}).status_code == 409
        assert ok(client.get(c))['adoption'] is None
        assert counts() == (3, 2, 2, 1, 0)
    finally:
        runtime.stop(0)


def test_adopt_two_saved_crops_atomically_and_restore_once(tmp_path, monkeypatch):
    main, repo, _, _, runtime, client, original, _, _ = _visual_scope_case(tmp_path, monkeypatch)
    task = "/api/creation-tasks/task"
    collection = task + "/timeline/candidates"
    monkeypatch.setattr(main, "_creation_runtime", lambda: pytest.fail("candidate adoption is a metadata transaction"))

    def ok(response, status=200):
        assert response.status_code == status, response.text
        return response.json()

    def bind(version):
        return {"expected_timeline_version_id": version["id"], "expected_timeline_sequence": version["sequence"]}

    def fork():
        created = ok(client.post(collection, json={**bind(base), "candidate_id": str(uuid4())}))
        return f"{collection}/{created['id']}", created["version"]

    def crop(path, version, clip, x):
        return ok(client.post(path + "/edits/exact", json={**bind(version), "request_id": str(uuid4()),
            "operation_type": "ChangeCrop", "clip_id": clip, "fit_mode": "fill", "crop_x": x, "crop_y": .5}))["version"]

    def prepare(path, version):
        target = {**bind(version), "base_version_id": base["id"], "base_sequence": base["sequence"], "all_crops": True}
        selection = ok(client.post(path + "/adoption/prepare", json=target))
        return selection, {**target, "selection_digest": selection["selection_digest"], "request_id": str(uuid4())}

    def counts():
        with repo._connect() as conn:
            tables = ("edit_intents", "edit_proposals", "edit_operations", "timeline_versions",
                "task_events", "device_work_requests")
            return tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in tables)

    try:
        source = repo.get_asset("local", "asset")
        second_path = repo.video_library_path / "second.mp4"
        copy2(source.path, second_path)
        second = repo._save_asset(replace(source, id="second", path=str(second_path), file_name=second_path.name,
            owner_user_id="other-member", visibility="shared",
            segments=[replace(source.segments[0], id="second-segment", asset_id="second")]))
        reference = repo.workspace_media_reference_for_asset("local", second.id)
        with repo._connect() as conn:
            repo._claim_workspace_media_reference(conn, workspace_id="local", reference=reference)
        montage = deepcopy(original)
        montage["clips"][2].update(asset_id=second.id, segment_id="second-segment", source_path=reference)
        _, live = repo.montage_plan_adoption.save_creation_task_montage_plan("local", "task", montage["id"], montage)
        base = {"id": "two-crop-base", "sequence": 2}
        repo.timeline_records.create_timeline_version(TimelineVersion(base["id"], "local", "task", "timeline",
            base["sequence"], {"montage": live}, None))

        path, head = fork()
        head = crop(path, head, "clip_2", .78)
        _, old = prepare(path, head)
        head = crop(path, head, "clip_1", .31)
        selection, confirm = prepare(path, head)
        changes = selection["changes"]
        assert [(c["clip_id"], c["clip_number"], c["before"]["crop_x"], c["after"]["crop_x"]) for c in changes] == [
            ("clip_1", 2, .5, .31), ("clip_2", 3, .5, .78)]
        other, other_head = fork()
        other_head = crop(other, other_head, "clip_3", .6)
        _, other_confirm = prepare(other, other_head)
        oversized, oversized_head = fork()
        for clip_id in ("clip_1", "clip_2", "clip_3"):
            oversized_head = crop(oversized, oversized_head, clip_id, .6)
        oversized_input = {**bind(oversized_head), "base_version_id": base["id"], "base_sequence": 2, "all_crops": True}
        ok(client.post(oversized + "/adoption/prepare", json=oversized_input), 409)

        unchanged = counts()
        adopt_url = path + "/adoption/confirm"
        ok(client.post(adopt_url, json=old), 409)
        ok(client.post(adopt_url, json={**confirm, "all_crops": False, "clip_id": "clip_1"}), 409)
        ok(client.post(adopt_url, json={**confirm, "all_crops": True, "clip_id": "clip_1"}), 422)
        ok(client.post(adopt_url, json={**confirm, "selection_digest": "0" * 64}), 409)
        repo.set_asset_visibility("local", second.id, "private")
        ok(client.post(adopt_url, json=confirm), 409)
        repo.set_asset_visibility("local", second.id, "shared")
        ok(client.post(adopt_url, json=confirm), 409)
        second = repo.get_asset("local", second.id)
        _, confirm = prepare(path, head)
        repo._save_asset(replace(second, segments=[replace(second.segments[0], end_seconds=.5)]))
        assert "范围" in ok(client.post(adopt_url, json=confirm), 409)["detail"]
        assert counts() == unchanged and repo.get_creation_task_montage_plan("local", "task") == live
        assert ok(client.get(path))["adoption"] is None

        repo._save_asset(second)
        _, confirm = prepare(path, head)
        persist = MetadataEditUseCase.persist_montage_edit

        def change_source_after_prepare(case, **kwargs):
            second_path.write_bytes(second_path.read_bytes() + b"\x00")
            return persist(case, **kwargs)

        with monkeypatch.context() as changed_on_commit:
            changed_on_commit.setattr(MetadataEditUseCase, "persist_montage_edit", change_source_after_prepare)
            ok(client.post(adopt_url, json=confirm), 409)
        assert counts() == unchanged and repo.get_creation_task_montage_plan("local", "task") == live
        copy2(source.path, second_path)
        _, confirm = prepare(path, head)
        result = ok(client.post(adopt_url, json=confirm))
        assert result["changes"] == changes and result["result_sequence"] == 3
        after = counts()
        assert after[:4] == tuple(count + 1 for count in unchanged[:4])
        assert after[-1] == unchanged[-1]
        assert ok(client.post(adopt_url, json=confirm)) == result
        ok(client.post(other + "/adoption/confirm", json=other_confirm), 409)
        assert counts() == after
        persisted = repo.timeline_records.get_timeline_version("local", result["result_version_id"])
        expected = deepcopy(live["clips"])
        expected[1]["crop_x"], expected[2]["crop_x"] = .31, .78
        assert persisted.snapshot["montage"]["clips"] == expected
        for field in ("audio_timeline", "narration_audio_path", "graphic_overlays", "cues", "duration_seconds"):
            assert persisted.snapshot["montage"][field] == live[field]
        undo = ok(client.post(task + "/timeline/undo", json={**bind({"id": persisted.id, "sequence": 3}),
            "request_id": str(uuid4()), "target_operation_id": result["operation_id"]}))["operation"]

        reopened = ProductionRepository(repo.database_path, repo.video_library_path)
        restored = reopened.timeline_records.get_timeline_version("local", undo["result_version_id"])
        assert restored.parent_version_id == persisted.id and restored.sequence == 4
        assert restored.snapshot["montage"]["clips"] == live["clips"]
        assert reopened.edit_records.get_edit_operation("local", result["operation_id"]).payload["changes"] == changes
        assert ok(client.get(path))["adoption"] == result
        assert ok(client.post(adopt_url, json=confirm)) == result
    finally:
        runtime.stop(0)
        client.close()
