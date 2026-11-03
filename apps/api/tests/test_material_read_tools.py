"""N06 stage 1: real extension with synthetic HTTP responses, never Pi/media/services."""
from __future__ import annotations
from assistant_sdk_fixtures import project_event_fixture

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

from app.assistant_pi import AssistantUnavailable, _safe_tool_result_summary, run_material_turn
from app.config import Settings
from app.assistant.catalogue import ASSISTANT_BUSINESS_TOOLS

from tool_result_test_fixtures import TOOL_OUTCOME_JS

EXTENSION = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = TOOL_OUTCOME_JS + r"""
let input=''; for await(const chunk of process.stdin) input+=chunk;
const data=JSON.parse(input), tools=new Map(), calls=[];
globalThis.fetch=async(url,init)=>{
  const parsed=new URL(url), path=parsed.pathname+parsed.search;
  calls.push({path,method:init?.method??'GET',headers:Object.fromEntries(init.headers.entries())});
  const body=data.routes[path];
  if(!body) throw new Error('unexpected HTTP path '+path);
  return new Response(JSON.stringify(body.__status ? {detail:body.detail}:body), {status:body.__status??200,headers:{'Content-Type':'application/json'}});
};
const extension=await import(process.argv[1]); extension.default({registerTool:t=>tools.set(t.name,t)});
try {
 const tool=tools.get(data.tool);
 const result=await tool.execute('fixture-call',data.params??{});
 console.log(JSON.stringify({...toolOutcome(result),parameters:tool.parameters,calls,names:[...tools.keys()]}));
} catch(error) {console.log(JSON.stringify({ok:false,error:error.message,calls,names:[...tools.keys()]}));}
"""

def call(tmp_path, tool, routes, *, selection=None, params=None):
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_path), "XDG_CONFIG_HOME": str(tmp_path / "config"),
           "AI_VIDEO_DIARY_AGENT_STAGE": "material", "AI_VIDEO_DIARY_AGENT_API_BASE": "http://synthetic.invalid",
           "AI_VIDEO_DIARY_AGENT_MATERIAL_SELECTION": json.dumps(selection) if selection else "",
           "AI_VIDEO_DIARY_AGENT_AUTHORIZATION": "Bearer synthetic-trusted",
           "AI_VIDEO_DIARY_AGENT_COOKIE": "workspace=synthetic-trusted",
           "AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN": "synthetic-member"}
    result = subprocess.run(["node", "--experimental-strip-types", "--no-warnings", "--input-type=module", "-e", RUNNER, EXTENSION.as_uri()],
                            input=json.dumps({"tool": tool, "routes": routes, "params": params or {}}, ensure_ascii=False),
                            capture_output=True, text=True, cwd=tmp_path, env=env, timeout=10, check=True)
    return json.loads(result.stdout)

@pytest.fixture
def asset():
    return {"id": "asset-one", "file_name": "synthetic-one.mp4", "media_type": "video", "duration_seconds": 8,
            "analysis_status": "ready", "analysis_message": "画面处理完成", "use_state": "available", "user_tags": ["整条标签"],
            "user_description": "用户说明", "upload_context": "本批次背景", "path": "/private/synthetic/media.mp4", "owner_user_id": "private-member",
            "speech_analysis": {"status": "ready", "message": "转写完成", "source_fingerprint": "private-fingerprint", "observations": [
                {"start_ms": 0, "end_ms": 2000, "text": "前段声音陈述"}, {"start_ms": 4000, "end_ms": 6000, "text": "后段声音陈述"}]},
            "segments": [{"id": "segment-one", "asset_id": "asset-one", "start_seconds": 0, "end_seconds": 2,
                "description": "已存描述", "transcript": "已存片段文字", "visual_status": "ready", "user_tags": ["人工标签"], "system_tags": ["系统标签"],
                "understanding": {"status": "ready", "summary": "桌面上的电脑", "facts": [{"kind": "object", "text": "电脑", "sample_times": [0.5]}],
                    "unknowns": ["无法确认型号"], "message": "画面已分析", "ocr_status": "blocked", "ocr_frames": [], "model_id": "not-sent-model"}},
                {"id": "segment-two", "asset_id": "asset-one", "start_seconds": 4, "end_seconds": 6,
                "description": "", "transcript": "", "visual_status": "blocked", "user_tags": [], "system_tags": [],
                "understanding": {"status": "blocked", "summary": "不能伪报的旧画面文字", "facts": [], "unknowns": [], "message": "画面能力未配置",
                    "ocr_status": "ready", "ocr_frames": [{"timestamp_seconds": 4.5, "width": 100, "height": 100,
                        "observations": [{"text": "屏幕字", "confidence": .3, "retrieval_eligible": False, "box": [0, 0, 1, 1]}]}]}}]}

