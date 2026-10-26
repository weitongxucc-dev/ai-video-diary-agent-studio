"""One real button request, Conversation and Run through bounded draft-only writes."""

import json
from uuid import uuid4

import httpx
import pytest

import app.main as main
from app.conversation_repository import ConversationRepository
from app.creation.script_generation_intent import ScriptGenerationIntent
from app.creation_execution import CreationExecutionActor
from app.schemas import (
    CreationTaskScriptWindowStartRequest,
    CreationTaskScriptSectionsPatchRequest,
    CreationTaskScriptConfirmSavedRequest,
)
from app.topics import OpenAICompatibleCreationTaskScriptGenerator
from test_creation_task_script_grounding import (
    script_domain_state,  # noqa: F401
    FACT,
    _review_tool_response,
)  # noqa: F401


@pytest.fixture
def generation_state(script_domain_state, monkeypatch):  # noqa: F811
    state = script_domain_state
    repo = state.repository
    member = state.owner.member_id
    with repo._connect() as conn:
        p = json.loads(
            conn.execute(
                "SELECT payload FROM creation_plans WHERE id='plan'"
            ).fetchone()[0]
        )
        p["template_settings"] = {"script_section_count": 45}
        conn.execute(
            "UPDATE creation_plans SET payload=? WHERE id='plan'", (json.dumps(p),)
        )
    token = state.token

    def identity(actor):
        actual = main.local_members_service.resolve_member(token)
        return (
            "ready"
            if actual is not None
            and actual.member_id == (actor.user_id or actor.member_id)
            else "denied"
        )

    owner = ScriptGenerationIntent(
        repo,
        ConversationRepository(repo.database_path),
        verify_identity=identity,
        source_inputs=main._creation_task_topic_inputs,
        route_snapshot={"provider": "custom", "execution_location": "cloud"},
    )
    request_id = str(uuid4())
    initial, _ = owner.start(
        "local", "task", request_id, actor=CreationExecutionActor("local", member)
    )
    calls = []

    def review(request):
        data = json.loads(json.loads(request.content)["messages"][1]["content"])
        calls.append(data)
        return _review_tool_response(
            {
                "sections": [
                    {
                        "index": p["index"],
                        "supports": [
                            {
                                "candidate_quote_id": p["content"][0]["quote_id"],
                                "source_quote_id": p["allowed_source_quote_ids"][
                                    "evidence_1"
                                ][0],
                            }
                        ],
                    }
                    for p in data["script"]["sections"]
                ]
            }
        )

    checker = OpenAICompatibleCreationTaskScriptGenerator(
        "https://models.invalid/v1", "same-model", transport=httpx.MockTransport(review)
    )
    monkeypatch.setattr(
        main, "_creation_task_script_generator_for", lambda settings: checker
    )
    case = main._script_draft_use_case(
        "local",
        validate_inputs=lambda ws, plan, refs: main._validate_script_input_sources(
            ws, plan, refs, member
        ),
    )
    case.read_generation_request = owner.read_generation_request
    case.read_generation_result = owner.read
    case.bind_generation_result = owner.bind_generation_result
    return state, owner, case, initial, request_id, calls


def parts(state, start, end):
    return [
        {
            "id": f"part-{i}",
            "title": f"段{i}",
            "content": FACT,
            "visual_prompt": "计划画面",
            "evidence_reference_ids": [state.refs[0]["reference_id"]],
        }
        for i in range(start, end)
    ]


