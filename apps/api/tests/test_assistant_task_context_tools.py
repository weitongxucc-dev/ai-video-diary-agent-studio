"""Execute the actual TypeScript tool in Node against a controlled loopback HTTP response."""
import json
import os
from pathlib import Path
import subprocess

import pytest


from tool_result_test_fixtures import TOOL_OUTCOME_JS

MODULE = Path(__file__).parents[1] / "agent_tools" / "diary_tools.ts"
RUNNER = TOOL_OUTCOME_JS + r"""
import {createServer} from 'node:http';
let raw='';for await(const part of process.stdin)raw+=part;
const input=JSON.parse(raw),calls=[],tools=new Map();
const server=createServer((req,res)=>{
  calls.push({url:req.url,method:req.method,token:req.headers['x-local-member-token']});
  res.setHeader('content-type','application/json');
  const execution=req.url.endsWith('/execution');
  res.statusCode=execution?input.executionStatus:200;
  res.end(JSON.stringify(execution?input.execution:req.url.endsWith('/assistant/script-context')?input.scriptContext:input.workspace));
});
await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
process.env.AI_VIDEO_DIARY_AGENT_API_BASE=`http://127.0.0.1:${server.address().port}`;
try {
  const {default:register}=await import(process.argv[1]);register({registerTool:tool=>tools.set(tool.name,tool)});
  const result=await tools.get('read_current_task').execute();
  console.log(JSON.stringify({...toolOutcome(result),calls}));
}catch(error){console.log(JSON.stringify({ok:false,error:error.message,calls}));}
finally{await new Promise(resolve=>server.close(resolve));}
"""


def run_tool(tmp_path, workspace, *, execution=None, execution_status=200, stage="topic", script_context=None):
    env = {key: os.environ[key] for key in ("PATH", "HOME", "TMPDIR") if key in os.environ}
    env.update(AI_VIDEO_DIARY_AGENT_TASK_ID="task-real", AI_VIDEO_DIARY_AGENT_STAGE=stage,
               AI_VIDEO_DIARY_AGENT_SELECTED_REFS="[]", AI_VIDEO_DIARY_AGENT_LOCAL_MEMBER_TOKEN="controlled-member",
               AI_VIDEO_DIARY_AGENT_TASK_VERSION=json.dumps({"timeline_version_id":None,"timeline_sequence":0}))
    completed = subprocess.run([
        os.environ.get("MATERIAL_TOOLS_TEST_NODE", "node"), "--experimental-strip-types", "--input-type=module",
        "-e", RUNNER, MODULE.as_uri(),
    ], input=json.dumps({"workspace":workspace, "scriptContext":script_context, "executionStatus":execution_status,
        "execution":execution if execution is not None else {"task_id":"task-real","job":None,
          "current_stage":None,"waiting_reason":None,"status_updated_at":None}}), text=True, capture_output=True, check=True,
        cwd=tmp_path, env=env, timeout=10)
    return json.loads(completed.stdout)


def task_workspace():
    return {
        "task":{"id":"task-real","name":"已有资料的任务","stage":"topic","status":"failed",
                "stage_status":"failed","stage_reason":"provider_timeout",
                "stage_impact":"文字服务超时，尚未生成选题。","stage_repair_action":"检查连接后重试选题。",
                "current_timeline_version_id":None,"timeline_sequence":0,"private_key":"secret-token"},
        "plan":{"goal":"按已有两项资料讲解","duration_target_seconds":30,"target_count":1,
                "language":"zh-CN","aspect_ratio":"9:16","template_id":"template-real","template_version":2,
                "selected_person":{"profile_id":"person-real","version":"person-v1","file_path":"/private/person.mp4"},
                "selected_voice":{"profile_id":"voice-real","version":"voice-v2","api_key":"secret-token"},
                "knowledge_references":[{"id":"knowledge-real","version":"1","title":"固定资料",
                    "authorization":"","ownership":"","body":"large-body"*10000,"file_reference":"/private/reference.pdf"}],
                "source_references":[{"id":"source-real","identity":"fixed-hash","title":"手工来源",
                    "license":"unknown","body":"large-body"*10000,"secret":"secret-token"}],
                "template_settings":{"source_path":"/private/material.mp4"},"private_key":"secret-token"},
        "montage":None,"subtitle_tracks":[],"narration":None,"raw_media_path":"/private/material.mp4",
    }


