"""Bounded source evidence reads from the material owner's existing payload.

No derived state or inference: manual corrections, sampled visual observations
and raw ASR alignment remain distinguishable, with the original source clock.
"""
import json
import math
from datetime import datetime

from app.source_policy import knowledge_item_readable


class MaterialEvidenceConflict(ValueError):
    pass


class MaterialEvidenceReader:
    def __init__(self, connection_factory):
        self._connect = connection_factory

    def read(self, workspace_id, asset_id, *, member_id, local_diagnostic=False,
             expected_updated_at=None, start_seconds=0, end_seconds=None,
             kind="segments", offset=0, limit=10):
        if (kind not in {"segments", "speech", "words"} or type(offset) is not int or offset < 0
                or type(limit) is not int or not 1 <= limit <= 20
                or not math.isfinite(start_seconds) or start_seconds < 0
                or end_seconds is not None and (not math.isfinite(end_seconds) or end_seconds <= start_seconds)):
            raise ValueError("素材证据窗口无效")
        fields = ("id", "updated_at", "file_name", "duration_seconds", "media_type", "use_state",
                  "owner_user_id", "user_description", "upload_context", "user_tags", "analysis_status")
        with self._connect() as conn:
            conn.execute("BEGIN")
            # SQL extracts metadata without transferring the transcript/segment
            # arrays to Python; JSON windows below apply LIMIT in the store.
            selection = ",".join(f"json_extract(payload,'$.{name}') AS {name}" for name in fields)
            row = conn.execute(f"SELECT visibility,{selection},"
                "json_extract(payload,'$.speech_analysis.status') AS speech_status,"
                "json_extract(payload,'$.speech_analysis.model_id') AS speech_model_id,"
                "json_extract(payload,'$.speech_analysis.source_fingerprint') AS source_fingerprint,"
                "json_extract(payload,'$.speech_analysis.timing_granularity') AS timing_granularity,"
                "json_extract(payload,'$.speech_analysis.audio_present') AS audio_present "
                "FROM material_assets WHERE workspace_id=? AND id=?", (workspace_id, asset_id)).fetchone()
            if row is None or not knowledge_item_readable(user_id=member_id, local_diagnostic=local_diagnostic,
                    visibility=row["visibility"], owner_user_id=row["owner_user_id"]):
                raise KeyError(asset_id)
            if expected_updated_at is not None:
                try:
                    same_version = datetime.fromisoformat(row["updated_at"]) == datetime.fromisoformat(expected_updated_at)
                except ValueError:
                    same_version = False
                if not same_version:
                    raise MaterialEvidenceConflict("素材版本已变化，请回读后重新判断，不沿旧时间执行")
            if row["use_state"] == "excluded":
                return {"asset_id": asset_id, "updated_at": row["updated_at"],
                        "use_state": "excluded", "content_withheld": True}
            array = {"segments": "segments", "speech": "speech_analysis.observations",
                     "words": "speech_analysis.word_observations"}[kind]
            start_key, end_key, scale = ("start_seconds", "end_seconds", 1) if kind == "segments" else ("start_ms", "end_ms", 1000)
            where = (f"json_extract(e.value,'$.{end_key}') > ? AND "
                     f"(? IS NULL OR json_extract(e.value,'$.{start_key}') < ?)")
            if kind != 'segments':
                where += " AND json_extract(a.payload,'$.speech_analysis.status')='ready'"
            values = (workspace_id, asset_id, start_seconds * scale,
                      end_seconds * scale if end_seconds is not None else None,
                      end_seconds * scale if end_seconds is not None else None)
            query = f"FROM material_assets a,json_each(a.payload,'$.{array}') e WHERE a.workspace_id=? AND a.id=? AND {where}"
            total = conn.execute("SELECT count(*) " + query, values).fetchone()[0]
            rows = conn.execute("SELECT e.key,e.value " + query + " ORDER BY CAST(e.key AS INTEGER) LIMIT ? OFFSET ?",
                                (*values, limit, offset)).fetchall()
        truncated = []

        def bounded(text, key):
            text = text or ""
            if len(text) > 2000:
                truncated.append(key)
            return text[:2000]

        def visual_evidence(visual, segment_id):
            def window(values, key, size=8):
                if len(values) > size:
                    truncated.append(f"{segment_id}.{key}")
                return values[:size]
            return {"status": visual.get("status", "not_recorded"),
                "summary": bounded(visual.get("summary"), f"{segment_id}.visual.summary"),
                "sample_times": visual.get("sample_times", []),
                "facts": [{"kind": fact["kind"], "label": fact.get("label"),
                    "text": bounded(fact["text"], f"{segment_id}.visual.fact"), "sample_times": fact["sample_times"]}
                    for fact in window(visual.get("facts", []), "visual.facts")],
                "unknowns": [bounded(text, f"{segment_id}.unknown") for text in window(visual.get("unknowns", []), "visual.unknowns")],
                "ocr_status": visual.get("ocr_status", "not_recorded"),
                "ocr_frames": [{"timestamp_seconds": frame["timestamp_seconds"],
                    "observations": [{"text": bounded(item["text"], f"{segment_id}.ocr.text"),
                        "confidence": item["confidence"], "retrieval_eligible": item.get("retrieval_eligible", False)}
                        for item in window(frame["observations"], "ocr.observations")]}
                    for frame in window(visual.get("ocr_frames", []), "ocr.frames", 3)]}

        items = []
        for entry in rows:
            value = json.loads(entry["value"])
            if kind == "segments":
                visual = value.get("understanding") or {}
                item = {key: value.get(key) for key in ("id", "start_seconds", "end_seconds", "review_status", "usage_role", "user_tags")}
                item.update(description=bounded(value.get("description"), f"{value['id']}.description"),
                    corrected_transcript=bounded(value.get("transcript"), f"{value['id']}.transcript"),
                    visual=visual_evidence(visual, value['id']))
            else:
                item = {"source_index": int(entry["key"]), "start_ms": value["start_ms"], "end_ms": value["end_ms"],
                        "text": bounded(value["text"], f"{kind}.{entry['key']}.text")}
            items.append(item)
        return {"asset_id": asset_id, "updated_at": row["updated_at"], "file_name": row["file_name"],
            "media_type": row["media_type"], "duration_seconds": row["duration_seconds"], "use_state": row["use_state"],
            "user_description": bounded(row["user_description"], "user_description"),
            "upload_context": bounded(row["upload_context"], "upload_context"),
            "speech_status": row["speech_status"] or "not_recorded", "speech_model_id": row["speech_model_id"],
            "source_sha256": row["source_fingerprint"], "timing_granularity": row["timing_granularity"] or "segment",
            "audio_present": None if row["audio_present"] is None else bool(row["audio_present"]),
            "clock": "original_source", "kind": kind, "range_seconds": [start_seconds, end_seconds],
            "items": items, "page": {"offset": offset, "limit": limit, "total_count": total, "has_more": offset + len(items) < total},
            "truncated_fields": truncated,
            "evidence_policy": "人工转录优先于ASR文字；words仅是原ASR对齐，不为人工纠正自动提供逐词时间。采样画面、声音陈述、业务说明与创作用途分别判断；未观察范围不能宣称通过。"}
