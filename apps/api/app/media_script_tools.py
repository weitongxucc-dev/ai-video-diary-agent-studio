"""Private script recipes; metadata only, never code execution or permission grants."""

from __future__ import annotations

import json
import math
import hashlib
from uuid import UUID, uuid4

from app.artifact_store import ArtifactStore
from app.creation_execution import CreationExecutionActor
from app.media_script import PngOutputContract, _name
from app.models import utc_now
from app.creation.contracts import ArtifactRecord, JobRun, TransformRun
from app.production import ProductionRepository
from app.task_audio import TASK_MEDIA_KIND


class ScriptToolError(ValueError):
    pass


class ScriptToolConflict(ScriptToolError):
    pass


class ScriptToolUnknown(ScriptToolError):
    pass


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _finite(value: object) -> bool:
    return type(value) is int or type(value) is float and math.isfinite(value)


def _scalar(rule: dict, value: object) -> None:
    kind = rule["type"]
    valid = (type(value) is int if kind == "integer" else
             _finite(value) if kind == "number" else
             type(value) is str if kind == "string" else type(value) is bool)
    if not valid:
        raise ScriptToolError("parameter type does not match the scalar schema")
    if kind in {"integer", "number"}:
        if "minimum" in rule and value < rule["minimum"] or "maximum" in rule and value > rule["maximum"]:
            raise ScriptToolError("parameter is outside its numeric bounds")
    if kind == "string" and len(value) > rule["maxLength"]:
        raise ScriptToolError("parameter exceeds maxLength")
    if "enum" in rule and not any(value == item for item in rule["enum"]):
        raise ScriptToolError("parameter is not a declared enum value")


def validate_parameter_schema(schema: dict) -> dict:
    """Implemented subset: closed object, at most 16 bounded scalar properties.

    No references, nested collections, nullable unions, patterns or readOnly
    declarations. Returns a detached canonical value suitable for persistence.
    """
    if (type(schema) is not dict or set(schema) != {"type", "properties", "required", "additionalProperties"}
            or schema["type"] != "object" or schema["additionalProperties"] is not False
            or type(schema["properties"]) is not dict or len(schema["properties"]) > 16
            or type(schema["required"]) is not list):
        raise ScriptToolError("schema must be a bounded closed object with explicit required")
    properties = schema["properties"]
    required = schema["required"]
    if (any(type(name) is not str or not name or len(name) > 64 for name in required)
            or len(set(required)) != len(required) or not set(required) <= set(properties)):
        raise ScriptToolError("required must contain unique declared property names")
    for name, rule in properties.items():
        if type(name) is not str or not name or len(name) > 64 or type(rule) is not dict:
            raise ScriptToolError("invalid scalar property")
        kind = rule.get("type")
        if type(kind) is not str or kind not in {"integer", "number", "string", "boolean"}:
            raise ScriptToolError("unsupported parameter type")
        allowed = {"type", "enum", "default", "description"} | (
            {"minimum", "maximum"} if kind in {"integer", "number"} else {"maxLength"} if kind == "string" else set())
        if set(rule) - allowed:
            raise ScriptToolError("unsupported scalar schema key")
        if "description" in rule and (type(rule["description"]) is not str or len(rule["description"]) > 300):
            raise ScriptToolError("scalar description exceeds bounds")
        if kind == "string" and (type(rule.get("maxLength")) is not int or not 0 <= rule["maxLength"] <= 4096):
            raise ScriptToolError("string requires maxLength between 0 and 4096")
        if kind in {"integer", "number"}:
            for bound in ("minimum", "maximum"):
                if bound in rule and not _finite(rule[bound]):
                    raise ScriptToolError("numeric bounds must be finite")
            if ("minimum" in rule) != ("maximum" in rule):
                raise ScriptToolError("numeric bounds must be paired")
            if "minimum" not in rule and "enum" not in rule:
                raise ScriptToolError("numeric parameter requires finite bounds or enum")
            if "minimum" in rule and rule["minimum"] > rule["maximum"]:
                raise ScriptToolError("numeric bounds are reversed")
        if "enum" in rule:
            values = rule["enum"]
            if type(values) is not list or not 1 <= len(values) <= 32:
                raise ScriptToolError("enum requires 1 to 32 scalar values")
            without_enum = {key: value for key, value in rule.items() if key != "enum"}
            for value in values:
                _scalar(without_enum, value)
            if any(value in values[:index] for index, value in enumerate(values)):
                raise ScriptToolError("enum values must be unique")
        if "default" in rule:
            _scalar(rule, rule["default"])
    encoded = _json(schema)
    if len(encoded.encode()) > 65536:
        raise ScriptToolError("parameter schema exceeds metadata bounds")
    return json.loads(encoded)