def test_real_node_task_read_separates_fixed_inputs_from_empty_editor_selection(tmp_path):
    output = run_tool(tmp_path, task_workspace())
    details = output["result"]["details"]
    assert details["selected_refs"] == [] and "独立于plan" in details["selected_refs_meaning"]
    assert details["plan"]["knowledge_references"] == [{"id":"knowledge-real","version":"1","title":"固定资料"}]
    assert details["plan"]["source_references"] == [{"id":"source-real","identity":"fixed-hash","title":"手工来源"}]
    assert details["plan"]["goal"] == "按已有两项资料讲解"
    assert details["plan"]["selected_voice"] == {"profile_id":"voice-real","version":"voice-v2"}
    assert details["status"] == details["stage_status"] == "failed"
    assert details["stage_impact"] == "文字服务超时，尚未生成选题。"
    assert details["stage_repair_action"] == "检查连接后重试选题。"
    assert output["calls"] == [
        {"url":"/api/creation-tasks/task-real","method":"GET","token":"controlled-member"},
        {"url":"/api/creation-tasks/task-real/execution","method":"GET","token":"controlled-member"},
    ]
    assert details["execution"]["job"] is None and "服务端本次未返回" in details["execution"]["meaning"]
    text = output["result"]["content"][0]["text"]
    assert details["invocation_input_id"] is None
    assert json.loads(text) == {key: value for key, value in details.items() if key != "invocation_input_id"}
    assert all(value not in text for value in ("/private/", "secret-token", "large-body", "authorization", "ownership"))


def test_real_node_task_reference_metadata_is_bounded_with_truthful_counts(tmp_path):
    workspace = task_workspace()
    workspace["plan"]["knowledge_references"] *= 60
    workspace["plan"]["goal"] = "目标" * 1500
    details = run_tool(tmp_path, workspace)["result"]["details"]
    assert len(details["plan"]["knowledge_references"]) == 50
    assert details["plan"]["knowledge_reference_count"] == 60
    assert details["plan"]["source_reference_count"] == 1
    assert details["plan"]["references_truncated"] is True
    assert len(details["plan"]["goal"]) == 1000


def confirmed_workspace():
    workspace = task_workspace()
    workspace["task"].update(stage="narration", stage_status="not_started", status="running",
                             confirmed_script_id="confirmed-script")
    sections = [{"id":f"section-{index}","title":f"第{index}段", "content":f"第{index}段实际配音正文。",
                 "visual_prompt":f"第{index}段实际画面。", "evidence_reference_ids":["knowledge:knowledge-real:1"],
                 "source_path":"/private/raw.mp4"} for index in (1,2,3)]
    script = {"id":"confirmed-script", "task_id":"task-real", "selection_status":"confirmed",
              "title":"实际已确认三段文案", "language":"zh-CN", "updated_at":"2026-10-10T04:26:40Z",
              "sections":sections, "sections_loaded":True, "sections_total_count":3, "private_key":"secret-token"}
    workspace["scripts"] = [{**script, "id":"newer-draft", "selection_status":"draft", "title":"不可冒充确认稿"}, script]
    return workspace


@pytest.mark.parametrize(("status", "waiting_reason"), [
    ("queued", "本机资源余量不足，正在等待满足制作条件。"),
    ("interrupted", "当前任务授权已变化，请核对权限。"),
], ids=["queued-memory-wait", "interrupted-authorization-block"])
def test_real_node_reads_confirmed_script_and_independent_execution_without_guessing_from_stage(tmp_path, status, waiting_reason):
    execution = {"task_id":"task-real", "job":{"id":"job-real","task_id":"task-real","status":status,
                 "progress":0,"private_key":"secret-token", "artifact_path":"/private/output.mp4"},
                 "current_stage":"narration", "waiting_reason":waiting_reason,
                 "status_updated_at":"2026-10-10T04:29:00Z", "stages":[], "private_path":"/private/runtime"}
    output = run_tool(tmp_path, confirmed_workspace(), execution=execution, stage="narration")
    assert output["ok"]
    details = output["result"]["details"]
    assert details["stage_status"] == "not_started"
    assert details["execution"]["status"] == status
    assert {key: details["execution"]["job"][key] for key in ("id", "status", "progress")} == {"id":"job-real","status":status,"progress":0}
    assert details["execution"]["job"]["task_id"] == "task-real"
    assert details["execution"]["waiting_reason"] == waiting_reason
    assert details["execution"]["updated_at"] == execution["status_updated_at"]
    script = details["confirmed_script"]
    assert script["id"] == details["confirmed_script_id"] == "confirmed-script"
    assert script["selection_status"] == "confirmed" and script["sections_loaded"] is False
    assert script["sections"] is None and script["read_tool"] == "read_script_sections"
    assert script["sections_total_count"] == 3
    assert len(output["calls"]) == 2 and all(call["method"] == "GET" for call in output["calls"])
    text = output["result"]["content"][0]["text"]
    assert "/private/" not in text and "secret-token" not in text and "不可冒充确认稿" not in text


@pytest.mark.parametrize("metadata_count", [None, 30])
def test_real_node_context_does_not_embed_preloaded_script_body(tmp_path, metadata_count):
    workspace = confirmed_workspace()
    sections = workspace["scripts"][1]["sections"]
    sections[0].update(content="正文"*2000, visual_prompt="画面"*400,
                       evidence_reference_ids=[f"knowledge:item-{index}:1" for index in range(20)])
    workspace["scripts"][1]["sections"] = sections * 10
    if metadata_count is None:
        workspace["scripts"][1].pop("sections_total_count")
    else:
        workspace["scripts"][1]["sections_total_count"] = metadata_count
    output = run_tool(tmp_path, workspace, stage="narration")
    script = output["result"]["details"]["confirmed_script"]
    assert script["sections_loaded"] is False and script["sections"] is None
    assert script["sections_total_count"] == metadata_count and script["read_tool"] == "read_script_sections"
    assert "正文" not in json.dumps(output["result"], ensure_ascii=False)
    assert "画面" not in json.dumps(script, ensure_ascii=False)