def page(assets, *, total=None, offset=0, limit=100, more=False):
    return {"assets": assets, "total_count": len(assets) if total is None else total, "offset": offset, "limit": limit, "has_more": more}

def bound(asset, segment=None):
    return {"scope": "segment" if segment else "asset", "assetId": asset["id"], **({"segmentId": segment} if segment else {})}

def selected(tmp_path, asset, *, segment=None, params=None):
    return call(tmp_path, "read_selected_material", {f"/api/production/materials/{asset['id']}": asset}, selection=bound(asset, segment), params=params)

def test_unselected_library_reads_all_real_pages_but_sends_only_aggregate_counts(tmp_path, asset):
    assets = [{**asset, "id": f"asset-{index}", "analysis_status": ["pending", "ready", "blocked", "failed"][index % 4]} for index in range(151)]
    out = call(tmp_path, "read_material_library_status", {
        "/api/production/materials?limit=100&offset=0": page(assets[:100], total=151, more=True),
        "/api/production/materials?limit=100&offset=100": page(assets[100:], total=151, offset=100),
    })
    assert out["ok"]
    assert out["result"]["details"]["analysis_counts"] == {"pending": 38, "ready": 38, "blocked": 38, "failed": 37}
    assert out["result"]["details"]["total_count"] == 151
    assert len(out["calls"]) == 2
    output = json.dumps(out["result"], ensure_ascii=False)
    for private in ("前段声音陈述", "桌面上的电脑", "private-member", "/private", "synthetic-one.mp4"):
        assert private not in output
    assert "read_selected_material" in out["names"]
    assert "update_selected_material_tags" in out["names"]

def test_empty_library_is_a_successful_fact_not_a_failure(tmp_path):
    out = call(tmp_path, "read_material_library_status", {"/api/production/materials?limit=100&offset=0": page([])})
    assert out["ok"] and out["result"]["details"]["empty"] is True
    assert out["result"]["details"]["complete"] is True

@pytest.mark.parametrize("status", [403, 404, 500])
@pytest.mark.parametrize("tool,path,params", [
    ("read_material_library_status", "/api/production/materials?limit=100&offset=0", {}),
    ("search_material_library", "/api/production/materials?q=%E7%94%B5%E8%84%91&limit=8&offset=0", {"query": "电脑"}),
    ("read_selected_material", "/api/production/materials/asset-one", {}),
])
def test_http_failure_never_becomes_empty_success_or_exposes_a_local_path(tmp_path, status, tool, path, params):
    out = call(tmp_path, tool, {path: {"__status": status, "detail": f"真实请求失败({status}) /private/synthetic/data"}},
               selection={"scope": "asset", "assetId": "asset-one"} if tool == "read_selected_material" else None, params=params)
    assert not out["ok"] and str(status) in out["error"]
    assert "/private" not in json.dumps(out["result"])
    assert out["result"]["isError"] is True
    assert set(out["result"]["details"]) == {"failure", "invocation_input_id"}
    assert json.loads(out["result"]["content"][0]["text"]) == {"failure": out["result"]["details"]["failure"]}

@pytest.mark.parametrize("second", [page([], total=101, offset=100, more=True), page([], total=102, offset=100), page([], total=101, offset=99)])
def test_incomplete_or_changed_pagination_is_not_reported_as_complete(tmp_path, asset, second):
    out = call(tmp_path, "read_material_library_status", {
        "/api/production/materials?limit=100&offset=0": page([asset] * 100, total=101, more=True),
        "/api/production/materials?limit=100&offset=100": second})
    assert not out["ok"] and "数量尚未确认" in out["error"]

@pytest.mark.parametrize("assets,total,offset", [([], 0, 0), ([], 12, 12)])
def test_zero_search_results_keep_match_count_and_page_shape(tmp_path, assets, total, offset):
    out = call(tmp_path, "search_material_library", {
        f"/api/production/materials?q=%E7%94%B5%E8%84%91&limit=8&offset={offset}": page(assets, total=total, limit=8, offset=offset)}, params={"query": "电脑", "offset": offset})
    assert out["ok"] and out["result"]["details"]["matches"] == []
    assert out["result"]["details"]["total_count"] == total
    assert out["result"]["details"]["has_more"] is False

