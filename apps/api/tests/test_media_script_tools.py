"""Private recipe metadata on real isolated repositories and small Blob artifacts.

The successful producer is an explicit fixture of the frozen PI05 protocol;
these checks do not claim a production script, device calibration or movie run.
"""

import copy
import hashlib
import io
import json
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from PIL import Image

from app.artifact_store import ArtifactStore
from app.creation_execution import CreationExecutionActor
from app.local_members import LocalMemberService
from app.member_permits import MemberDeviceAccess
from app.models import utc_now
from app.production import (ArtifactRecord, CreationPlan, CreationTask, JobRun,
                            ProductionRepository, TaskEvent, TransformRun)
from app.task_audio import TASK_MEDIA_KIND
from app.media_script_jobs import script_definition
from app.media_script import ScriptInputRevision
from app.schemas import MediaScriptPreviewRequest
from app.media_script_tools import (MediaScriptTools, ScriptToolConflict, ScriptToolError,
                                   validate_parameter_schema, validate_tool_parameters, ScriptToolUnknown)


SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["label"],
    "properties": {
        "label": {"type": "string", "maxLength": 16},
        "gain": {"type": "number", "minimum": 0, "maximum": 2, "default": 1},
        "enabled": {"type": "boolean", "default": True},
        "count": {"type": "integer", "minimum": 1, "maximum": 4, "enum": [1, 2, 4], "default": 2},
    },
}


def test_one_scalar_validator_applies_defaults_without_mutating_inputs():
    schema = copy.deepcopy(SCHEMA)
    parameters = {"label": "actual"}
    assert validate_tool_parameters(schema, parameters) == {"label": "actual", "gain": 1, "enabled": True, "count": 2}
    assert parameters == {"label": "actual"} and schema == SCHEMA
    numeric_enum = {"type":"object", "properties":{"value":{"type":"number", "enum":[1.0]}},
                    "required":["value"], "additionalProperties":False}
    assert validate_tool_parameters(numeric_enum, {"value":1}) == {"value":1}


@pytest.mark.parametrize("parameters", [
    {}, {"label": "too-long-for-limit"}, {"label": 1}, {"label": "x", "unknown": 1},
    {"label": "x", "count": True}, {"label": "x", "count": 1.5}, {"label": "x", "count": 3},
    {"label": "x", "gain": float("inf")}, {"label": "x", "gain": float("nan")},
    {"label": "x", "gain": -1}, {"label": "x", "enabled": 1},
])
def test_scalar_input_type_range_enum_and_nonfinite_rejections(parameters):
    with pytest.raises(ScriptToolError):
        validate_tool_parameters(SCHEMA, parameters)


@pytest.mark.parametrize("rule", [
    {"type":"object"}, {"type":["string","null"]},
    {"type":"number"}, {"type":"number", "minimum":0},
    {"type":"number", "minimum":2, "maximum":1},
    {"type":"number", "minimum":0, "maximum":float("inf")},
    {"type":"number", "enum":[1,1.0]},
    {"type":"string", "maxLength":8, "pattern":".*"},
    {"type":"string", "maxLength":True},
    {"type":"integer", "minimum":1, "maximum":3, "default":0},
    {"type":"boolean", "default":1},
    {"type":"string", "maxLength":2, "default":"long"},
    {"type":"boolean", "readOnly":True},
])
def test_unsupported_schema_and_invalid_defaults_are_explicit(rule):
    schema = {"type":"object", "properties":{"value":rule}, "required":[], "additionalProperties":False}
    with pytest.raises(ScriptToolError):
        validate_parameter_schema(schema)


def test_schema_is_closed_and_bounded_not_full_jsonschema():
    with pytest.raises(ScriptToolError):
        validate_parameter_schema(SCHEMA | {"$schema":"https://example.invalid/schema"})
    with pytest.raises(ScriptToolError):
        validate_parameter_schema(SCHEMA | {"additionalProperties":True})
    with pytest.raises(ScriptToolError):
        validate_parameter_schema(SCHEMA | {"properties":{str(n):{"type":"boolean"} for n in range(17)}})


