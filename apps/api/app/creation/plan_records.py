"""Plan records, including the original atomic Plan/Task insertion port."""
from __future__ import annotations

import json
import sqlite3

from app.creation.contracts import (
    CreationTask as _CreationTask,
    CreationPlanPersonReference as _CreationPlanPersonReference,
    CreationPlanVoiceReference as _CreationPlanVoiceReference,
)
from app.creation.plan_contracts import (
    CreationPlan, CreationPlanKnowledgeReference, CreationPlanSourceReference,
    CreationPlanPronunciationReference, CreationPlanBlocker,
)
from app.materials.contracts import MaterialSegmentMatch, material_usage_eligibility
from app.models import utc_now
from app.persistence_json import _json_payload, _with_datetime


class CreationPlanRecordStore:
    def __init__(self, connection_factory, *, read_asset, decode_asset):
        self._connect = connection_factory
        self._read_asset, self._decode_asset = read_asset, decode_asset

    def insert_confirmed_creation_plan(
        self, conn: sqlite3.Connection, plan: CreationPlan, task: _CreationTask,
    ) -> None:
        now = utc_now()
        plan.creation_task_id = task.id
        plan.updated_at = now
        task.updated_at = now
        for match in plan.material_matches:
            asset_id = match.asset_ref.removeprefix("asset:")
            segment_id = match.segment_ref.removeprefix("segment:")
            asset_row = self._read_asset(
                conn, plan.workspace_id, asset_id,
            )
            if asset_row is None:
                raise ValueError("创作方案引用的素材不属于当前工作区")
            asset = self._decode_asset(json.loads(str(asset_row["payload"])))
            segment = next((item for item in asset.segments if item.id == segment_id), None)
            if segment is None or not material_usage_eligibility(asset,segment,purpose='manual').eligible:
                raise ValueError("创作方案引用的素材片段不可用")
        conn.execute(
            "INSERT INTO creation_plans (id, workspace_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?)",
            (plan.id, plan.workspace_id, _json_payload(plan), plan.visibility, plan.updated_at.isoformat()),
        )
        conn.execute(
            "INSERT INTO creation_tasks (id, workspace_id, plan_id, payload, visibility, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (task.id, task.workspace_id, task.plan_id, _json_payload(task), task.visibility, task.updated_at.isoformat()),
        )


    def get_creation_plan(self, workspace_id: str, plan_id: str) -> CreationPlan:
        with self._connect() as conn:
            return self.read_creation_plan(conn, workspace_id, plan_id)


    def read_creation_plan(self, connection, workspace_id, plan_id):
        row = connection.execute(
            """SELECT payload, visibility FROM creation_plans
            WHERE workspace_id = ? AND id = ?""",
            (workspace_id, plan_id),
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        data = json.loads(str(row["payload"]))
        if "visibility" in row.keys():
            data["visibility"] = str(row["visibility"])
        return self.creation_plan_from_payload(data)


    @staticmethod
    def creation_plan_from_payload(data: dict[str, object]) -> CreationPlan:
        knowledge_payloads = data.pop("knowledge_references", [])
        source_payloads = data.pop("source_references", [])
        voice_payload = data.pop("selected_voice", None)
        # Missing key identifies historical payloads only. Explicit None stays
        # unselected even if an old voice relationship exists.
        person_payload = data.pop("selected_person", None) if "selected_person" in data else (
            {"profile_id": voice_payload["person_profile_id"], "version": voice_payload["person_version"]}
            if isinstance(voice_payload, dict) and voice_payload.get("person_profile_id")
            and voice_payload.get("person_version") else None)
        pronunciation_payloads = data.pop("pronunciation_references", [])
        blocker_payloads = data.pop("blockers", [])
        material_match_payloads = data.pop("material_matches", [])
        data = _with_datetime(data, "created_at", "confirmed_at", "updated_at")
        return CreationPlan(
            **data,
            knowledge_references=[
                CreationPlanKnowledgeReference(**item)
                for item in knowledge_payloads
                if isinstance(item, dict)
            ],
            source_references=[
                CreationPlanSourceReference(**item)
                for item in source_payloads
                if isinstance(item, dict)
            ],
            selected_voice=(
                _CreationPlanVoiceReference(**voice_payload)
                if isinstance(voice_payload, dict)
                else None
            ),
            selected_person=(_CreationPlanPersonReference(**person_payload)
                             if isinstance(person_payload, dict) else None),
            pronunciation_references=[
                CreationPlanPronunciationReference(**item)
                for item in pronunciation_payloads
                if isinstance(item, dict)
            ],
            blockers=[
                CreationPlanBlocker(**item)
                for item in blocker_payloads
                if isinstance(item, dict)
            ],
            material_matches=[
                MaterialSegmentMatch(**item)
                for item in material_match_payloads
                if isinstance(item, dict)
            ],
        )


    def read_knowledge_adoption_plan(self, connection, workspace_id, plan_id):
        row = connection.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, plan_id),
        ).fetchone()
        if row is None:
            raise KeyError(plan_id)
        return self.creation_plan_from_payload(json.loads(row["payload"]))

    def write_knowledge_adopted_plan(self, connection, workspace_id, plan):
        connection.execute(
            "UPDATE creation_plans SET payload=?,updated_at=? WHERE workspace_id=? AND id=?",
            (_json_payload(plan), plan.updated_at.isoformat(), workspace_id, plan.id),
        )

    @staticmethod
    def knowledge_reference_keys(connection, workspace_id, plan_id):
        row = connection.execute(
            "SELECT payload FROM creation_plans WHERE workspace_id=? AND id=?",
            (workspace_id, plan_id),
        ).fetchone()
        return {(item["id"], item["version"])
                for item in json.loads(row["payload"])["knowledge_references"]}
