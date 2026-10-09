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
let reads=0;
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
      if (reads===2 && input.failure==='mismatch') reply.user_tags=['concurrent-change'];
      return reply;
    }
    if(input.failure==='write') throw new Error('真实写入失败（500）');
    if(path.endsWith('/tags')) asset.user_tags=body.user_tags;
    else {
      const segment=asset.segments.find(s=>path.endsWith('/segments/'+encodeURIComponent(s.id)));
      if(!segment) throw new Error('wrong route');
      // Existing PUT defaults erase these fields if a caller omits them.
      segment.description=body.description??''; segment.transcript=body.transcript??'';
      segment.review_status=body.review_status??'raw';
      if(body.usage_role!==undefined) segment.usage_role=body.usage_role;
      if(body.collection_ids!==undefined) segment.collection_ids=body.collection_ids;
      segment.user_tags=body.user_tags;
    }
    return structuredClone(asset);
  }
});
const tool=tools.get(input.tool||'update_selected_material_tags');
try {
 const result=await tool.execute('synthetic-call',input.params||{});
 console.log(JSON.stringify({ok:true,result,calls,asset,original,parameters:tool.parameters}));
} catch(error) { console.log(JSON.stringify({ok:false,error:error.message,calls,asset,original})); }
"""


@pytest.fixture
def asset():
    return {
        "id": "asset-a", "user_tags": ["原整条"], "file_name": "synthetic.mp4",
        "source_path": "/synthetic/private/not-for-model.mp4", "owner_user_id": "not-for-model",
        "segments": [
            {"id": "segment-1", "asset_id": "asset-a", "description": "办公电脑",
             "transcript": "合成文字", "review_status": "approved", "usage_role": "b_roll",
             "collection_ids": ["collection-1"], "system_tags": ["场景:办公", "物体:电脑"],
             "user_tags": ["原片段"], "start_seconds": 0, "end_seconds": 2},
            {"id": "segment-2", "asset_id": "asset-a", "description": "第二片段",
             "transcript": "不改", "review_status": "raw", "usage_role": "unassigned",
             "collection_ids": [], "system_tags": [], "user_tags": ["保留"],
             "start_seconds": 2, "end_seconds": 5},
        ],
    }


def call(tmp_path, asset, *, scope="asset", selection="bound", tool=None, action="add", tags=None, failure=None, **extra):
    selected = {"scope": scope, "assetId": "asset-a"}
    if scope == "segment":
        selected["segmentId"] = "segment-1"
    if selection != "bound":
        selected = selection
    data = {"asset": asset, "selection": selected, "params": {"action": action, "tags": tags if tags is not None else ["新增"], **extra}, "failure": failure}
    if tool:
        data["tool"] = tool
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
    assert out["asset"] == before  # Includes all unrelated segments, system tags and metadata.
    if scope == "segment":
        assert set(out["calls"][1]["body"]) == {"description", "transcript", "review_status", "usage_role", "collection_ids", "user_tags"}
    else:
        assert set(out["calls"][1]["body"]) == {"user_tags"}


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
