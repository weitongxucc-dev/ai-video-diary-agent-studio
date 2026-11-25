"""Same-work section candidates through the shipped routes and controlled execution ports."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import subprocess
from types import SimpleNamespace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.creation.editing.metadata_use_case import MetadataEditUseCase
from app.creation.montage_stage_use_case import MontageStageUseCase
from app.creation_execution import submit_creation_stage
from app.knowledge_files import KnowledgeSection
from app.montage import creation_task_montage_plan_from_payload, creation_task_montage_plan_to_payload
from app.source_repository import SourceRepository
from app.source_types import KnowledgeItem
from test_creation_execution import _segment_units_case


def test_fixed_manual_page_candidates_require_trusted_desktop_to_adopt(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("This revision test may not start a model, FFmpeg or a device process")

    monkeypatch.setattr(subprocess, 'Popen', forbidden)
    case = _segment_units_case(tmp_path, monkeypatch, declared_source=True)
    repo, task_id, actor = case.repo, case.task_id, case.actor
    member = actor.member_id
    task_root = f'/api/creation-tasks/{task_id}'
    sources = SourceRepository(repo.database_path)
    case.segments.source_repository = sources
    manual_workspace = 'qa-manual-owner'
    sources.create_knowledge_item_owned(KnowledgeItem(id='manual-zx', workspace_id=manual_workspace,
        content_type='file', title='QA 型号说明书', body='合成资料正文',
        file_reference='/internal/qa-only.pdf', authorization='user-provided', ownership='user-owned',
        source_item_id=None, version='1', sections=(KnowledgeSection('第3页', '型号', '型号 ZX-410，仅适用于干燥环境。'),
            KnowledgeSection('第4页', '近似型号', 'ZX-401'))), verified_user_id=member)
    sources.edit_knowledge_item(manual_workspace, 'manual-zx', expected_version='1', title='第二版说明书',
        body='QA 新版', verified_user_id=member)
    grant = sources.grant_knowledge_item(manual_workspace, 'manual-zx', 'local', user_id=member)
    reference = {'kind': 'knowledge', 'knowledge_item_id': 'manual-zx', 'version': '1',
        'locator': '第3页', 'label': '型号说明书第3页'}

    conversations = ConversationRepository(repo.database_path)
    chat = conversations.create('local', member, 'task', task_id)
    runtime = SimpleNamespace(scheduler=case.scheduler, wake=lambda: None)
    from app.local_members import LocalMemberService
    from app.task_edit_confirmation import DesktopSectionConfirmation

    members = LocalMemberService(repo.database_path)
    with repo.connection_factory() as conn:
        local_token = conn.execute('SELECT token FROM local_members WHERE member_id=?', (member,)).fetchone()['token']
    _, other_token = members.whoami(None)
    session = {'X-Local-Member-Token': local_token}
    control = {'X-Yaohe-Desktop-Control': 'a' * 64}
    monkeypatch.setattr(main, 'production_repo', repo)
    monkeypatch.setattr(main, 'conversation_repo', conversations)
    monkeypatch.setattr(main, 'source_repository', sources)
    monkeypatch.setattr(main, 'current_workspace_id', lambda _: 'local')
    monkeypatch.setattr(main, '_content_identity_http_adapter', lambda: SimpleNamespace(
        reader=lambda *_: (member, False), assistant_member=lambda *_: member, visible=lambda *_: True))
    monkeypatch.setattr(main, 'local_members_service', members)
    monkeypatch.setattr(main, '_require_automation_task_open', lambda *_: None)
    monkeypatch.setattr(main, '_segment_production_use_case', lambda: case.segments)
    monkeypatch.setattr(main, '_creation_runtime', lambda: runtime)
    monkeypatch.setattr(main, '_desktop_section_confirmation', DesktopSectionConfirmation())
    monkeypatch.setattr(main.app.state, 'desktop_control_token', control['X-Yaohe-Desktop-Control'])
    monkeypatch.setattr(main, '_automation_runtime', lambda: SimpleNamespace(require_task_open=lambda *_: None))
    api = FastAPI()
    api.middleware('http')(main.auth_boundary_middleware)
    api.routes.extend(route for route in main.app.routes if route.name in {
        'creation_task_input_understanding', 'creation_task_narration_semantic_review', 'revise_creation_task_segment',
        'creation_task_segment_request', 'creation_task_segment_video', 'assistant_knowledge_reference',
        'get_section_candidate_confirmation', 'confirm_creation_task_edit'})
    client = TestClient(api, headers=session, client=('127.0.0.1', 58585))
    host = TestClient(api, client=('127.0.0.1', 58586))
    submit_creation_stage(case.execution, case.scheduler, case.job, actor)
    for _ in range(5):
        case.executor.execute(case.scheduler.pick_and_grant(), case.scheduler)
    # The test imports the composed timeline below. Leave no work queued for the
    # initial job while exercising the normal segment revision submission.
    for request in repo.job_records.list_creation_job_request_states('local', case.job.id):
        if request['state'] in {'waiting', 'blocked'}:
            case.scheduler.cancel(request['id'], actor)
    task = repo.task_records.get_creation_task('local', task_id)
    first_state = repo.section_inputs.stage_segment_state('local', task_id, 'section_1')
    template = creation_task_montage_plan_from_payload(first_state['montage'])
    full = MontageStageUseCase.include_adopted_sections(SimpleNamespace(repository=repo), task,
        replace(template, id='existing_rough_cut',
            narration_audio_path=repo.narration_artifacts.narration_artifact_reference(task.narration_cache_key)))
    repo.montage_plan_adoption.save_creation_task_montage_plan('local', task_id, full.id,
        creation_task_montage_plan_to_payload(full))
    edits = MetadataEditUseCase(repo, case.segments.artifact_store, output_root=tmp_path / 'review', audio_service=None)
    base = edits.ensure_montage_base('local', task, full)
    authority = main._task_authorizations_for_goal()
    authority.ensure(repo.task_records.get_creation_task('local', task_id), actor, interactive=True)
    assert authority.verdict(actor, task_id, 'narration_edit', references={'asset:section_video'}) == 'ready'
    original_plan = repo.plan_records.get_creation_plan('local', task.plan_id)
    original_script = repo.script_records.get_creation_task_script('local', case.script_id)
    original_other = case.segments.context('local', task_id, 'section_2', actor)
    original_section = case.segments.context('local', task_id, 'section_1', actor)
    replacement = '原文。型号 ZX-410，仅适用于干燥环境。'
    review_url = task_root + '/assistant/narration-semantic-review'
    revision_url = task_root + '/segments/section_1/revision'

    def start_turn(message):
        saved = conversations.get(chat['id'], 'local', member)
        draft = conversations.save_draft(chat['id'], 'local', member, message, saved['draft_version'])
        turn, created = conversations.create_turn_idempotent(chat['id'], 'local', member,
            message, str(uuid4()), draft['draft_version'], request_context={'kind': 'task', 'task_id': task_id,
                'target_snapshot': {'timeline_version_id': base.id, 'timeline_sequence': base.sequence,
                    'selected_references': [reference]}})
        assert created
        return turn['id'], {'assistant_turn_id': turn['id'], 'input_id': turn['id'],
            'request_id': str(uuid4()), 'section_id': 'section_1', 'script_id': case.script_id,
            'expected_segment_version': original_section['segment_version'], 'base_version_id': base.id,
            'dependency_digest': original_section['dependency_digest'],
            'source_text_sha256': original_section['source_text_sha256'],
            'replacement_text_sha256': hashlib.sha256(replacement.encode()).hexdigest(),
            'protected_rules': []}

    def review(input_fields):
        return client.post(review_url, json={**input_fields, 'conclusion': 'preserved',
            'findings': [{'rule_id': 'original', 'before': '原文', 'after': '原文',
                'assessment': 'preserved', 'reason': 'QA 声明的对照，不是模型事实验收'}]})

    def submit(fields, receipt, adoption_mode):
        return client.post(revision_url, json={**fields, 'semantic_review_ref': receipt['semantic_review_ref'],
            'replacement_text': replacement, 'adoption_mode': adoption_mode})

    def settle_queued(response):
        assert response.status_code == 200, response.text
        queued = response.json()
        assert queued['status'] == 'queued'
        for _ in range(2):
            grant = case.scheduler.pick_and_grant()
            assert grant is not None and grant.owner_id == queued['job_id']
            case.executor.execute(grant, case.scheduler)
        settled = client.get(task_root + '/segment-requests/' + queued['request_id']).json()
        assert settled['status'] == 'succeeded', settled
        return settled

    try:
        before = repo.job_records.list_job_runs('local', task_id)
        for message in (
            '请不要改写第1段，仅解释这页说明书的影响。',
            '只解释这页说明书的影响。',
            '采用前先让我确认第 1 段。',
            '请采用说明书第 3 页，但先让我确认。',
            '请采用说明书第 3 页，等待我授权。',
            '如果审核通过，再采用说明书第 3 页。',
            '同事说「请根据这页说明书修改第1段并保存。」我想了解他的意思。',
            'Should we apply the change from this manual?',
            'Please apply page 3 after I approve.',
        ):
            turn, fields = start_turn(message)
            interpretation = client.post(task_root + '/assistant/input-understanding', json={
                'assistant_turn_id': turn, 'input_id': fields['input_id'],
                'kind': 'adoption', 'summary': 'QA 模拟误判为采用',
                'target_refs': ['section:section_1']})
            assert interpretation.status_code == 200, interpretation.text
            reviewed = review(fields)
            assert reviewed.status_code == 200, reviewed.text
            assert submit(fields, reviewed.json(), 'apply').status_code == 403, message
            assert repo.job_records.list_job_runs('local', task_id) == before
            assert repo.task_records.get_creation_task('local', task_id).current_timeline_version_id == base.id
            conversations.finish_turn(turn, 'completed')

        assert base.sequence == 3
        turn, command = start_turn('请根据这页说明书修改第1段并保存。')
        assert review({**command, 'base_version_id': 'stale-version'}).status_code == 409
        assert review({**command, 'knowledge_evidence': [{'locator': '第4页'}]}).status_code == 409
        saved = review(command)
        assert saved.status_code == 200, saved.text
        reviewed = saved.json()
        attached, = reviewed['knowledge_evidence']
        assert (attached['object_id'], attached['version_or_identity'], attached['locator']) == ('manual-zx', '1', '第3页')
        assert 'ZX-401' not in saved.text and 'qa-only.pdf' not in saved.text
        assert submit(command, reviewed, 'apply').status_code == 403
        assert submit({**command, 'request_id': str(uuid4())}, reviewed, 'candidate').status_code == 409
        sources.set_knowledge_item_use_state(manual_workspace, 'manual-zx', 'excluded')
        assert review(command).json() == reviewed
        assert submit(command, reviewed, 'candidate').status_code == 409
        assert repo.job_records.list_job_runs('local', task_id) == before
        sources.set_knowledge_item_use_state(manual_workspace, 'manual-zx', 'available')

        receipt = settle_queued(submit(command, reviewed, 'candidate'))
        assert receipt['adoption']['status'] == 'candidate'
        assert receipt['section_id'] == 'section_1'
        assert receipt['result']['text'] == replacement
        assert receipt['knowledge_evidence'] == receipt['result']['knowledge_evidence'] == [attached]
        assert client.get(receipt['result']['video']['url']).status_code == 200
        jobs = repo.job_records.list_job_runs('local', task_id)
        assert submit(command, reviewed, 'candidate').json() == receipt
        assert submit(command, reviewed, 'apply').status_code == 409
        assert repo.job_records.list_job_runs('local', task_id) == jobs
        conversations.finish_turn(turn, 'completed')

        turn, english = start_turn('Please revise section 1 using this manual page and save the change.')
        english_review = review(english).json()
        assert submit(english, english_review, 'apply').status_code == 403
        second = settle_queued(submit(english, english_review, 'candidate'))
        assert second['adoption']['status'] == 'candidate' and second['job_id'] != receipt['job_id']
        assert second['result']['knowledge_evidence'] == [attached]
        conversations.finish_turn(turn, 'completed')

        assert repo.task_records.get_creation_task('local', task_id).current_timeline_version_id == base.id
        assert repo.timeline_records.get_timeline_version('local', base.id) == base
        assert repo.plan_records.get_creation_plan('local', task.plan_id) == original_plan
        assert repo.script_records.get_creation_task_script('local', case.script_id) == original_script
        assert case.segments.context('local', task_id, 'section_1', actor) == original_section
        assert case.segments.context('local', task_id, 'section_2', actor) == original_other
        assert repo.edit_records.list_edit_operations('local', task_id) == []

        sources_url = '/api/assistant/knowledge-references/' + attached['object_id']
        params = {'version': attached['version_or_identity'], 'locator': attached['locator']}
        source_read = client.get(sources_url, params=params)
        assert source_read.status_code == 200, source_read.text
        page, = source_read.json()['sections']
        assert page['locator'] == '第3页' and page['text'] == '型号 ZX-410，仅适用于干燥环境。'

        proposal_id = receipt['result']['adoption_proposal_id']
        second_id = second['result']['adoption_proposal_id']
        confirmation_id = str(uuid4())
        edit_url = f'{task_root}/edits/{proposal_id}'
        second_url = f'{task_root}/edits/{second_id}'
        summary_url = edit_url + f'/section-candidate-confirmation?request_id={confirmation_id}'
        assert client.get(summary_url, headers={'X-Local-Member-Token': ''}).status_code == 403
        assert client.get(summary_url, headers={'X-Local-Member-Token': other_token}).status_code == 403
        draft_read = client.get(summary_url)
        assert draft_read.status_code == 200, draft_read.text
        displayed = draft_read.json()
        assert displayed['status'] == 'proposed' and displayed['after_text'] == replacement
        assert displayed['before_text'] == original_section['text']
        assert displayed['knowledge_evidence'] == [attached] and displayed['job_id'] == receipt['job_id']
        assert displayed['publication_id'] == receipt['result']['segment_version'] and displayed['base_version_id'] == base.id
        assert 'selection_proof' not in displayed and 'qa-only.pdf' not in draft_read.text
        body = {'request_id': confirmation_id, 'expected_base_version_id': displayed['base_version_id'],
                'expected_input_digest': displayed['selection_digest'], 'desktop_selection_id': displayed['selection_id']}
        assert client.post(edit_url + '/confirm', json=body).status_code == 403
        assert client.post(edit_url + '/confirm', json={'request_id': confirmation_id}).status_code == 403
        assert host.post(second_url + '/confirm', headers=control, json=body).status_code == 404
        assert host.post(edit_url.replace(task_id, 'task_other') + '/confirm', headers=control,
                         json=body).status_code == 404
        assert host.post(edit_url + '/confirm', headers=control,
                           json={**body, 'expected_base_version_id': 'wrong-version'}).status_code == 409
        rotated = uuid4().hex
        with repo.connection_factory() as conn:
            conn.execute('UPDATE local_members SET token=? WHERE member_id=?', (rotated, member))
        try:
            assert client.get(summary_url).status_code == 401
            current_session = client.get(summary_url, headers={'X-Local-Member-Token': rotated}).json()
            assert current_session['selection_digest'] == displayed['selection_digest']
            assert current_session['session_fingerprint'] != displayed['session_fingerprint']
            assert host.post(edit_url + '/confirm', headers=control, json=body).status_code == 403
        finally:
            with repo.connection_factory() as conn:
                conn.execute('UPDATE local_members SET token=? WHERE member_id=?', (local_token, member))
        remote = TestClient(api, client=('192.0.2.42', 58587))
        try:
            assert remote.post(edit_url + '/confirm', headers=control, json=body).status_code == 403
        finally:
            remote.close()
        with monkeypatch.context() as expired_clock:
            expired_clock.setattr('app.task_edit_confirmation.utc_now',
                lambda: datetime.fromisoformat(displayed['expires_at']) + timedelta(seconds=1))
            assert host.post(edit_url + '/confirm', headers=control, json=body).status_code == 409
        renewed = client.get(summary_url).json()
        assert renewed['selection_id'] != displayed['selection_id']
        assert renewed['session_fingerprint'] == displayed['session_fingerprint']
        assert renewed['selection_digest'] == displayed['selection_digest']
        body['desktop_selection_id'] = renewed['selection_id']
        candidate_video = case.segments.artifact_store.resolve_file(receipt['result']['video']['artifact_ref'])
        original_bytes = candidate_video.read_bytes()
        candidate_video.write_bytes(b'x' * len(original_bytes))
        try:
            assert host.post(edit_url + '/confirm', headers=control, json=body).status_code == 409
        finally:
            candidate_video.write_bytes(original_bytes)
        assert repo.edit_records.list_edit_operations('local', task_id) == []

        _, pending = start_turn('按该页准备第 1 段的修订候选。')
        pending_review = review(pending).json()
        jobs = repo.job_records.list_job_runs('local', task_id)
        sources.revoke_knowledge_grant(manual_workspace, 'manual-zx', grant.id, user_id=member)
        assert client.get(sources_url, params=params).status_code == 404
        assert client.get(summary_url).status_code == 409
        assert host.post(edit_url + '/confirm', json=body, headers=control).status_code == 409
        assert submit(pending, pending_review, 'candidate').status_code == 409
        assert review(pending).json() == pending_review
        assert client.get(task_root + '/segment-requests/' + receipt['request_id']).json() == receipt
        sources.grant_knowledge_item(manual_workspace, 'manual-zx', 'local', user_id=member)
        second_request_id = str(uuid4())
        second_summary = client.get(second_url + f'/section-candidate-confirmation?request_id={second_request_id}')
        assert second_summary.status_code == 200, second_summary.text
        other = second_summary.json()
        execution_counts = (len(case.generated), len(case.merged))
        original_artifacts = repo.execution_records.list_artifact_records('local', task_id)
        # The native confirmation port is isolated. The real HTTP, request claims,
        # revision checkpoint, EditOperation and timeline transaction are exercised.
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: host.post(edit_url + '/confirm', headers=control, json=body), range(2)))
        assert all(result.status_code == 200 for result in results), [result.text for result in results]
        assert results[0].json() == results[1].json()
        applied = results[0].json()['operation']
        operation = repo.edit_records.get_edit_operation('local', applied['id'])
        version = repo.timeline_records.get_timeline_version('local', applied['result_version_id'])
        assert version.parent_version_id == base.id and version.sequence == base.sequence + 1
        assert operation.payload['confirmation_request_id'] == confirmation_id
        assert operation.payload['candidate_job_id'] == receipt['job_id']
        assert operation.payload['after_publication'] == receipt['result']['segment_version']
        assert operation.payload['knowledge_evidence'] == [attached]
        assert version.snapshot['stage_segments']['section_2'] == base.snapshot['stage_segments']['section_2']
        assert version.snapshot['stage_segments']['section_1'] == operation.payload['after_publication']
        assert case.segments.context('local', task_id, 'section_1', actor)['text'] == replacement
        assert case.segments.context('local', task_id, 'section_2', actor)['text'] == original_other['text']
        assert client.get(receipt['result']['video']['url']).status_code == 200
        reread = client.get(task_root + '/segment-requests/' + receipt['request_id']).json()
        assert reread['adoption']['status'] == 'current' and reread['result'] == receipt['result']
        recovered = client.get(summary_url).json()
        assert recovered['status'] == 'applied' and 'selection_id' not in recovered
        assert recovered['result'] == results[0].json()
        for key in ('proposal_id', 'request_id', 'job_id', 'publication_id', 'section_id',
                    'base_version_id', 'selection_digest', 'session_fingerprint'):
            assert recovered[key] == displayed[key]
        assert len(repo.edit_records.list_edit_operations('local', task_id)) == 1
        assert (len(case.generated), len(case.merged)) == execution_counts
        assert all(item in repo.execution_records.list_artifact_records('local', task_id) for item in original_artifacts)
        assert host.post(second_url + '/confirm', headers=control, json={'request_id': second_request_id,
            'expected_base_version_id': other['base_version_id'], 'expected_input_digest': other['selection_digest'],
            'desktop_selection_id': other['selection_id']}).status_code == 409
        expired = submit(pending, pending_review, 'candidate')
        assert expired.status_code == 409
        assert repo.job_records.list_job_runs('local', task_id) == jobs
        repo.update_asset_use_state('local', 'section_video', 'excluded')
        assert client.get(summary_url).status_code == 403
        assert host.post(edit_url + '/confirm', headers=control, json=body).status_code == 403
        assert len(repo.edit_records.list_edit_operations('local', task_id)) == 1
        assert repo.task_records.get_creation_task('local', task_id).current_timeline_version_id == version.id
    finally:
        host.close()
        client.close()
        case.scheduler.close()