def test_search_uses_server_query_and_returns_metadata_not_entire_analysis(tmp_path, asset):
    out = call(tmp_path, "search_material_library", {
        "/api/production/materials?q=%E7%94%B5%E8%84%91%20%26%20%E5%AE%A4%E5%86%85&limit=2&offset=4": page([asset], total=9, offset=4, limit=2, more=True)},
        params={"query": "电脑 & 室内", "offset": 4, "limit": 2, "assetId": "model-forged", "workspace_id": "model-forged"})
    assert out["ok"]
    details = out["result"]["details"]
    assert details["matches"][0]["asset_ref"] == "asset:asset-one"
    assert details["total_count"] == 9 and details["has_more"]
    for private in ("segments", "speech_analysis", "path", "owner_user_id", "user_description", "upload_context"):
        assert private not in details["matches"][0]
    assert set(out["parameters"]["properties"]) == {"query", "limit", "offset"}

def test_excluded_content_never_enters_search_candidates_or_selected_analysis(tmp_path, asset):
    asset["use_state"] = "excluded"
    out = call(tmp_path, "search_material_library", {
        "/api/production/materials?q=%E7%94%B5%E8%84%91&limit=8&offset=0": page([asset], limit=8)}, params={"query": "电脑"})
    details = out["result"]["details"]
    assert details["matches"] == [] and details["total_count"] == 1 and details["excluded_count_on_page"] == 1
    assert "不是可用候选数量" in details["total_count_scope"]
    read = selected(tmp_path, asset)["result"]["details"]
    assert read["content_withheld"] is True and read["user_tags"] == ["整条标签"]
    assert "segments" not in read and "speech" not in read and "user_description" not in read
    assert "桌面上的电脑" not in json.dumps(out["result"], ensure_ascii=False)

def test_selected_read_separates_visual_speech_ocr_and_user_evidence(tmp_path, asset):
    out = selected(tmp_path, asset)
    assert out["ok"]
    details = out["result"]["details"]
    assert details["file_name"] == "synthetic-one.mp4" and details["user_description"] == "用户说明"
    first, second = details["segments"]
    assert first["visual"]["facts"][0]["text"] == "电脑" and first["visual"]["unknowns"] == ["无法确认型号"]
    assert first["stored_transcript"] == "已存片段文字" and first["ocr"] == {"status": "blocked", "frames": []}
    assert second["visual"]["status"] == "blocked" and second["visual"]["summary"] == ""
    assert second["ocr"]["frames"][0]["observations"][0] == {"text": "屏幕字", "confidence": .3, "retrieval_eligible": False}
    assert details["speech"]["status"] == "ready" and len(details["speech"]["observations"]) == 2
    output = json.dumps(out["result"], ensure_ascii=False)
    for private in ("/private", "private-member", "private-fingerprint", "not-sent-model", "不能伪报的旧画面文字"):
        assert private not in output

@pytest.mark.parametrize("speech_status", [None, "blocked", "failed"])
def test_missing_or_blocked_speech_never_claims_transcription(tmp_path, asset, speech_status):
    if speech_status is None:
        asset["speech_analysis"] = None
    else:
        asset["speech_analysis"]["status"] = speech_status
        asset["speech_analysis"]["message"] = "能力未就绪"
    out = selected(tmp_path, asset, segment="segment-one")
    assert out["ok"]
    assert out["result"]["details"]["speech"]["status"] == (speech_status or "not_recorded")
    assert out["result"]["details"]["speech"]["observations"] == []
    assert out["result"]["details"]["segments"][0]["stored_transcript"] == "已存片段文字"

def test_selected_segment_and_asset_paging_filter_speech_to_original_time_range(tmp_path, asset):
    out = selected(tmp_path, asset, segment="segment-two", params={"assetId": "model-forged", "segmentId": "segment-one"})
    details = out["result"]["details"]
    assert details["segment_ref"] == "segment:segment-two"
    assert [s["segment_ref"] for s in details["segments"]] == ["segment:segment-two"]
    assert [o["text"] for o in details["speech"]["observations"]] == ["后段声音陈述"]
    assert set(out["parameters"]["properties"]) == {"offset", "limit"}
    paged = selected(tmp_path, asset, params={"offset": 1, "limit": 1})["result"]["details"]
    assert paged["segment_page"] == {"total_count": 2, "offset": 1, "limit": 1, "has_more": False}
    assert paged["speech"]["observations"] == details["speech"]["observations"]

def test_different_file_at_same_seconds_does_not_reuse_previous_file_identity(tmp_path, asset):
    first = selected(tmp_path, asset, segment="segment-one")["result"]["details"]
    second_asset = copy.deepcopy(asset)
    second_asset.update(id="asset-two", file_name="synthetic-two.mp4")
    for segment in second_asset["segments"]:
        segment["asset_id"] = "asset-two"
    second = selected(tmp_path, second_asset, segment="segment-one")["result"]["details"]
    assert first["segments"][0]["start_seconds"] == second["segments"][0]["start_seconds"]
    assert first["asset_ref"] != second["asset_ref"] and first["file_name"] != second["file_name"]