def validate_tool_parameters(schema: dict, parameters: dict) -> dict:
    """One validator for registration examples, defaults and every invocation."""
    schema = validate_parameter_schema(schema)
    if type(parameters) is not dict or set(parameters) - set(schema["properties"]):
        raise ScriptToolError("parameters must be an object without undeclared keys")
    result = dict(parameters)
    for name, rule in schema["properties"].items():
        if name not in result and "default" in rule:
            result[name] = rule["default"]
        if name in result:
            _scalar(rule, result[name])
    if not set(schema["required"]) <= set(result):
        raise ScriptToolError("required parameter is missing")
    return json.loads(_json(result))


def _digest(value: object) -> bool:
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


class MediaScriptTools:
    """Trusted API supplies existing identity and task-write verifiers.

    Immutable version blobs/TransformRuns are the metadata. The largest version
    is the sole current head; only its active bit is mutable. No execution here.
    """

    def __init__(self, repository: ProductionRepository, store: ArtifactStore, *,
                 verify_identity, authorize_task_write, task_authorizations=None, task_id=None):
        self.repository = repository
        self.store = store
        self.verify_identity = verify_identity
        self.authorize_task_write = authorize_task_write
        self.task_authorizations, self.task_id = task_authorizations, task_id

    def _identity(self, actor):
        if not isinstance(actor, CreationExecutionActor):
            raise PermissionError("current recipe identity is unavailable or unauthorized")
        verdict = (self.task_authorizations.verdict(actor,self.task_id,"task_media",check_scope=False)
                   if self.task_authorizations is not None and self.task_id is not None else self.verify_identity(actor))
        if verdict != "ready":
            raise PermissionError("current recipe identity is unavailable or unauthorized")
        return actor.user_id or actor.member_id

    def registration_identity(self, actor, request_id):
        if self.task_id is not None:
            raise PermissionError("任务授权不能创建通用配方")
        owner = self._identity(actor)
        try:
            request = str(UUID(str(request_id)))
        except ValueError as exc:
            raise ScriptToolError("creation requires a stable UUID request_id") from exc
        identity = hashlib.sha256(_json([actor.workspace_id,owner,request]).encode()).hexdigest()
        return "script_tool_"+identity, "job_tool_"+identity, "script-tool-create:"+identity

    def _blob(self, record):
        limit = 256 * 1024 if record.kind == "media_script_definition" else 65536
        if not 0 < record.size_bytes <= limit:
            raise ScriptToolError("definition/recipe artifact exceeds metadata bounds")
        path = self.store.resolve_file(record.logical_ref)
        if path.stat().st_size != record.size_bytes:
            raise ScriptToolError("artifact size differs from its registered record")
        actual = self.store.describe(record.logical_ref)
        if (actual.sha256, actual.size_bytes) != (record.sha256, record.size_bytes):
            raise ScriptToolError("artifact digest differs from its registered record")
        with path.open("rb") as source:
            content = source.read(limit + 1)
        if len(content) != record.size_bytes or hashlib.sha256(content).hexdigest() != record.sha256:
            raise ScriptToolError("artifact changed during metadata read")
        try:
            value = json.loads(content)
        except (ValueError, UnicodeError) as exc:
            raise ScriptToolError("artifact is not a complete JSON definition") from exc
        if type(value) is not dict:
            raise ScriptToolError("artifact definition must be an object")
        return value

    def _definition(self, actor, job_id):
        owner = self._identity(actor)
        job = self.repository.get_job_run(actor.workspace_id, job_id)
        task = self.repository.get_creation_task(actor.workspace_id, job.task_id)
        if self.authorize_task_write(actor, task) is False:
            raise PermissionError("current creator cannot write the producer task")
        if job.kind != TASK_MEDIA_KIND or job.status != "succeeded":
            raise ScriptToolError("source must be a successful canonical script job")
        transforms = [run for run in self.repository.lineage_records.list_transform_runs(actor.workspace_id, task.id)
                      if run.job_id == job.id and run.transform_type == "media_script" and run.status == "succeeded"]
        if len(transforms) != 1:
            raise ScriptToolError("successful job has no unique script definition transform")
        run = transforms[0]
        if run.parameters.get("owner_member_id") != owner:
            raise PermissionError("script producer does not belong to the current creator")
        records = [record for record in self.repository.list_artifact_records(actor.workspace_id, task.id)
                   if record.kind == "media_script_definition" and record.producer_job_id == job.id
                   and record.logical_ref == run.parameters.get("definition_ref")]
        if (len(records) != 1 or records[0].logical_ref not in run.output_refs
                or records[0].logical_ref not in job.output_refs):
            raise ScriptToolError("successful job definition artifact is missing or unbound")
        record = records[0]
        definition = self._blob(record)
        fields = {"script_source", "language", "parameters", "input_slots", "requested_outputs",
                  "purpose", "script_digest", "toolchain"}
        if set(definition) != fields or definition["language"] != "python" or definition["purpose"] != "preview":
            raise ScriptToolError("unsupported successful script definition")
        if type(definition["script_source"]) is not str or not definition["script_source"].strip():
            raise ScriptToolError("successful definition has no source")
        digest = definition["script_digest"]
        if not _digest(digest) or digest != run.parameters.get("script_digest") or digest != run.input_digest:
            raise ScriptToolError("definition is not bound to the successful script digest")
        toolchain = definition["toolchain"]
        if (type(toolchain) is not dict or set(toolchain) != {"version", "digest"}
                or type(toolchain["version"]) is not str or not toolchain["version"] or not _digest(toolchain["digest"])):
            raise ScriptToolError("definition toolchain must contain version and digest only")
        slots = definition["input_slots"]
        if type(slots) is not list or len(slots) > 16:
            raise ScriptToolError("definition input slots are invalid")
        names = []
        for slot in slots:
            if type(slot) is not dict or set(slot) != {"name", "kind"} or slot["kind"] != "graphic_source_png":
                raise ScriptToolError("definition may only name current-input PNG slots")
            _name(slot["name"])
            names.append(slot["name"])
        if len(set(names)) != len(names):
            raise ScriptToolError("duplicate definition input slot")
        outputs = definition["requested_outputs"]
        if type(outputs) is not list or not 1 <= len(outputs) <= 16:
            raise ScriptToolError("definition output contract is invalid")
        names = []
        for output in outputs:
            if type(output) is not dict or set(output) != {"name", "max_bytes", "width", "height"}:
                raise ScriptToolError("definition has unsupported output fields")
            item = PngOutputContract(name=output["name"], max_bytes=output["max_bytes"],
                                     max_width=output["width"], max_height=output["height"])
            _name(item.name)
            if not item.name.endswith(".png") or any(type(v) is not int or v <= 0 for v in (item.max_bytes,item.max_width,item.max_height)):
                raise ScriptToolError("definition requires bounded PNG outputs")
            names.append(item.name)
        if len(set(names)) != len(names):
            raise ScriptToolError("duplicate definition output name")
        return job, record, definition

    def _runs(self, conn, actor, tool_id=None):
        owner = self._identity(actor)
        if self.task_id is not None and tool_id is None:
            raise PermissionError("任务授权不能浏览通用配方库")
        runs = self.repository.lineage_records.list_recipe_runs(
            conn, actor.workspace_id, owner, tool_id,
        )
        return [run for run in runs if tool_id is None or run.parameters["tool_id"] == tool_id]

    def _head(self, conn, actor, tool_id):
        runs = self._runs(conn, actor, tool_id)
        if not runs:
            raise KeyError(tool_id)
        return max(runs, key=lambda run: run.parameters["version"])

    def _value(self, actor, run, head):
        metadata = run.parameters
        if self.task_authorizations is not None and self.task_id is not None:
            if self.task_authorizations.verdict(actor,self.task_id,"task_media",
                    references=[metadata["definition_ref"]]) != "ready":
                raise PermissionError("配方来源不在原任务授权范围内")
        record = self.repository.get_artifact_record(actor.workspace_id, metadata["recipe_artifact_id"])
        job = self.repository.get_job_run(actor.workspace_id, run.job_id)
        if (job.kind != "media_script_tool_registration" or job.status != "succeeded" or run.status != "succeeded"
                or record.kind != "media_script_tool_recipe" or record.producer_job_id != job.id
                or record.task_id != run.task_id or record.logical_ref not in run.output_refs
                or record.logical_ref not in job.output_refs or run.input_digest != job.input_digest
                or record.sha256 != job.input_digest):
            raise ScriptToolError("recipe has no successful metadata registration")
        value = self._blob(record)
        if (value["tool_id"],value["version"],value["owner_member_id"]) != (
                metadata["tool_id"],metadata["version"],self._identity(actor)):
            raise ScriptToolError("recipe metadata does not match its immutable version")
        source = self.repository.get_artifact_record(actor.workspace_id, value["definition_artifact_id"])
        if (source.kind != "media_script_definition" or source.task_id != run.task_id
                or source.logical_ref != value["definition_ref"] or source.producer_job_id != value["source_job_id"]):
            raise ScriptToolError("recipe definition provenance changed")
        definition = self._blob(source)
        if (definition["script_digest"] != value["script_digest"]
                or hashlib.sha256(definition["script_source"].encode()).hexdigest() != value["code_sha256"]):
            raise ScriptToolError("recipe source digest changed")
        return {**value, "active":head.parameters["active"], "definition":definition}

    def _save(self, conn, actor, tool_id, version, source_job_id, name, description, parameter_schema,
              *, registration_id=None, idempotency_key=None):
        if type(name) is not str or not 1 <= len(name) <= 80 or type(description) is not str or not 1 <= len(description) <= 500:
            raise ScriptToolError("recipe name/description are required and bounded")
        job, record, definition = self._definition(actor, source_job_id)
        schema = validate_parameter_schema(parameter_schema)
        validate_tool_parameters(schema, definition["parameters"])
        owner = self._identity(actor)
        registration_id = registration_id or "job_tool_" + uuid4().hex
        idempotency_key = idempotency_key or registration_id
        value = {"tool_id":tool_id, "version":version, "owner_member_id":owner,
                 "name":name, "description":description, "parameter_schema":schema,
                 "definition_artifact_id":record.id, "definition_ref":record.logical_ref,
                 "source_job_id":job.id, "code_sha256":hashlib.sha256(definition["script_source"].encode()).hexdigest(),
                 "script_digest":definition["script_digest"], "input_slots":definition["input_slots"],
                 "requested_outputs":definition["requested_outputs"], "toolchain":definition["toolchain"]}
        content = _json(value).encode()
        if len(content) > 65536:
            raise ScriptToolError("recipe exceeds bounded metadata size")
        input_digest = hashlib.sha256(content).hexdigest()
        previous = self.repository.get_job_run_by_idempotency_key(actor.workspace_id,idempotency_key)
        if previous is not None:
            if (previous.id != registration_id or previous.kind != "media_script_tool_registration"
                    or previous.input_digest != input_digest):
                raise ScriptToolConflict("creation request is bound to different recipe content")
            if previous.status != "succeeded":
                raise ScriptToolUnknown("original registration result is unknown; query without recreating")
            return tool_id
        registration = self.repository.create_job_run(JobRun(id=registration_id, workspace_id=actor.workspace_id,
            task_id=job.task_id, kind="media_script_tool_registration", idempotency_key=idempotency_key,
            input_digest=input_digest), connection=conn)
        registration = self.repository.claim_job_run(actor.workspace_id, registration.id, connection=conn)
        published = self.store.publish_bytes(f"script-tools/{tool_id}/{version}-{registration.id}.json", content)
        if self.store.describe(published.logical_ref) != published:
            raise ScriptToolError("recipe publication cannot be verified")
        artifact = self.repository.create_artifact_record(ArtifactRecord(id="artifact_"+registration.id,
            workspace_id=actor.workspace_id, task_id=job.task_id, kind="media_script_tool_recipe",
            logical_ref=published.logical_ref, sha256=published.sha256, size_bytes=published.size_bytes,
            producer_job_id=registration.id, input_refs=[record.logical_ref]), connection=conn)
        now = utc_now()
        self.repository.lineage_records.create_transform_run(TransformRun(id="transform_"+registration.id,
            workspace_id=actor.workspace_id, task_id=job.task_id, job_id=registration.id,
            transform_type="media_script_tool_recipe", input_digest=registration.input_digest,
            input_refs=[record.logical_ref], output_refs=[artifact.logical_ref], engine="trusted-recipe-metadata",
            parameters={**value,"active":True,"recipe_artifact_id":artifact.id}, status="succeeded",
            started_at=registration.started_at, finished_at=now), connection=conn)
        self.repository.finish_job_run(actor.workspace_id, registration.id, status="succeeded",
            output_refs=[artifact.logical_ref], connection=conn)
        return tool_id

    def create_from_job(self, actor, job_id, *, request_id, name, description, parameter_schema):
        tool_id, registration_id, key = self.registration_identity(actor,request_id)
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._save(conn, actor, tool_id, 1, job_id, name, description, parameter_schema,
                       registration_id=registration_id,idempotency_key=key)
        return self.get(actor, tool_id,version=1)

    def get_created_for_request(self, actor, request_id):
        tool_id, registration_id, key = self.registration_identity(actor,request_id)
        job = self.repository.get_job_run_by_idempotency_key(actor.workspace_id,key)
        if job is None:
            raise KeyError(str(request_id))
        if job.id != registration_id or job.kind != "media_script_tool_registration" or job.status != "succeeded":
            raise ScriptToolUnknown("original registration has no verified completed result")
        return self.get(actor,tool_id,version=1)

    def get(self, actor, tool_id, *, version=None):
        with self.repository._connect() as conn:
            head = self._head(conn, actor, tool_id)
            run = head if version is None else next((item for item in self._runs(conn,actor,tool_id)
                                                    if item.parameters["version"] == version), None)
            if run is None:
                raise KeyError((tool_id,version))
        return self._value(actor, run, head)

    def list(self, actor, *, include_inactive=False, limit=50):
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ScriptToolError("recipe discovery limit must be 1 to 100")
        with self.repository._connect() as conn:
            heads = {}
            for run in self._runs(conn, actor):
                tool_id = run.parameters["tool_id"]
                if tool_id not in heads or heads[tool_id].parameters["version"] < run.parameters["version"]:
                    heads[tool_id] = run
        selected = [run for tool_id,run in sorted(heads.items()) if include_inactive or run.parameters["active"]][:limit]
        return [{key:value for key,value in self._value(actor,run,run).items() if key != "definition"} for run in selected]

    def update_from_job(self, actor, tool_id, *, expected_version, job_id, name, description, parameter_schema):
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            head = self._head(conn, actor, tool_id)
            if type(expected_version) is not int or head.parameters["version"] != expected_version:
                raise ScriptToolConflict("recipe current version changed")
            if not head.parameters["active"]:
                raise ScriptToolError("revoked recipe cannot be updated")
            self._save(conn,actor,tool_id,expected_version+1,job_id,name,description,parameter_schema)
        return self.get(actor,tool_id)

    def revoke(self, actor, tool_id, *, expected_version):
        with self.repository._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            head = self._head(conn,actor,tool_id)
            if type(expected_version) is not int or head.parameters["version"] != expected_version:
                raise ScriptToolConflict("recipe current version changed")
            self.repository.lineage_records.deactivate_recipe_run(conn, actor.workspace_id, head)
        return self.get(actor,tool_id)

    def instantiate_request(self, actor, tool_id, *, version, parameters):
        if type(version) is not int or version < 1:
            raise ScriptToolError("invocation requires an explicit positive recipe version")
        value = self.get(actor,tool_id,version=version)
        if not value["active"]:
            raise ScriptToolError("recipe is revoked for all future calls")
        definition = value["definition"]
        return {"script_source":definition["script_source"], "language":definition["language"],
                "parameters":validate_tool_parameters(value["parameter_schema"],parameters),
                "input_slots":definition["input_slots"], "requested_outputs":definition["requested_outputs"],
                "purpose":definition["purpose"]}