@pytest.mark.parametrize(("execution", "status"), [
    ({"detail":"后台安排读取失败"}, 503),
    ({"task_id":"other-task","job":None}, 200),
])
def test_real_node_execution_unreadable_never_reports_empty_or_not_queued(tmp_path, execution, status):
    output = run_tool(tmp_path, confirmed_workspace(), execution=execution, execution_status=status, stage="narration")
    assert not output["ok"] and output["error"]
    assert output["result"]["isError"] is True
    assert set(output["result"]["details"]) == {"failure", "invocation_input_id"}
    assert json.loads(output["result"]["content"][0]["text"]) == {"failure": output["result"]["details"]["failure"]}
    assert len(output["calls"]) == 2


def test_real_node_missing_confirmed_identity_is_unreadable_not_an_empty_script(tmp_path):
    workspace = confirmed_workspace()
    workspace["task"]["confirmed_script_id"] = "missing-script"
    output = run_tool(tmp_path, workspace, stage="narration")
    assert not output["ok"] and "已确认文案不可读" in output["error"]
    assert output["result"]["isError"] is True
    assert set(output["result"]["details"]) == {"failure", "invocation_input_id"}
    assert json.loads(output["result"]["content"][0]["text"]) == {"failure": output["result"]["details"]["failure"]}


def test_actual_api_narration_without_segments_and_montage_audio_are_not_omitted(tmp_path):
    workspace=task_workspace()
    workspace["narration"]={"audio_reference":"narration://artifact/pi-qa-audio","duration_seconds":5,
                            "sample_rate":24000,"language":"zh","segments":[]}
    workspace["montage"]={"clips":[],"cues":[],"narration_reference":"narration://artifact/pi-qa-audio",
        "audio_timeline":{"format_version":1,"tracks":[{"id":"voice-track","name":"本人声音","kind":"voice",
        "clips":[{"id":"voice-clip","source_ref":"narration://artifact/pi-qa-audio","source_start_ms":0,
                   "source_end_ms":5000,"timeline_start_ms":0,"playback_rate":1,"gain_db":0}]}]}}
    seen=run_tool(tmp_path,workspace)["result"]["details"]
    assert seen["narration_segment_count"]==0
    assert seen["narration"]["has_audio_reference"] and seen["narration"]["duration_seconds"]==5
    assert seen["narration"]["audio_reference"]==seen["narration"]["montage_narration_reference"]
    assert seen["audio_track_count"]==1 and seen["audio_tracks"][0]["clips"][0]["duration_ms"]==5000


def test_actual_relative_artifact_refs_preserve_distinct_current_and_registered_audio(tmp_path):
    workspace=task_workspace()
    workspace["narration"]={"audio_reference":"artifacts/task-real/job-cached/audio.wav","duration_seconds":5,
                            "sample_rate":24000,"model_id":"synthetic-pcm","voice_profile_id":"synthetic-only",
                            "source_kind":"generated","segments":[]}
    workspace["montage"]={"clips":[],"cues":[],"narration_reference":"artifacts/task-real/job-current/edited.wav",
                           "audio_timeline":{"format_version":1,"tracks":[]}}
    facts=run_tool(tmp_path,workspace)["result"]["details"]["narration"]
    assert facts["audio_reference"]==workspace["narration"]["audio_reference"]
    assert facts["montage_narration_reference"]==workspace["montage"]["narration_reference"]
    assert not facts["montage_uses_registered_narration"] and "status" not in facts
    assert facts["duration_seconds"]==5 and facts["model_id"]=="synthetic-pcm"
    for physical in ("/private/audio.wav","file:///private/audio.wav"):
        workspace["narration"]["audio_reference"]=physical
        assert run_tool(tmp_path,workspace)["result"]["details"]["narration"]["audio_reference"] is None


@pytest.mark.parametrize('complete',[False,True])
def test_script_context_preserves_server_completeness_without_embedding_body(tmp_path,complete):
    workspace=task_workspace()
    workspace['task']['stage']='script'
    context={'task_id':'task-real','draft':{'id':'draft','updated_at':'v1','sections_total_count':20,
        'sections':[{'id':'section','content':'正文不可嵌入当前上下文'}], 'draft_complete':complete}}
    output=run_tool(tmp_path,workspace,stage='script',script_context=context)
    draft=output['result']['details']['script_context']['draft']
    assert draft['draft_complete'] is complete
    assert draft['sections'] is None and draft['sections_loaded'] is False
    assert draft['sections_total_count']==20 and draft['read_tool']=='read_script_sections'
    assert '正文不可嵌入' not in json.dumps(output['result'],ensure_ascii=False)