@pytest.fixture
def case(tmp_path):
    repository = ProductionRepository(tmp_path / "metadata.sqlite", tmp_path / "library")
    members = LocalMemberService(repository.database_path)
    member = members.whoami(None)[0]
    actor = CreationExecutionActor("local", member.member_id)
    plan = CreationPlan(id="plan", workspace_id="local", goal="fixture", name="fixture", target_count=1,
        language="zh-CN", aspect_ratio="9:16", duration_target_seconds=5,
        knowledge_references=[], source_references=[], selected_voice=None, pronunciation_references=[],
        approved_asset_count=0, approved_clip_count=0, material_rule_summary="", model_snapshot=[],
        confirmation_points=[], blockers=[], preflight_fingerprint="fixture")
    task = CreationTask(id="task", workspace_id="local", plan_id="plan", name="fixture",
        scenario="knowledge_explanation", owner_user_id=actor.member_id, visibility="private")
    repository.confirm_creation_plan(plan, task, verified_owner_user_id=actor.member_id)
    store = ArtifactStore(tmp_path / "blobs")
    access = MemberDeviceAccess(members)

    def write(actor, task):
        # Trusted owner callback re-reads canonical task ownership, no caller flag.
        current = repository.get_creation_task(actor.workspace_id, task.id)
        if current.owner_user_id != (actor.user_id or actor.member_id):
            raise PermissionError("current task is not writable")

    def tools():
        return MediaScriptTools(repository, store, verify_identity=access.verify_identity, authorize_task_write=write)

    def producer(number=1, *, owner=None, status="succeeded", kind=TASK_MEDIA_KIND, task_id="task", include_definition=True):
        job = repository.create_job_run(JobRun(id=f"script-job-{number}", workspace_id="local", task_id=task_id,
            kind=kind, idempotency_key=f"script-{number}", input_digest="whole-fixture-job"))
        job = repository.claim_job_run("local", job.id)
        digest = hashlib.sha256(f"sealed fixture {number}".encode()).hexdigest()
        payload = MediaScriptPreviewRequest(script_source=f"# successful fixture version {number}\nprint(parameters['label'])",
            request_id="12345678-1234-4321-8123-123456789abc",
            expected_timeline_version_id=None, expected_timeline_sequence=0,
            language="python", parameters={"label":"original"},
            input_refs=[{"name":"frame.png", "source_ref":"fixture/input.png",
                         "expected_artifact_id":"fixture-input", "expected_sha256":"a"*64}],
            requested_outputs=[{"name":"result.png", "max_bytes":4096, "width":16, "height":16}],
            purpose="preview")
        sealed = SimpleNamespace(script_source=payload.script_source, language=payload.language,
            parameters_json=json.dumps(payload.parameters),
            input_revisions=(ScriptInputRevision("frame.png","fixture-revision","a"*64),),
            digest=digest, toolchain=SimpleNamespace(version="fixture-runtime",digest="a"*64))
        blob = store.publish_bytes(f"definitions/{number}.json", script_definition(sealed,payload))
        record = repository.create_artifact_record(ArtifactRecord(id=f"definition-{number}", workspace_id="local", task_id=task_id,
            kind="media_script_definition", logical_ref=blob.logical_ref, sha256=blob.sha256,
            size_bytes=blob.size_bytes, producer_job_id=job.id, input_refs=[]))
        data = io.BytesIO()
        Image.new("RGB", (4,4), "green").save(data, format="PNG")
        png = store.publish_bytes(f"outputs/{number}.png", data.getvalue())
        picture = repository.create_artifact_record(ArtifactRecord(id=f"png-{number}", workspace_id="local", task_id=task_id,
            kind="graphic_source", logical_ref=png.logical_ref, sha256=png.sha256, size_bytes=png.size_bytes,
            producer_job_id=job.id, input_refs=[]))
        now = utc_now()
        repository.create_transform_run(TransformRun(id=f"script-transform-{number}", workspace_id="local", task_id=task_id,
            job_id=job.id, transform_type="media_script", input_digest=digest, input_refs=[],
            output_refs=[record.logical_ref,picture.logical_ref], engine="fixture-python-pillow",
            parameters={"definition_ref":record.logical_ref,"script_digest":digest,"owner_member_id":owner or actor.member_id},
            status="succeeded", started_at=job.started_at, finished_at=now))
        outputs = ([record.logical_ref] if include_definition else []) + [picture.logical_ref]
        repository.finish_job_run("local",job.id,status=status,output_refs=outputs if status=="succeeded" else [],
            **({} if status=="succeeded" else {"failure_reason":"fixture","impact":"fixture","repair_action":"fixture"}))
        repository.append_task_event(TaskEvent(id=f"event_{job.id}_verified",workspace_id="local",task_id=task_id,job_id=job.id,
            event_type="fixture_verified",payload={"outputs":[{"reference":record.logical_ref},{"reference":picture.logical_ref}]}))
        return job.id,record

    return repository, store, members, actor, tools, producer