@pytest.mark.parametrize("mismatch", ["asset", "segment"])
def test_selected_read_rejects_mismatched_http_object_before_projecting_text(tmp_path, asset, mismatch):
    reply = copy.deepcopy(asset)
    if mismatch == "asset":
        reply["id"] = "another-asset"
    else:
        reply["segments"][0]["asset_id"] = "another-asset"
    out = call(tmp_path, "read_selected_material", {"/api/production/materials/asset-one": reply}, selection=bound(asset, "segment-one"))
    assert not out["ok"] and out["result"]["isError"] is True
    assert set(out["result"]["details"]) == {"failure", "invocation_input_id"}
    assert json.loads(out["result"]["content"][0]["text"]) == {"failure": out["result"]["details"]["failure"]}

@pytest.mark.parametrize("name", ["/private/synthetic.mp4", r"C:\private\synthetic.mp4"])
def test_selected_file_name_and_embedded_diagnostics_do_not_send_local_paths(tmp_path, asset, name):
    asset["file_name"] = name
    asset["analysis_message"] = "读取 /private/synthetic/data 失败"
    out = selected(tmp_path, asset)
    assert out["result"]["details"]["file_name"] == "synthetic.mp4"
    assert "/private" not in json.dumps(out["result"], ensure_ascii=False)

@pytest.mark.parametrize("tool", ["read_selected_material", "update_selected_material_tags"])
def test_unselected_cannot_guess_identity_or_read_write_an_object(tmp_path, tool):
    out = call(tmp_path, tool, {}, params={"assetId": "forged", "path": "/private/forged", "action": "replace", "tags": ["forged"]})
    assert not out["ok"] and "请先选择" in out["error"] and out["calls"] == []

def test_read_uses_trusted_headers_on_every_page_and_ignores_model_identity(tmp_path, asset):
    out = call(tmp_path, "read_material_library_status", {
        "/api/production/materials?limit=100&offset=0": page([asset] * 100, total=101, more=True),
        "/api/production/materials?limit=100&offset=100": page([asset], total=101, offset=100)},
        params={"Authorization": "forged", "member": "forged", "workspace_id": "forged"})
    assert out["ok"]
    for request in out["calls"]:
        assert request["method"] == "GET"
        assert request["headers"] == {"content-type": "application/json", "authorization": "Bearer synthetic-trusted",
                                      "cookie": "workspace=synthetic-trusted", "x-local-member-token": "synthetic-member"}
    assert "forged" not in json.dumps(out["result"])

def test_unselected_material_turn_binds_browse_session_and_workspace_capabilities(monkeypatch, tmp_path):
    seen = {}
    monkeypatch.setattr("app.assistant_pi._run_turn", lambda _settings, **kwargs: (seen.update(kwargs) or ("库状态", None)))
    result = run_material_turn(Settings(model_root_path=str(tmp_path)), workspace_id="trusted-space", conversation_id="trusted-conversation", message="库里有什么",
                               local_member_token="trusted-member", cookie="trusted-cookie", authorization="trusted-auth", caller_user_id="trusted-user")
    assert result == ("库状态", None)
    assert seen["material_selection"] is None and seen["session_key"] == "conversation:trusted-conversation"
    assert set(seen["tool_names"].split(",")) == set(ASSISTANT_BUSINESS_TOOLS["home"])
    assert seen["task_id"] == "" and seen["stage"] == "" and seen["preview_kind"] == "plan_preview"
    assert not {"read_selected_material", "update_selected_material_tags", "start_creation_task_execution"}.intersection(seen["tool_names"].split(","))
    assert seen["local_member_token"] == "trusted-member" and seen["caller_user_id"] == "trusted-user"
    assert seen["authorization"] == "trusted-auth" and seen["cookie"] == "trusted-cookie"

@pytest.mark.parametrize("tool", ["read_material_library_status", "search_material_library"])
def test_library_tool_failure_cannot_settle_as_a_successful_material_answer(tool):
    events = [{"type": "tool_execution_end", "toolName": tool, "isError": True, "result": {"details": {}}},
              {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "不能冒称成功"}]}}, {"type": "agent_end"}]
    with pytest.raises(AssistantUnavailable):
        project_event_fixture("\n".join(json.dumps(event) for event in events), "material_result")

def test_library_tool_summary_uses_actual_counts_and_never_claims_ocr_is_ready():
    summary = _safe_tool_result_summary("read_material_library_status", {"details": {"total_count": 151, "analysis_counts": {"ready": 1}}}, False)
    assert "151" in summary and "转写和OCR需另看分项" in summary
    assert "成功" not in _safe_tool_result_summary("search_material_library", {}, True)
