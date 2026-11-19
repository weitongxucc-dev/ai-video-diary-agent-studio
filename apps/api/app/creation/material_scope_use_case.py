"""Set one work's source scope before production and retain the adoption receipt."""
from dataclasses import dataclass, replace
from datetime import datetime
import hashlib
import json

from app.creation.contracts import TaskEvent
from app.materials.contracts import MediaAuthorityError, material_usage_eligibility
from app.production import automatic_segment_eligible
from app.models import utc_now
from app.source_policy import knowledge_item_readable


@dataclass(frozen=True)
class MaterialScopeCommand:
    request_id: str
    expected_plan_updated_at: datetime
    assets: tuple[tuple[str, datetime], ...]


class MaterialScopeUseCase:
    def __init__(self, repository, connection_factory):
        self.repository = repository
        self._connect = connection_factory

    @staticmethod
    def _event_id(workspace_id, member_id, request_id):
        key = json.dumps([workspace_id, member_id, request_id])
        return "task_material_scope_" + hashlib.sha256(key.encode()).hexdigest()

    def _owned_task(self, connection, workspace_id, task_id, member_id):
        task = self.repository.task_records.get_creation_task(workspace_id, task_id, connection=connection)
        if not member_id or not knowledge_item_readable(user_id=member_id, local_diagnostic=False,
                visibility=task.visibility, owner_user_id=task.owner_user_id):
            raise KeyError(task_id)
        if task.owner_user_id != member_id:
            raise PermissionError("当前成员不能更改此作品的选材范围。")
        return task

    def read(self, workspace_id, task_id, member_id, request_id):
        with self._connect() as connection:
            self._owned_task(connection, workspace_id, task_id, member_id)
            event = self.repository.execution_records.get_task_event(workspace_id,
                self._event_id(workspace_id, member_id, request_id), connection=connection)
            if event.task_id != task_id:
                raise KeyError(request_id)
            return event.payload["receipt"]

    def save(self, workspace_id, task_id, member_id, command):
        assets = sorted(command.assets)
        selected_ids = [asset_id for asset_id, _ in assets]
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("同一素材只能选择一次。")
        event_id = self._event_id(workspace_id, member_id, command.request_id)
        inputs = {"plan_updated_at": command.expected_plan_updated_at.isoformat(),
                  "assets": [[key, version.isoformat()] for key, version in assets]}
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            task = self._owned_task(connection, workspace_id, task_id, member_id)
            try:
                previous = self.repository.execution_records.get_task_event(workspace_id, event_id, connection=connection)
            except KeyError:
                previous = None
            if previous is not None:
                if previous.task_id != task.id or previous.payload["inputs"] != inputs:
                    raise ValueError("原范围请求已绑定到另一作品或输入，请读取原回执。")
                return previous.payload["receipt"]

            plan = self.repository.plan_records.read_creation_plan(connection, workspace_id, task.plan_id)
            if plan.creation_task_id != task.id or plan.source_media is not None:
                raise ValueError("当前作品无需重新指定自动选材范围。")
            if plan.updated_at != command.expected_plan_updated_at:
                raise ValueError("制作方案已变化，请重新读取后选择素材。")
            if (task.stage_status == "running" or task.montage_plan_id or task.current_timeline_version_id
                    or self.repository.job_records.has_open_task_job(connection, workspace_id, task_id)):
                raise ValueError("此作品已有正在进行的制作或已采用镜头，请从当前剪辑中调整。")
            for asset_id, version in assets:
                asset = self.repository.get_asset(workspace_id, asset_id, connection=connection)
                if not knowledge_item_readable(user_id=member_id, local_diagnostic=False,
                        visibility=asset.visibility, owner_user_id=asset.owner_user_id):
                    raise KeyError(asset_id)
                eligibility = material_usage_eligibility(asset, purpose="automatic", expected_asset_version=version)
                if not eligibility.eligible:
                    raise ValueError(eligibility.repair_action)
                if not any(automatic_segment_eligible(asset, segment, workspace_id) for segment in asset.segments):
                    raise ValueError("所选素材当前版本缺少可用于本作品的已就绪视频片段。")
                try:
                    reference = self.repository.workspace_media_reference_for_asset(workspace_id, asset_id, connection=connection)
                    self.repository.resolve_workspace_media_reference(workspace_id, reference, require_registered=True, connection=connection)
                except (KeyError, MediaAuthorityError, OSError) as exc:
                    raise ValueError("所选视频的原文件已不可用，请核对素材库。") from exc
            before = plan.selected_material_asset_ids
            changed = before != selected_ids
            now = utc_now()
            if changed:
                plan = replace(plan, selected_material_asset_ids=selected_ids, updated_at=now)
                self.repository.plan_records.write_plan_payload(connection, workspace_id, plan)
            receipt = {"request_id": command.request_id, "task_id": task.id, "plan_id": plan.id,
                       "plan_updated_at": plan.updated_at.isoformat(), "status": "saved" if changed else "unchanged",
                       "selected_material_asset_ids": selected_ids}
            self.repository.execution_records.append_task_event(TaskEvent(
                event_id, workspace_id, task.id, None, "task_material_scope_saved",
                {"inputs": inputs, "previous_asset_ids": before, "receipt": receipt}, now), connection=connection)
            return receipt