def create(case, number=1):
    repository,store,members,actor,tools,producer = case
    job,_ = producer(number)
    return tools().create_from_job(actor,job,name="fixture_tool",description="Draw a parameterized PNG",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")


def test_persistent_discovery_reuse_version_cas_and_revoke_preserve_history(case):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    tool = recipe["tool_id"]
    first_ref = recipe["definition_ref"]
    first_bytes = store.resolve_file(first_ref).read_bytes()
    restarted = MediaScriptTools(ProductionRepository(repository.database_path, repository.database_path.parent / "library"),
        ArtifactStore(store._root), verify_identity=tools().verify_identity, authorize_task_write=tools().authorize_task_write)
    assert restarted.list(actor)[0]["tool_id"] == tool
    invocation = restarted.instantiate_request(actor,tool,version=1,parameters={"label":"new"})
    assert invocation["parameters"]["label"] == "new" and invocation["parameters"]["gain"] == 1
    assert set(invocation) == {"script_source","language","parameters","input_slots","requested_outputs","purpose"}
    assert invocation["input_slots"] == [{"name":"frame.png","kind":"graphic_source_png"}]
    assert invocation["requested_outputs"] == [{"name":"result.png","max_bytes":4096,"width":16,"height":16}]
    second_job,_ = producer(2)
    changed = restarted.update_from_job(actor,tool,expected_version=1,job_id=second_job,
        name="updated",description="updated metadata",parameter_schema=SCHEMA)
    assert changed["version"] == 2
    assert restarted.get(actor,tool,version=1)["definition"]["script_source"] == recipe["definition"]["script_source"]
    with pytest.raises(ScriptToolConflict):
        restarted.update_from_job(actor,tool,expected_version=1,job_id=second_job,
            name="stale",description="stale",parameter_schema=SCHEMA)
    restarted.revoke(actor,tool,expected_version=2)
    assert restarted.list(actor) == []
    assert restarted.list(actor,include_inactive=True)[0]["active"] is False
    for version in (1,2):
        with pytest.raises(ScriptToolError,match="revoked"):
            restarted.instantiate_request(actor,tool,version=version,parameters={"label":"new"})
    assert store.resolve_file(first_ref).read_bytes() == first_bytes


def test_registration_does_not_expand_source_job_artifacts_or_rewrite_verified_event(case):
    repository,store,members,actor,tools,producer = case
    job,definition = producer()
    before = [item for item in repository.list_artifact_records("local","task") if item.producer_job_id==job]
    event = repository.get_task_event("local",f"event_{job}_verified")
    recipe = tools().create_from_job(actor,job,name="fixture",description="fixture",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")
    tools().revoke(actor,recipe["tool_id"],expected_version=1)
    after = [item for item in repository.list_artifact_records("local","task") if item.producer_job_id==job]
    assert before == after and repository.get_task_event("local",event.id) == event
    assert store.describe(definition.logical_ref).sha256 == definition.sha256
    metadata_jobs = [job for job in repository.list_job_runs("local","task") if job.kind=="media_script_tool_registration"]
    assert len(metadata_jobs)==1 and metadata_jobs[0].status=="succeeded"


@pytest.mark.parametrize("difference", [{"status":"failed"},{"status":"cancelled"},{"kind":"creation_task_execution"},{"owner":"foreign"}])
def test_registration_rejects_non_success_wrong_kind_or_foreign_creator(case,difference):
    repository,store,members,actor,tools,producer = case
    job,_ = producer(**difference)
    with pytest.raises((ScriptToolError,PermissionError)):
        tools().create_from_job(actor,job,name="fixture",description="fixture",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")


def test_recipe_private_scope_identity_and_task_write_are_actual_checks(case):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    other = CreationExecutionActor("local",members.add_member("other").member_id)
    with pytest.raises(KeyError):
        tools().get(other,recipe["tool_id"])
    assert tools().list(other) == []
    with pytest.raises(PermissionError):
        tools().get(replace(actor,workspace_id="foreign"),recipe["tool_id"])
    members.remove_member(other.member_id)
    with pytest.raises(PermissionError):
        tools().get(other,recipe["tool_id"])
    job,_ = producer(2)
    with repository._connect() as conn:
        row = conn.execute("SELECT payload FROM creation_tasks WHERE id='task'").fetchone()
        payload = json.loads(row["payload"])
        payload["owner_user_id"] = "foreign"
        conn.execute("UPDATE creation_tasks SET payload=? WHERE id='task'",(json.dumps(payload),))
    with pytest.raises(PermissionError):
        tools().create_from_job(actor,job,name="fixture",description="fixture",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")


@pytest.mark.parametrize("corruption",["changed","missing"])
def test_modified_or_missing_successful_definition_is_rejected(case,corruption):
    repository,store,members,actor,tools,producer = case
    job,record = producer()
    path = store.resolve_file(record.logical_ref)
    path.write_bytes(b'changed') if corruption=="changed" else path.unlink()
    with pytest.raises((ScriptToolError,FileNotFoundError)):
        tools().create_from_job(actor,job,name="fixture",description="fixture",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")


def test_concurrent_version_cas_has_one_winner_and_no_duplicate_version(case):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    next_job,_ = producer(2)
    def update(name):
        try:
            tools().update_from_job(actor,recipe["tool_id"],expected_version=1,job_id=next_job,
                name=name,description="concurrent update",parameter_schema=SCHEMA)
            return "saved"
        except ScriptToolConflict:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as executor:
        assert sorted(executor.map(update,["one","two"])) == ["conflict","saved"]
    assert tools().get(actor,recipe["tool_id"])["version"] == 2
    registrations = [job for job in repository.list_job_runs("local","task") if job.kind=="media_script_tool_registration"]
    assert len(registrations) == 2


def test_old_input_availability_is_not_inherited_as_a_recipe_permission(case):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    with repository._connect() as conn:
        # A historical successful input can now be unavailable. The definition
        # remains proven; no live old input is read, copied or granted to reuse.
        conn.execute("UPDATE transform_runs SET input_refs_json=? WHERE id='script-transform-1'",
                     (json.dumps(["history/input-unavailable.png"]),))
    result = tools().instantiate_request(actor,recipe["tool_id"],version=1,parameters={"label":"fresh"})
    assert "input_refs" not in result and "history" not in json.dumps(result)


@pytest.mark.parametrize("corruption",["same_size_hash","missing"])
def test_reuse_rejects_definition_change_after_registration(case,corruption):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    path = store.resolve_file(recipe["definition_ref"])
    if corruption=="same_size_hash":
        content = path.read_bytes()
        path.write_bytes(content.replace(b"original",b"originaX"))
        assert path.stat().st_size == len(content)
    else:
        path.unlink()
    with pytest.raises((ScriptToolError,FileNotFoundError)):
        tools().instantiate_request(actor,recipe["tool_id"],version=1,parameters={"label":"fresh"})


def test_metadata_registration_failure_rolls_back_all_repository_rows(case):
    repository,store,members,actor,tools,producer = case
    job,_ = producer()
    before = (repository.list_job_runs("local","task"),repository.list_artifact_records("local","task"),
              repository.list_transform_runs("local","task"))
    with repository._connect() as conn:
        conn.execute("""CREATE TRIGGER reject_fixture_recipe BEFORE INSERT ON transform_runs
            WHEN NEW.transform_type='media_script_tool_recipe'
            BEGIN SELECT RAISE(ABORT,'synthetic metadata write failure'); END""")
    with pytest.raises(ValueError):
        tools().create_from_job(actor,job,name="fixture",description="fixture",parameter_schema=SCHEMA,request_id="12345678-1234-4321-8123-123456789abc")
    after = (repository.list_job_runs("local","task"),repository.list_artifact_records("local","task"),
             repository.list_transform_runs("local","task"))
    assert after == before
    assert tools().list(actor) == []


def test_creation_nonce_replay_query_and_payload_conflict_use_existing_job_key(case):
    repository,store,members,actor,tools,producer = case
    job,_ = producer()
    request = "12345678-1234-4321-8123-123456789abc"
    kwargs = dict(request_id=request,name="fixture",description="fixture",parameter_schema=SCHEMA)
    saved = tools().create_from_job(actor,job,**kwargs)
    before = repository.list_job_runs("local","task")
    replay = tools().create_from_job(actor,job,**kwargs)
    assert replay == saved
    assert repository.list_job_runs("local","task") == before
    restarted = tools()
    assert restarted.get_created_for_request(actor,request)["tool_id"] == saved["tool_id"]
    with pytest.raises(ScriptToolConflict):
        tools().create_from_job(actor,job,**(kwargs | {"name":"different"}))
    schema = copy.deepcopy(SCHEMA)
    schema["properties"]["gain"]["default"] = 2
    with pytest.raises(ScriptToolConflict):
        tools().create_from_job(actor,job,**(kwargs | {"parameter_schema":schema}))
    second_job,_ = producer(2)
    tools().update_from_job(actor,saved["tool_id"],expected_version=1,job_id=second_job,
        name="next",description="next",parameter_schema=SCHEMA)
    assert tools().get(actor,saved["tool_id"])["version"] == 2
    assert tools().get_created_for_request(actor,request)["version"] == 1
    assert tools().create_from_job(actor,job,**kwargs)["version"] == 1


def test_same_uuid_is_private_to_current_member_and_workspace(case):
    repository,store,members,actor,tools,producer = case
    request = "12345678-1234-4321-8123-123456789abc"
    saved = create(case)
    other = CreationExecutionActor("local",members.add_member("other").member_id)
    with pytest.raises(KeyError):
        tools().get_created_for_request(other,request)
    with pytest.raises(PermissionError):
        tools().get_created_for_request(replace(actor,workspace_id="foreign"),request)
    plan = replace(repository.get_creation_plan("local","plan"),id="other-plan")
    task = CreationTask(id="other-task",workspace_id="local",plan_id=plan.id,name="other",scenario="knowledge_explanation")
    repository.confirm_creation_plan(plan,task,verified_owner_user_id=other.member_id)
    other_job,_ = producer(2,owner=other.member_id,task_id=task.id)
    created = tools().create_from_job(other,other_job,request_id=request,name="other",description="other",parameter_schema=SCHEMA)
    assert created["tool_id"] != saved["tool_id"]
    assert tools().get_created_for_request(other,request)["tool_id"] == created["tool_id"]


def test_incomplete_registration_nonce_is_never_recreated(case):
    repository,store,members,actor,tools,producer = case
    request = "12345678-1234-4321-8123-123456789abc"
    job,_ = producer()
    kwargs = dict(request_id=request,name="fixture",description="fixture",parameter_schema=SCHEMA)
    tools().create_from_job(actor,job,**kwargs)
    with repository._connect() as conn:
        conn.execute("UPDATE job_runs SET status='interrupted' WHERE kind='media_script_tool_registration'")
    before = repository.list_job_runs("local","task")
    with pytest.raises(ScriptToolUnknown):
        tools().create_from_job(actor,job,**kwargs)
    with pytest.raises(ScriptToolUnknown):
        tools().get_created_for_request(actor,request)
    assert repository.list_job_runs("local","task") == before


@pytest.mark.parametrize("field",["input_digest","output_refs_json"])
def test_recipe_result_must_close_over_its_actual_registration_digest_and_outputs(case,field):
    repository,store,members,actor,tools,producer = case
    recipe = create(case)
    with repository._connect() as conn:
        if field=="input_digest":
            conn.execute("UPDATE job_runs SET input_digest=? WHERE kind='media_script_tool_registration'",("b"*64,))
        else:
            conn.execute("UPDATE job_runs SET output_refs_json='[]' WHERE kind='media_script_tool_registration'")
    with pytest.raises(ScriptToolError,match="metadata registration"):
        tools().get(actor,recipe["tool_id"])


def test_definition_must_be_an_actual_output_of_the_successful_producer_job(case):
    repository,store,members,actor,tools,producer = case
    job,definition = producer(include_definition=False)
    source = repository.get_job_run("local",job)
    assert source.status == "succeeded" and definition.logical_ref not in source.output_refs
    assert definition.logical_ref in repository.get_transform_run("local","script-transform-1").output_refs
    with pytest.raises(ScriptToolError,match="missing or unbound"):
        tools().create_from_job(actor,job,request_id="12345678-1234-4321-8123-123456789abc",
            name="fixture",description="fixture",parameter_schema=SCHEMA)