def test_original_generation_run_20_20_5_becomes_ready_without_adopting(
    generation_state, monkeypatch
):
    state, owner, case, initial, request_id, calls = generation_state
    member = state.owner.member_id
    origin = initial["source"]

    def read_origin(_):
        return owner.read("local", "task", request_id, member)["source"]

    common = {
        "member_id": member,
        "generation_request_id": request_id,
        "instruction_origin": origin,
        "reread_origin": read_origin,
    }
    store = state.repository.script_records
    with monkeypatch.context() as bounded:
        bounded.setattr(
            store,
            "get_creation_task_script",
            lambda *a, **k: pytest.fail("no full script read"),
        )
        bounded.setattr(
            store,
            "_creation_task_script_from_payload",
            lambda *a, **k: pytest.fail("no full parser"),
        )
        first_payload = CreationTaskScriptWindowStartRequest(
            request_id=uuid4(), title="完整原稿", sections=parts(state, 0, 20)
        )
        first = case.start_generation_window("local", "task", first_payload, **common)
        now = owner.read("local", "task", request_id, member)
        assert (
            now["run_id"] == initial["run_id"]
            and now["script_id"] == first["script_id"]
            and now["run_status"] == "running"
        )
        assert (
            now["script"]["sections_total_count"] == 20
            and not now["script"]["draft_complete"]
        )
        for start, end in [(20, 40), (40, 45)]:
            update = CreationTaskScriptSectionsPatchRequest(
                request_id=uuid4(),
                expected_updated_at=first["updated_at"],
                sections=parts(state, start, end),
            )
            first = case.patch("local", "task", first["script_id"], update, **common)
        finish_payload = CreationTaskScriptConfirmSavedRequest(
            request_id=uuid4(), expected_updated_at=first["updated_at"]
        )
        result = case.finish_generation_draft(
            "local", "task", first["script_id"], finish_payload, **common
        )
        assert result["status"] == "draft_ready"
        assert (
            case.finish_generation_draft(
                "local", "task", first["script_id"], finish_payload, **common
            )
            == result
        )
        assert (
            case.start_generation_window("local", "task", first_payload, **common)[
                "script_id"
            ]
            == result["script_id"]
        )
    current = owner.read("local", "task", request_id, member)
    assert current["status"] == "draft_ready" and current["run_status"] == "succeeded"
    assert (
        current["script"]["draft_complete"]
        and current["script"]["selection_status"] == "draft"
        and not current["script"]["adoption"]
    )
    task = state.repository.get_creation_task("local", "task")
    assert (
        task.stage == "script"
        and task.stage_status == "waiting_for_user"
        and task.confirmed_script_id is None
    )
    with state.repository._connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM script_generation_runs").fetchone()[0]
            == 1
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM creation_task_script_sections"
            ).fetchone()[0]
            == 45
        )
        assert (
            conn.execute(
                "SELECT user_message FROM assistant_conversation_turns WHERE id=?",
                (initial["turn_id"],),
            ).fetchone()[0]
            == ""
        )
    assert len(calls) == 6


def test_first_generation_binding_failure_rolls_back_window_not_original_run(
    generation_state, monkeypatch
):
    state, owner, case, initial, request_id, calls = generation_state
    member = state.owner.member_id

    def broken(*args, **kwargs):
        raise RuntimeError("synthetic binding failure")

    case.bind_generation_result = broken
    with pytest.raises(RuntimeError, match="binding failure"):
        case.start_generation_window(
            "local",
            "task",
            CreationTaskScriptWindowStartRequest(
                request_id=uuid4(), title="部分稿", sections=parts(state, 0, 20)
            ),
            member_id=member,
            generation_request_id=request_id,
        )
    facts = owner.read("local", "task", request_id, member)
    assert (
        facts["run_id"] == initial["run_id"]
        and facts["run_status"] == "running"
        and facts["script"] is None
    )
    with state.repository._connect() as conn:
        assert (
            conn.execute("SELECT count(*) FROM script_generation_runs").fetchone()[0]
            == 1
        )
        assert (
            conn.execute("SELECT count(*) FROM creation_task_scripts").fetchone()[0]
            == 0
        )
        assert (
            conn.execute(
                "SELECT count(*) FROM creation_task_script_sections"
            ).fetchone()[0]
            == 0
        )
    assert len(calls) == 1


def test_generation_stop_retains_partial_and_rejects_late_patch_before_model(
    generation_state,
):
    state, owner, case, initial, request_id, calls = generation_state
    member = state.owner.member_id
    first = case.start_generation_window(
        "local",
        "task",
        CreationTaskScriptWindowStartRequest(
            request_id=uuid4(), title="部分稿", sections=parts(state, 0, 20)
        ),
        member_id=member,
        generation_request_id=request_id,
    )
    owner.request_stop("local", "task", request_id, member)
    with pytest.raises(PermissionError):
        case.patch(
            "local",
            "task",
            first["script_id"],
            CreationTaskScriptSectionsPatchRequest(
                request_id=uuid4(),
                expected_updated_at=first["updated_at"],
                sections=parts(state, 20, 40),
            ),
            member_id=member,
            generation_request_id=request_id,
        )
    owner.fail(
        "local",
        "task",
        request_id,
        member,
        status="stopped",
        reason="script_generation_stopped",
        impact="保留已保存窗口",
        repair_action="查询原请求",
        retryable=False,
    )
    facts = owner.read("local", "task", request_id, member)
    assert (
        facts["run_id"] == initial["run_id"]
        and facts["run_status"] == "failed"
        and facts["script"]["sections_total_count"] == 20
    )
    assert len(calls) == 1


