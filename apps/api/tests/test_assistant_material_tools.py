"""Direct selected-material tool contracts; synthetic request callback, no live Pi/HTTP."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import subprocess

import pytest

MODULE = Path(__file__).parents[1] / "agent_tools" / "material_tools.ts"
NODE = os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node")
RUNNER = r"""
const {registerMaterialTools} = await import(process.argv[1]);
let raw=''; for await (const part of process.stdin) raw+=part;
const input=JSON.parse(raw), calls=[], tools=new Map();
const original=structuredClone(input.asset); let asset=structuredClone(input.asset);
let reads=0, writes=0;
const utcVersion=version=>version.replace(/\+00:00$/,'Z');
registerMaterialTools({registerTool:tool=>tools.set(tool.name,tool)}, {
  selection:input.selection,
  request:async(path,init)=>{
    const body=init?.body ? JSON.parse(init.body):null;
    calls.push({path,method:init?.method||'GET',body});
    if (!init) {
      reads++;
      if ((input.failure==='read' && reads===1) || (input.failure==='readback' && reads===2)) throw new Error('真实请求拒绝（403）');
      const reply=structuredClone(asset);
      if (input.failure==='wrong_asset') reply.id='another-asset';
      if (reads===2 && input.failure==='mismatch') {
        const object=input.selection?.scope==='segment'?reply.segments.find(s=>s.id===input.selection.segmentId):reply;
        object.user_tags=['concurrent-change'];
      }
      if (reads===2 && input.failure==='concurrent_readback') reply.updated_at='2026-10-10T00:00:02.000001Z';
      return reply;
    }
    if(input.failure==='write') throw new Error('真实写入失败（500）');
    if(input.failure==='cas') throw new Error('素材版本已变化（409）');
    if(utcVersion(body.expected_asset_updated_at)!==utcVersion(asset.updated_at)) throw new Error('素材版本已变化（409）');
    writes++;
    if(path.endsWith('/tags')) asset.user_tags=body.user_tags;
    else {
      const segment=asset.segments.find(s=>path.endsWith('/segments/'+encodeURIComponent(s.id)));
      if(!segment) throw new Error('wrong route');
      if(utcVersion(body.expected_segment_updated_at)!==utcVersion(segment.updated_at)) throw new Error('片段版本已变化（409）');
      // Existing PUT defaults erase these fields if a caller omits them.
      segment.description=body.description??''; segment.transcript=body.transcript??'';
      segment.review_status=body.review_status??'raw';
      if(body.usage_role!==undefined) segment.usage_role=body.usage_role;
      if(body.collection_ids!==undefined) segment.collection_ids=body.collection_ids;
      segment.user_tags=body.user_tags;
      segment.updated_at=`2026-10-10T00:00:0${writes+1}Z`;
    }
    asset.updated_at=`2026-10-10T00:00:0${writes+1}Z`;
    const submitted=structuredClone(asset);
    if(input.failure==='write_reply_missing_version') delete submitted.updated_at;
    if(input.failure==='write_reply_wrong_asset') submitted.id='another-asset';
    return submitted;
  }
});
const tool=tools.get(input.tool||'update_selected_material_tags');
try {
 const steps=[];
 for (const [index,operation] of (input.operations||[{tool:input.tool,params:input.params}]).entries()) {
   if(input.externalBeforeOperation===index) {
     if(input.externalSegmentOnly) asset.segments[0].updated_at='2026-10-10T00:00:02.000001Z';
     else asset.updated_at='2026-10-10T00:00:02.000001Z';
   }
   try { steps.push({ok:true,result:await tools.get(operation.tool||'update_selected_material_tags').execute('synthetic-call',operation.params||{})}); }
   catch(error) { steps.push({ok:false,error:error.message}); }
 }
 const last=steps.at(-1);
 console.log(JSON.stringify({...last,steps,calls,asset,original,selection:input.selection,parameters:tool.parameters}));
} catch(error) { console.log(JSON.stringify({ok:false,error:error.message,calls,asset,original})); }
"""


@pytest.fixture
def asset():
    return {
        "id": "asset-a", "updated_at":"2026-10-10T00:00:01Z", "user_tags": ["原整条"], "file_name": "synthetic.mp4",
        "source_path": "/synthetic/private/not-for-model.mp4", "owner_user_id": "not-for-model",
        "segments": [
            {"id": "segment-1", "updated_at":"2026-10-10T00:00:01Z", "asset_id": "asset-a", "description": "办公电脑",
             "transcript": "合成文字", "review_status": "approved", "usage_role": "b_roll",
             "collection_ids": ["collection-1"], "system_tags": ["场景:办公", "物体:电脑"],
             "user_tags": ["原片段"], "start_seconds": 0, "end_seconds": 2},
            {"id": "segment-2", "updated_at":"2026-10-10T00:00:01Z", "asset_id": "asset-a", "description": "第二片段",
             "transcript": "不改", "review_status": "raw", "usage_role": "unassigned",
             "collection_ids": [], "system_tags": [], "user_tags": ["保留"],
             "start_seconds": 2, "end_seconds": 5},
        ],
    }


def call(tmp_path, asset, *, scope="asset", selection="bound", tool=None, action="add", tags=None, failure=None, operations=None, external_before_operation=None, external_segment_only=False, **extra):
    selected = {"scope": scope, "assetId": "asset-a"}
    if scope == "segment":
        selected["segmentId"] = "segment-1"
    if selection != "bound":
        selected = selection
    data = {"asset": asset, "selection": selected, "params": {"action": action, "tags": tags if tags is not None else ["新增"], **extra}, "failure": failure}
    if tool:
        data["tool"] = tool
    if operations is not None:
        data.update(operations=operations,externalBeforeOperation=external_before_operation,externalSegmentOnly=external_segment_only)
    env = {key: os.environ[key] for key in ("PATH", "HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "TMPDIR") if key in os.environ}
    completed = subprocess.run(
        [NODE, "--experimental-strip-types", "--input-type=module", "-e", RUNNER, MODULE.as_uri()],
        input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True,
        cwd=tmp_path, env=env, timeout=10, check=True,
    )
    return json.loads(completed.stdout)


@pytest.mark.parametrize("scope", ["asset", "segment"])
def test_read_returns_only_bound_tag_facts(tmp_path, asset, scope):
    out = call(tmp_path, asset, scope=scope, tool="read_selected_material")
    assert out["ok"]
    details = out["result"]["details"]
    assert details["scope"] == scope and details["asset_ref"] == "asset:asset-a"
    assert details["user_tags"] == (["原整条"] if scope == "asset" else ["原片段"])
    assert out["calls"] == [{"path": "/api/production/materials/asset-a", "method": "GET", "body": None}]
    assert "source_path" not in json.dumps(out["result"]) and "owner_user_id" not in json.dumps(out["result"])
    assert out["parameters"]["additionalProperties"] is False


@pytest.mark.parametrize("scope", ["asset", "segment"])
@pytest.mark.parametrize("action,tags,expected", [
    ("replace", ["替换"], ["替换"]),
    ("add", ["新增"], ["原", "新增"]),
    ("remove", ["原"], []),
])
def test_explicit_tag_actions_write_then_read_same_selected_object(tmp_path, asset, scope, action, tags, expected):
    target = asset if scope == "asset" else asset["segments"][0]
    target["user_tags"] = ["原"]
    before = copy.deepcopy(asset)
    out = call(tmp_path, asset, scope=scope, action=action, tags=tags, assetId="model-untrusted", owner="model-untrusted")
    assert out["ok"]
    assert [c["method"] for c in out["calls"]] == ["GET", "PUT", "GET"]
    assert out["calls"][1]["path"] == "/api/production/materials/asset-a/" + ("tags" if scope == "asset" else "segments/segment-1")
    assert out["result"]["details"]["user_tags"] == expected
    assert out["result"]["details"]["changed"] is True
    assert out["parameters"]["additionalProperties"] is False
    assert set(out["parameters"]["properties"]) == {"action", "tags"}
    target_after = out["asset"] if scope == "asset" else out["asset"]["segments"][0]
    target_after["user_tags"] = ["原"]
    out["asset"]["updated_at"] = before["updated_at"]
    if scope == "segment":
        target_after["updated_at"] = before["segments"][0]["updated_at"]
    assert out["asset"] == before  # Includes all unrelated segments, system tags and metadata.
    if scope == "segment":
        assert set(out["calls"][1]["body"]) == {"description", "transcript", "review_status", "usage_role", "collection_ids", "user_tags", "expected_asset_updated_at", "expected_segment_updated_at"}
    else:
        assert set(out["calls"][1]["body"]) == {"user_tags", "expected_asset_updated_at"}


def test_explicit_clear_and_normalization(tmp_path, asset):
    out = call(tmp_path, asset, action="replace", tags=[])
    assert out["ok"] and out["result"]["details"]["user_tags"] == []
    out = call(tmp_path, asset, action="replace", tags=[" 城市 ", "城市", "", "🌅" * 81])
    assert out["ok"] and out["result"]["details"]["user_tags"] == ["城市", "🌅" * 80]


def test_no_change_does_not_write(tmp_path, asset):
    out = call(tmp_path, asset, action="add", tags=["原整条"])
    assert out["ok"] and out["result"]["details"]["changed"] is False
    assert [c["method"] for c in out["calls"]] == ["GET"]


@pytest.mark.parametrize("selection", [None, {"scope": "segment", "assetId": "asset-a", "segmentId": "missing"}])
def test_no_selection_or_missing_segment_never_guesses_or_writes(tmp_path, asset, selection):
    out = call(tmp_path, asset, selection=selection)
    assert not out["ok"] and not any(c["method"] == "PUT" for c in out["calls"])
    assert out["asset"] == asset


@pytest.mark.parametrize("failure", ["read", "wrong_asset", "write"])
def test_read_scope_or_write_failure_is_not_success(tmp_path, asset, failure):
    out = call(tmp_path, asset, failure=failure)
    assert not out["ok"] and out["asset"] == asset
    assert out["error"]
    if failure != "write":
        assert not any(c["method"] == "PUT" for c in out["calls"])


@pytest.mark.parametrize("failure", ["readback", "mismatch"])
def test_committed_but_unverified_readback_is_not_reported_as_saved(tmp_path, asset, failure):
    out = call(tmp_path, asset, failure=failure)
    assert not out["ok"] and "无法确认" in out["error"]
    assert [c["method"] for c in out["calls"]] == ["GET", "PUT", "GET"]
    assert out["asset"] != asset  # Error does not falsely imply the PUT was rolled back.


def test_add_over_limit_is_rejected_before_write(tmp_path, asset):
    asset["user_tags"] = [f"tag-{i}" for i in range(50)]
    out = call(tmp_path, asset)
    assert not out["ok"] and "最多50" in out["error"]
    assert [c["method"] for c in out["calls"]] == ["GET"]


@pytest.mark.parametrize("scope", ["asset", "segment"])
def test_selected_version_change_blocks_tool_write(tmp_path, asset, scope):
    asset["updated_at"] = "2026-10-10T00:00:02Z"
    asset["segments"][0]["updated_at"] = "2026-10-10T00:00:02Z"
    selection = {"scope":scope,"assetId":"asset-a", "expectedUpdatedAt":"2026-10-10T00:00:01Z"}
    if scope == "segment":
        selection.update(segmentId="segment-1", expectedUpdatedAt=asset["updated_at"], expectedSegmentUpdatedAt="2026-10-10T00:00:01Z")
    out = call(tmp_path, asset, selection=selection)
    assert not out["ok"] and "版本已变化" in out["error"]
    assert all(item["method"] == "GET" for item in out["calls"])
    assert out["asset"] == out["original"]


@pytest.mark.parametrize("scope", ["asset", "segment"])
@pytest.mark.parametrize("failure", [None, "cas"])
def test_frozen_versions_are_sent_to_atomic_mutation(tmp_path, asset, scope, failure):
    asset["updated_at"] = "2026-10-10T00:00:02Z"
    asset["segments"][0]["updated_at"] = "2026-10-10T00:00:01Z"
    selection = {"scope": scope, "assetId": "asset-a", "expectedUpdatedAt": asset["updated_at"]}
    if scope == "segment":
        selection.update(segmentId="segment-1", expectedSegmentUpdatedAt=asset["segments"][0]["updated_at"])
    out = call(tmp_path, asset, selection=selection, failure=failure)
    body = out["calls"][1]["body"]
    assert body["expected_asset_updated_at"] == selection["expectedUpdatedAt"]
    if scope == "segment":
        assert body["expected_segment_updated_at"] == selection["expectedSegmentUpdatedAt"]
    assert out["ok"] is (failure is None)
    if failure:
        assert "409" in out["error"]
        assert out["asset"] == out["original"]
        assert [call["method"] for call in out["calls"]] == ["GET", "PUT"]


@pytest.mark.parametrize("scope", ["asset", "segment"])
def test_own_confirmed_write_advances_only_turn_versions_for_read_and_second_write(tmp_path, asset, scope):
    selection={"scope":scope,"assetId":asset["id"],"expectedUpdatedAt":asset["updated_at"]}
    if scope=="segment":
        selection.update(segmentId="segment-1",expectedSegmentUpdatedAt=asset["segments"][0]["updated_at"])
    out=call(tmp_path,asset,selection=selection,operations=[
        {"params":{"action":"add","tags":["第一次"]}},
        {"tool":"read_selected_material"},
        {"params":{"action":"add","tags":["第二次"]}},
        {"tool":"read_selected_material"},
    ])
    assert all(step["ok"] for step in out["steps"]),out
    puts=[item for item in out["calls"] if item["method"]=="PUT"]
    assert puts[1]["body"]["expected_asset_updated_at"]=="2026-10-10T00:00:02Z"
    if scope=="segment":
        assert puts[1]["body"]["expected_segment_updated_at"]=="2026-10-10T00:00:02Z"
    assert out["selection"]==selection  # Persisted/user choice is not mutated.
    assert out["steps"][-1]["result"]["details"]["user_tags"][-2:]==["第一次","第二次"]


@pytest.mark.parametrize("scope", ["asset", "segment"])
def test_external_revision_after_own_write_is_rejected_even_within_same_millisecond(tmp_path, asset, scope):
    selection={"scope":scope,"assetId":asset["id"],"expectedUpdatedAt":asset["updated_at"]}
    if scope=="segment":
        selection.update(segmentId="segment-1",expectedSegmentUpdatedAt=asset["segments"][0]["updated_at"])
    out=call(tmp_path,asset,selection=selection,external_before_operation=1,external_segment_only=scope=="segment",operations=[
        {"params":{"action":"add","tags":["自己写入"]}},
        {"tool":"read_selected_material"},
        {"params":{"action":"add","tags":["不得写入"]}},
    ])
    assert out["steps"][0]["ok"]
    assert all(not step["ok"] and "版本已变化" in step["error"] for step in out["steps"][1:])
    assert sum(item["method"]=="PUT" for item in out["calls"])==1
    assert out["selection"]==selection


@pytest.mark.parametrize("scope", ["asset", "segment"])
@pytest.mark.parametrize("failure", ["write", "readback", "mismatch", "concurrent_readback", "write_reply_missing_version", "write_reply_wrong_asset"])
def test_failed_write_or_unverified_readback_never_advances_turn_expectation(tmp_path, asset, failure, scope):
    selection={"scope":scope,"assetId":asset["id"],"expectedUpdatedAt":asset["updated_at"]}
    if scope=="segment":
        selection.update(segmentId="segment-1",expectedSegmentUpdatedAt=asset["segments"][0]["updated_at"])
    out=call(tmp_path,asset,selection=selection,failure=failure,operations=[
        {"params":{"action":"add","tags":["已请求"]}},
        {"tool":"read_selected_material"},
        {"params":{"action":"add","tags":["第二次请求"]}},
    ])
    assert not out["steps"][0]["ok"]
    if failure=="write":
        assert out["steps"][1]["ok"]  # Original version remains valid.
        assert all(item["body"]["expected_asset_updated_at"]==asset["updated_at"] for item in out["calls"] if item["method"]=="PUT")
    else:
        assert all(not step["ok"] and "版本已变化" in step["error"] for step in out["steps"][1:])
        assert sum(item["method"]=="PUT" for item in out["calls"])==1
    assert out["selection"]==selection


@pytest.mark.parametrize("scope", ["asset", "segment"])
@pytest.mark.parametrize("microsecond_matches", [True,False])
def test_python_utc_suffix_and_api_z_preserve_exact_microsecond_versions(tmp_path,asset,scope,microsecond_matches):
    asset["updated_at"]="2026-10-10T00:00:01.123456Z"
    asset["segments"][0]["updated_at"]=asset["updated_at"]
    frozen="2026-10-10T00:00:01.123456+00:00"
    other="2026-10-10T00:00:01.123457+00:00"
    selection={"scope":scope,"assetId":asset["id"],"expectedUpdatedAt":frozen if microsecond_matches or scope=="segment" else other}
    if scope=="segment":
        selection.update(segmentId="segment-1",expectedSegmentUpdatedAt=frozen if microsecond_matches else other)
    out=call(tmp_path,asset,selection=selection,operations=[
        {"tool":"read_selected_material"},
        {"params":{"action":"add","tags":["第一次"]}},
        {"tool":"read_selected_material"},
        {"params":{"action":"add","tags":["第二次"]}},
    ])
    if microsecond_matches:
        assert all(step["ok"] for step in out["steps"]),out
        puts=[item for item in out["calls"] if item["method"]=="PUT"]
        assert puts[0]["body"]["expected_asset_updated_at"]==frozen
        assert puts[1]["body"]["expected_asset_updated_at"]=="2026-10-10T00:00:02Z"
        if scope=="segment":
            assert puts[0]["body"]["expected_segment_updated_at"]==frozen
    else:
        assert all(not step["ok"] and "版本已变化" in step["error"] for step in out["steps"])
        assert all(item["method"]=="GET" for item in out["calls"])
    assert out["selection"]==selection


def test_observation_tag_suggestions_are_read_only_and_adoption_is_explicit(tmp_path, asset):
    segment = asset["segments"][0]
    segment["description"] = ""
    segment["understanding"] = {"status": "ready", "facts": [{"kind": "action", "label": "人物走路",
        "text": "人物走过街道", "sample_times": [.3, 1.7]}]}
    result = call(tmp_path, asset, scope="segment", tool="suggest_selected_material_tags")
    assert result["ok"] is True
    assert result["result"]["details"]["suggestions"][0] == {"label": "人物走路", "source": "visual_observation",
        "reason": "人物走过街道", "kind": "action", "sample_times": [.3, 1.7], "segment_ref": "segment:segment-1"}
    assert result["asset"] == result["original"]
    assert all(call["method"] == "GET" for call in result["calls"])
    adopted = call(tmp_path, asset, scope="segment", tags=["人物走路"])
    assert adopted["ok"] is True
    assert adopted["asset"]["segments"][0]["user_tags"] == ["原片段", "人物走路"]


def test_tag_suggestions_do_not_resurface_observations_shadowed_by_correction(tmp_path, asset):
    asset["segments"][0]["understanding"] = {"status": "ready", "facts": [
        {"kind": "object", "label": "笔记本", "text": "笔记本电脑", "sample_times": [.3]}]}
    result = call(tmp_path, asset, scope="segment", tool="suggest_selected_material_tags")
    assert result["result"]["details"]["suggestions"] == []
    assert result["asset"] == result["original"]
