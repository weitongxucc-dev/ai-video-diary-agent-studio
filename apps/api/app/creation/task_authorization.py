"""Task-scoped authority; network observation never runs in a process watchdog."""
from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
import logging
import threading
from uuid import uuid4

from app.creation.contracts import TaskAuthorization, TaskAuthorizationConflict
from app.materials.contracts import MediaAuthorityError


logger = logging.getLogger(__name__)
TASK_AUTHORIZATION_LIFETIME = timedelta(hours=24)
AUTHORIZATION_OBSERVATION_INTERVAL = timedelta(minutes=1)
TASK_OPERATIONS = ("narration", "person_render", "montage_plan", "render", "narration_edit", "person_edit", "cover", "task_media", "quality_check")


class TaskAuthorizationDenied(PermissionError):
    """This task's original identity or concrete source scope cannot be used."""


class TaskAuthorizationUseCase:
    def __init__(self, repository, verify_identity, *, clock=lambda: datetime.now(timezone.utc)):
        self.repository, self.verify_identity, self.clock = repository, verify_identity, clock
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self.last_error = None

    @staticmethod
    def identity(actor):
        return {"workspace_id": actor.workspace_id, **actor.identity_payload()}

    def _requirements(self, workspace_id, task_id, *, connection=None):
        task = self.repository.get_creation_task(workspace_id, task_id, connection=connection)
        with (self.repository._connect() if connection is None else nullcontext(connection)) as conn:
            row = conn.execute("SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?", (workspace_id,task.plan_id)).fetchone()
            plan = json.loads(row["payload"])
            montage = conn.execute("SELECT payload FROM creation_task_montage_plans WHERE workspace_id=? AND task_id=?", (workspace_id,task_id)).fetchone()
            montage = json.loads(montage["payload"]) if montage is not None else {}
        result = {"assets": {item["asset_ref"].removeprefix("asset:") for item in plan.get("material_matches", [])},
                  "voices": set(), "persons": set(), "references": set()}
        for key, kind in (("selected_voice", "voices"), ("selected_person", "persons")):
            ref = plan.get(key)
            if ref:
                result[kind].add(f"{ref['profile_id']}:{ref['version']}")
        result["assets"].update(clip["asset_id"] for clip in montage.get("clips", [])
                                if clip.get("asset_id") and not clip.get("person_render_id"))
        def references(value):
            if isinstance(value, dict):
                for child in value.values(): references(child)
            elif isinstance(value, list):
                for child in value: references(child)
            elif isinstance(value, str) and value.startswith(("workspace://", "artifact://", "narration://", "person-render://", "artifacts/")):
                result["references"].add(value)
        references(montage)
        return task, {key: sorted(values) for key, values in result.items()}

    def _scope(self, identity, task_id):
        workspace_id = identity["workspace_id"]
        task, required = self._requirements(workspace_id, task_id)
        reader = identity.get("user_id") or identity["member_id"]
        if task.owner_user_id is not None and task.owner_user_id != reader:
            raise TaskAuthorizationDenied("任务授权只能绑定原任务所有者")
        if task.owner_user_id is None and (identity.get("device_id") or workspace_id != "local"):
            raise TaskAuthorizationDenied("原任务缺少可信所有者，不能签发任务授权")
        assets = self.repository.list_assets(workspace_id, viewer_user_id=reader, local_diagnostic=False)
        allowed_assets = {asset.id for asset in assets}
        if not set(required["assets"]) <= allowed_assets:
            raise TaskAuthorizationDenied("目标素材不在原成员有权使用的范围内")
        scope = self.repository.task_authorization_material_scope(workspace_id, task_id)
        # A concrete approved selection is a boundary, not a suggestion to expand.
        if required["assets"]:
            allowed_assets = set(required["assets"])
        elif scope is not None and scope.get("kind") != "workspace_library":
            raise TaskAuthorizationDenied("原目标的限定素材尚未确认，不能授权整个素材库")
        required["assets"] = sorted(allowed_assets)
        try:
            required["references"] = sorted({self.repository.workspace_media_reference_for_asset(workspace_id, asset_id)
                                              for asset_id in allowed_assets})
        except (KeyError, MediaAuthorityError) as exc:
            raise TaskAuthorizationDenied(f"原任务素材引用不可用：{exc}") from exc
        plan = self.repository.get_creation_plan(workspace_id, task.plan_id)
        if plan.selected_voice is not None:
            try:
                self.repository.creation_plan_voice_reference(workspace_id, plan.selected_voice.profile_id,
                    plan.selected_voice.version, viewer_user_id=reader, local_diagnostic=False)
            except json.JSONDecodeError:
                raise  # Corrupt persisted data is not a resource permission refusal.
            except (KeyError, ValueError, PermissionError) as exc:
                raise TaskAuthorizationDenied(f"原任务声音参考不可用：{exc}") from exc
        if plan.selected_person is not None:
            try:
                self.repository.creation_plan_person_reference(workspace_id, plan.selected_person.profile_id,
                    plan.selected_person.version, viewer_user_id=reader, local_diagnostic=False)
            except json.JSONDecodeError:
                raise
            except (KeyError, ValueError, PermissionError) as exc:
                raise TaskAuthorizationDenied(f"原任务人物参考不可用：{exc}") from exc
        return required

    def _derived_reference(self, conn, authorization, task, reference, seen):
        """Only persisted outputs of this original task inherit its authority."""
        if not isinstance(reference, str):
            return False
        if reference.startswith("asset:"):
            return reference.removeprefix("asset:") in authorization.resource_scope.get("assets", [])
        if reference in authorization.resource_scope.get("references", []):
            return True
        if reference in seen:
            return False
        seen.add(reference)
        ws, task_id = authorization.workspace_id, authorization.task_id
        if reference.startswith("narration://artifact/"):
            row = conn.execute("SELECT payload FROM narration_artifacts WHERE workspace_id=? AND cache_key=?",
                               (ws, reference.removeprefix("narration://artifact/"))).fetchone()
            if row is not None:
                value = json.loads(row[0])
                return (value.get("task_id") == task_id and value.get("script_id") == task.confirmed_script_id
                    and (value.get("source_kind") == "recorded" and self._derived_reference(conn, authorization, task,
                         value.get("source_artifact_ref"), seen)
                         or value.get("voice_profile_id") in {item.split(":", 1)[0] for item in authorization.resource_scope.get("voices", [])}))
        if reference.startswith("person-render://"):
            row = conn.execute("SELECT p.person_profile_id,p.person_version,p.voice_profile_id,p.voice_version "
                "FROM person_renders p JOIN narration_segments n ON n.id=p.narration_segment_id AND n.workspace_id=p.workspace_id "
                "WHERE p.workspace_id=? AND p.task_id=? AND p.id=? AND n.task_id=? AND n.script_id=?",
                (ws,task_id,reference.removeprefix("person-render://"),task_id,task.confirmed_script_id)).fetchone()
            return row is not None and f"{row[0]}:{row[1]}" in authorization.resource_scope.get("persons", []) and f"{row[2]}:{row[3]}" in authorization.resource_scope.get("voices", [])
        segment = conn.execute("SELECT voice_profile_id,voice_version,source_kind,source_artifact_ref "
            "FROM narration_segments WHERE workspace_id=? AND task_id=? AND script_id=? AND audio_reference=?",
            (ws,task_id,task.confirmed_script_id,reference)).fetchone()
        if segment is not None:
            return (segment[2] == "generated" and f"{segment[0]}:{segment[1]}" in authorization.resource_scope.get("voices", [])
                or segment[2] == "recorded" and self._derived_reference(conn, authorization, task, segment[3], seen))
        row = conn.execute("SELECT a.input_refs_json FROM artifact_records a JOIN job_runs j "
            "ON j.workspace_id=a.workspace_id AND j.id=a.producer_job_id "
            "WHERE a.workspace_id=? AND a.task_id=? AND j.task_id=? AND a.logical_ref=?",
            (ws,task_id,task_id,reference)).fetchone()
        return row is not None and all(self._derived_reference(conn, authorization, task, ref, seen.copy())
                                      for ref in json.loads(row[0]))

    def ensure(self, task, actor, *, interactive=False):
        identity = self.identity(actor)
        current = self.repository.get_task_authorization(task.workspace_id, task.id)
        goal = self.repository.task_authorization_goal_revision(task.workspace_id, task.id)
        if current is not None:
            if current.identity != identity:
                raise TaskAuthorizationDenied("任务授权不能切换原设备或账号")
            if current.revoked_at is not None:
                raise TaskAuthorizationDenied("原任务授权已明确撤销")
            if current.goal_revision == goal and self.status(task.workspace_id, task.id, identity=identity) == "valid":
                return current
            if not interactive:
                self._wake.set()
                return current
        verdict = self.verify_identity(identity)  # Before any writer transaction.
        now = self.clock()
        scope = current.resource_scope if current else {}
        refusal = None
        if verdict == "ready":
            try:
                scope = self._scope(identity, task.id)
            except TaskAuthorizationDenied as exc:
                refusal = exc
                verdict = f"scope_refused: {exc}"
        authorization = TaskAuthorization(current.id if current else f"task_authorization_{uuid4().hex}",
            task.workspace_id, task.id, identity, goal if verdict == "ready" else (current.goal_revision if current else goal),
            TASK_OPERATIONS, scope,
            current.issued_at if current and current.issued_at else now if verdict == "ready" else None,
            now if verdict == "ready" else current.verified_at if current else None,
            now + TASK_AUTHORIZATION_LIFETIME if verdict == "ready" else current.expires_at if current else None,
            (current.revision + 1) if current else 1,
            now if verdict == "unauthorized" else None, now, str(verdict))
        try:
            result = self.repository.save_task_authorization(authorization, expected_revision=current.revision if current else None)
        except TaskAuthorizationConflict:
            result = self.repository.get_task_authorization(task.workspace_id, task.id)
        self._wake.set()
        if refusal is not None:
            raise refusal
        return result

    def status(self, workspace_id, task_id, *, identity=None, operation=None, connection=None, check_scope=True):
        authorization = self.repository.get_task_authorization(workspace_id, task_id, connection=connection, metadata_only=not check_scope)
        if authorization is None:
            return "unverified"
        if identity is not None and authorization.identity != identity:
            return "outside_scope"
        if authorization.revoked_at is not None:
            return "revoked"
        if authorization.expires_at is None:
            return "unverified"
        if self.clock() >= authorization.expires_at:
            return "expired"
        if self.repository.task_authorization_goal_revision(workspace_id, task_id, connection=connection) != authorization.goal_revision:
            return "outside_scope"
        if operation is not None and operation not in authorization.allowed_operations:
            return "outside_scope"
        task = self.repository.get_creation_task(workspace_id, task_id, connection=connection)
        reader = authorization.identity.get("user_id") or authorization.identity["member_id"]
        if (task.owner_user_id is not None and task.owner_user_id != reader
                or task.owner_user_id is None and (workspace_id != "local" or authorization.identity.get("device_id"))):
            return "outside_scope"
        if not check_scope:
            return "valid"
        task, required = self._requirements(workspace_id, task_id, connection=connection)
        if any(not set(values) <= set(authorization.resource_scope.get(key, []))
               for key, values in required.items() if key != "references"):
            return "outside_scope"
        with (self.repository._connect() if connection is None else nullcontext(connection)) as conn:
            if any(not self._derived_reference(conn, authorization, task, ref, set()) for ref in required["references"]):
                return "outside_scope"
        return "valid"

    def material_asset_ids(self, workspace_id, task_id):
        if self.status(workspace_id, task_id, operation="montage_plan") != "valid":
            raise PermissionError("原任务的选材授权未就绪")
        return frozenset(self.repository.get_task_authorization(workspace_id, task_id).resource_scope["assets"])

    def verdict(self, actor, task_id, operation, *, references=(), connection=None, check_scope=True):
        status = self.status(actor.workspace_id, task_id, identity=self.identity(actor), operation=operation,
                             connection=connection, check_scope=check_scope)
        if status != "valid":
            return "unknown" if status in {"unverified", "expired"} else "unauthorized"
        if not references:
            return "ready"
        authorization = self.repository.get_task_authorization(actor.workspace_id, task_id, connection=connection)
        task = self.repository.get_creation_task(actor.workspace_id, task_id, connection=connection)
        with (self.repository._connect() if connection is None else nullcontext(connection)) as conn:
            return "ready" if all(self._derived_reference(conn, authorization, task, ref, set()) for ref in references) else "unauthorized"

    def refresh(self, authorization):
        if authorization.revoked_at is not None:
            return
        verdict = self.verify_identity(authorization.identity)
        now = self.clock()
        ready = (verdict == "ready" and self.repository.task_authorization_goal_revision(
            authorization.workspace_id, authorization.task_id) == authorization.goal_revision)
        scope = authorization.resource_scope
        if ready and authorization.issued_at is None:
            try:
                scope = self._scope(authorization.identity, authorization.task_id)
            except TaskAuthorizationDenied as exc:
                ready = False
                verdict = f"scope_refused: {exc}"
        updated = replace(authorization, revision=authorization.revision + 1,
            issued_at=authorization.issued_at or (now if ready else None),
            verified_at=now if ready else authorization.verified_at,
            expires_at=now + TASK_AUTHORIZATION_LIFETIME if ready else authorization.expires_at,
            revoked_at=now if verdict == "unauthorized" else None,
            resource_scope=scope, last_checked_at=now, last_verdict=str(verdict))
        try:
            self.repository.save_task_authorization(updated, expected_revision=authorization.revision)
        except TaskAuthorizationConflict:
            pass  # A newer/revoked record owns the result; this observation cannot restore it.

    def _run(self):
        while not self._stop.is_set():
            try:
                now = self.clock()
                for authorization in self.repository.list_task_authorizations(due_before=now-AUTHORIZATION_OBSERVATION_INTERVAL):
                    if self._stop.is_set(): break
                    if (authorization.revoked_at is None and (authorization.last_checked_at is None
                            or now - authorization.last_checked_at >= AUTHORIZATION_OBSERVATION_INTERVAL)):
                        self.refresh(authorization)
            except Exception as exc:
                self.last_error = exc
                logger.exception("Task authorization observation stopped; existing local authorizations keep their own expiry")
                self._stop.set()
            self._wake.wait(1.0)
            self._wake.clear()

    def start(self):
        if self._thread is not None and self._thread.is_alive(): return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="task-authorization-observation", daemon=True)
        self._thread.start()

    def stop(self, timeout_seconds):
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(max(0, timeout_seconds))
            return not self._thread.is_alive()
        return True