def test_unknown_window_prevents_draft_ready_and_other_generation_cannot_read_receipt(
    generation_state,
):
    from app.creation.script_draft_use_case import ScriptDraftRejected

    state, owner, case, initial, request_id, calls = generation_state
    member = state.owner.member_id
    first = case.start_generation_window(
        "local",
        "task",
        CreationTaskScriptWindowStartRequest(
            request_id=uuid4(), title="部分稿", sections=parts(state, 0, 20)
        ),
        member_id=member,
        generation_request_id=request_id,
    )
    for start, end in [(20, 40), (40, 45)]:
        first = case.patch(
            "local",
            "task",
            first["script_id"],
            CreationTaskScriptSectionsPatchRequest(
                request_id=uuid4(),
                expected_updated_at=first["updated_at"],
                sections=parts(state, start, end),
            ),
            member_id=member,
            generation_request_id=request_id,
        )
    pending = str(uuid4())
    state.repository.script_records.claim_script_window_request(
        "local",
        "task",
        first["script_id"],
        "patch",
        pending,
        member,
        "unknown-input",
        submitted_input={},
        generation_request_id=request_id,
    )
    before = len(calls)
    with pytest.raises(ScriptDraftRejected) as failure:
        case.finish_generation_draft(
            "local",
            "task",
            first["script_id"],
            CreationTaskScriptConfirmSavedRequest(
                request_id=uuid4(), expected_updated_at=first["updated_at"]
            ),
            member_id=member,
            generation_request_id=request_id,
        )
    assert failure.value.status_code == 409 and len(calls) == before
    with pytest.raises(KeyError):
        case.window_request_result(
            "local",
            "task",
            pending,
            member_id=member,
            operation="patch",
            script_id=first["script_id"],
            generation_request_id=str(uuid4()),
        )
    state.repository.script_records.end_script_window_request_with_error(
        "local", "task", first["script_id"], "patch", pending, member
    )
    result = case.finish_generation_draft(
        "local",
        "task",
        first["script_id"],
        CreationTaskScriptConfirmSavedRequest(
            request_id=uuid4(), expected_updated_at=first["updated_at"]
        ),
        member_id=member,
        generation_request_id=request_id,
    )
    assert result["status"] == "draft_ready"


def test_ready_binding_failure_keeps_original_run_partial_and_reuses_completed_review(
    generation_state,
):
    state, owner, case, initial, request_id, calls = generation_state
    member = state.owner.member_id
    common = {"member_id": member, "generation_request_id": request_id}
    first = case.start_generation_window(
        "local",
        "task",
        CreationTaskScriptWindowStartRequest(
            request_id=uuid4(), title="原请求草稿", sections=parts(state, 0, 20)
        ),
        **common,
    )
    for start, end in [(20, 40), (40, 45)]:
        first = case.patch(
            "local",
            "task",
            first["script_id"],
            CreationTaskScriptSectionsPatchRequest(
                request_id=uuid4(),
                expected_updated_at=first["updated_at"],
                sections=parts(state, start, end),
            ),
            **common,
        )
    bind = case.bind_generation_result

    def fail_ready(*args, **kwargs):
        if kwargs["draft_ready"]:
            raise RuntimeError("synthetic ready binding failure")
        return bind(*args, **kwargs)

    case.bind_generation_result = fail_ready
    with pytest.raises(RuntimeError, match="ready binding failure"):
        case.finish_generation_draft(
            "local",
            "task",
            first["script_id"],
            CreationTaskScriptConfirmSavedRequest(
                request_id=uuid4(), expected_updated_at=first["updated_at"]
            ),
            **common,
        )
    record = owner.read("local", "task", request_id, member)
    assert (
        record["run_status"] == "running"
        and record["script"]["updated_at"] == first["updated_at"]
    )
    assert not record["script"]["draft_complete"] and record["status"] != "draft_ready"
    task = state.repository.get_creation_task("local", "task")
    assert task.stage_status == "running" and task.confirmed_script_id is None
    assert len(calls) == 6
    case.bind_generation_result = bind
    result = case.finish_generation_draft(
        "local",
        "task",
        first["script_id"],
        CreationTaskScriptConfirmSavedRequest(
            request_id=uuid4(), expected_updated_at=first["updated_at"]
        ),
        **common,
    )
    assert result["status"] == "draft_ready" and len(calls) == 6
