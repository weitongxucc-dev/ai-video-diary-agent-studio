"""Input-goal revisions over real SQLite; model/production verification are seams."""
from uuid import uuid4
import json
from types import SimpleNamespace
import pytest
from app.conversation_repository import ConversationRepository
from app.assistant_goal_continuation import GoalContinuationService, GoalResultProjection


def started(repo):
    chat=repo.create("w","m","task","t")
    message="修改字幕后生成视频"
    saved=repo.save_draft(chat["id"],"w","m",message,0)
    context={"kind":"task","task_id":"t","stage":"montage","target_snapshot":{"timeline_version_id":"v1","timeline_sequence":1}}
    turn,_=repo.create_turn_idempotent(chat["id"],"w","m",message,str(uuid4()),saved["draft_version"],context)
    repo.record_task_execution(turn["id"],"w","m","t",{"task_id":"t","job":{"id":"original-job","status":"queued"}})
    return chat,turn


def consume(repo,chat,target,*,message,mode="steer",version="v1",sequence=1):
    current=repo.get(chat["id"],"w","m")
    draft=repo.save_draft(chat["id"],"w","m",message,current["draft_version"])
    input_id=str(uuid4())
    context={"kind":"task","task_id":"t","stage":"montage","selected_reference":None,
        "target_snapshot":{"timeline_version_id":version,"timeline_sequence":sequence}}
    repo.enqueue_input(chat["id"],"w","m",target_turn_id=target["id"],client_request_id=input_id,
        message=message,mode=mode,expected_draft_version=draft["draft_version"],expected_reference_version=0,request_context=context)
    repo.transition_input(chat["id"],"w","m",input_id,"consumed")
    return input_id


def result(repo,turn,input_id,job):
    response={"task_id":"t","job":{"id":job,"status":"queued"}}
    repo.record_task_execution(turn["id"],"w","m","t",response,input_id=input_id)
    repo.transition_input(turn["conversation_id"],"w","m",input_id,"completed")
    repo.finish_turn(turn["id"],"completed",result={"kind":"task_turn","response":{"execution_status":response}})


def emit(repo,turn,input_id,event_id):
    event={"type":"business_result","event_id":event_id,"task_id":"t","phase":"execution","status":"succeeded"}
    if input_id:event["input_id"]=input_id
    assert repo.project_business_result(turn["id"],"w","m",event,response_patch={},message="实际结果")
    return event


def test_consumed_steering_creates_revision_and_only_its_event_resumes(tmp_path):
    repo=ConversationRepository(tmp_path/"revisions.sqlite")
    chat,turn=started(repo)
    input_id=consume(repo,chat,turn,message="字幕用蓝色，仍按原要求生成")
    assert repo.get_task_goal(turn["id"])["status"] == "invalidated"
    result(repo,turn,input_id,"input-job")
    goal=repo.get_task_goal(turn["id"])
    assert goal["revision"] == 1 and goal["source_input_id"] == input_id
    assert "修改字幕后生成视频" in goal["original_instruction"] and "字幕用蓝色" in goal["original_instruction"]
    emit(repo,turn,None,"old-result")
    assert repo.goal_event_candidates() == []
    emit(repo,turn,input_id,"input-result")
    calls=[]
    def run(claim,fresh):
        calls.append(claim)
        source=repo.task_turn_source(claim["turn_id"],"w","m","t")
        assert source["input_id"] == input_id and source["input_turn_id"] == turn["id"]
        assert source["context"]["target_snapshot"]["timeline_version_id"] == "v2"
        assert source["raw_command"] == goal["original_instruction"]
        repo.finish_turn(claim["turn_id"],"completed","已回读完成",result={})
    service=GoalContinuationService(repo,verify_current_goal=lambda *_:{"status":"ready","task_version":{"timeline_version_id":"v2","timeline_sequence":2}},run_goal_turn=run)
    assert service.reconcile() == 1 and len(calls) == 1
    assert service.reconcile() == 0
    assert [item["user_message"] for item in repo.list_turns(chat["id"])] == [turn["user_message"],""]
    assert repo.request_context(calls[0]["turn_id"],chat["id"],"w","m")["goal_revision"] == 1


def test_new_followup_during_child_invalidates_old_claim_and_does_not_inherit_old_authority(tmp_path):
    repo=ConversationRepository(tmp_path/"successor.sqlite")
    chat,turn=started(repo)
    first=consume(repo,chat,turn,message="保持蓝色")
    result(repo,turn,first,"first-job")
    emit(repo,turn,first,"first-result")
    stale_candidate=repo.goal_event_candidates()[0]
    claims=[]
    second=[]
    def run(claim,fresh):
        claims.append(claim)
        child=repo.get_turn(claim["turn_id"])
        input_id=consume(repo,chat,child,message="后续只检查已有结果，不再生成",mode="follow_up",version="v2",sequence=2)
        second.append(input_id)
        result(repo,child,input_id,"second-job")
    service=GoalContinuationService(repo,verify_current_goal=lambda *_:{"status":"ready","task_version":{"timeline_version_id":"v2","timeline_sequence":2}},run_goal_turn=run)
    assert service.reconcile() == 1
    newer=repo.get_task_goal(turn["id"])
    assert newer["revision"] == 2 and newer["source_input_id"] == second[0]
    assert newer["original_instruction"] == "后续只检查已有结果，不再生成"
    assert newer["status"] == "waiting_result"
    assert repo.claim_goal_event(stale_candidate,{"timeline_version_id":"v3","timeline_sequence":3},max_rounds=6) is None
    assert repo.settle_goal_round(claims[0]) is None
    assert ConversationRepository(repo.database_path).get_task_goal(turn["id"])["revision"] == 2


def test_late_old_execution_is_history_and_cannot_invalidate_or_replace_new_input(tmp_path):
    repo=ConversationRepository(tmp_path/"late.sqlite")
    chat,turn=started(repo)
    first=consume(repo,chat,turn,message="先调蓝色")
    repo.record_task_execution(turn["id"],"w","m","t",{"job":{"id":"first-job","status":"queued"}},input_id=first)
    repo.transition_input(chat["id"],"w","m",first,"completed")
    second=consume(repo,chat,turn,message="改为绿色",version="v2",sequence=2)
    result(repo,turn,second,"second-job")
    goal=repo.get_task_goal(turn["id"])
    assert goal["revision"] == 2 and goal["source_input_id"] == second
    repo.record_task_execution(turn["id"],"w","m","t",{"job":{"id":"first-job","status":"succeeded"}},
        current_source=False,input_id=first)
    old={"type":"business_result","event_id":"old-late","task_id":"t","status":"stale","input_id":first}
    assert repo.project_business_result(turn["id"],"w","m",old,
        response_patch={"execution_status":{"job":{"id":"first-job","status":"succeeded"}}},message="旧结果")
    saved=repo.get_turn(turn["id"])
    assert repo.get_task_goal(turn["id"]) == goal
    assert saved["result"]["response"]["execution_status"]["job"]["id"] == "second-job"
    assert old in saved["events"] and "旧结果" not in saved["assistant_text"]
    assert repo.goal_event_candidates() == []
    assert repo.task_result_source(turn["id"],"w","m","t")["raw_command"] == turn["user_message"]
    original=repo.task_result_source(turn["id"],"w","m","t",input_id=first,input_turn_id=turn["id"])
    assert original["raw_command"] == "先调蓝色" and original["context"]["target_snapshot"]["timeline_version_id"] == "v1"
    with pytest.raises(ValueError):repo.task_result_source(turn["id"],"w","other","t",input_id=first,input_turn_id=turn["id"])
    with pytest.raises(ValueError):repo.task_result_source(turn["id"],"w","m","other",input_id=first,input_turn_id=turn["id"])
    with pytest.raises(ValueError):repo.task_result_source(turn["id"],"w","m","t",input_id=first,input_turn_id="foreign")


def test_old_preview_source_and_unknown_locator_do_not_block_new_input(tmp_path):
    repo=ConversationRepository(tmp_path/"pending.sqlite")
    chat,turn=started(repo)
    input_id=consume(repo,chat,turn,message="现在处理绿色版本")
    result(repo,turn,input_id,"new-job")
    goal=repo.get_task_goal(turn["id"])
    old={"assistant_turn_id":turn["id"]}
    current={**old,"input_id":input_id,"input_turn_id":turn["id"]}
    pending=[{"id":"old-preview","constraints_json":json.dumps({"request_source":old})}]
    completed=[{"id":"old-source","payload_json":json.dumps({"arguments":{"_typed_source":old}})}]
    production=SimpleNamespace(goal_pending_preview_rows=lambda *_:(pending,[]),
        goal_completed_source_rows=lambda *_:completed)
    owner=GoalResultProjection(production,repo)
    assert owner.pending_preview(goal,"v1") is None
    assert owner.completed_sources(goal,None,None,None) == []
    assert owner.unknown_request_for_goal(goal,{"payload_json":json.dumps({"source":old,"locator":{"request_id":"old"}})}) is None
    pending.append({"id":"current-preview","constraints_json":json.dumps({"request_source":current})})
    assert owner.pending_preview(goal,"v1")["pending_id"] == "current-preview"
    assert owner.unknown_request_for_goal(goal,{"payload_json":json.dumps({"source":current,"locator":{"request_id":"new"}})}) == {"request_id":"new"}


def test_stale_verification_cannot_overwrite_new_revision_checkpoint(tmp_path):
    repo=ConversationRepository(tmp_path/"race.sqlite")
    chat,turn=started(repo)
    first=consume(repo,chat,turn,message="先处理蓝色")
    result(repo,turn,first,"first-job")
    emit(repo,turn,first,"first-result")
    calls=[]
    def verify(*_):
        # A real concurrent request can consume input while host verification runs.
        with repo._connect() as conn:conn.execute("UPDATE assistant_conversation_turns SET status='running' WHERE id=?",(turn["id"],))
        second=consume(repo,chat,turn,message="后续只处理绿色",mode="follow_up")
        result(repo,turn,second,"second-job")
        return {"status":"invalidated","reason":"old_verification_failed"}
    service=GoalContinuationService(repo,verify_current_goal=verify,run_goal_turn=lambda *_:calls.append(True))
    assert service.reconcile() == 0 and calls == []
    goal=repo.get_task_goal(turn["id"])
    assert goal["revision"] == 2 and goal["status"] == "waiting_result"
    assert goal["original_instruction"] == "后续只处理绿色"


@pytest.mark.parametrize("restart",[False,True])
def test_interrupted_latest_input_never_restores_previous_execution_response(tmp_path,restart):
    repo=ConversationRepository(tmp_path/"interrupted.sqlite")
    chat,turn=started(repo)
    first=consume(repo,chat,turn,message="先等旧作业")
    repo.record_task_execution(turn["id"],"w","m","t",{"job":{"id":"old-job","status":"queued"}},input_id=first)
    repo.transition_input(chat["id"],"w","m",first,"completed")
    second=consume(repo,chat,turn,message="后续只处理新要求",mode="follow_up")
    repo.transition_input(chat["id"],"w","m",second,"interrupted")
    with pytest.raises(ValueError,match="最新已消费输入已中断"):
        repo.task_turn_source(turn["id"],"w","m","t")
    if restart:
        assert ConversationRepository(repo.database_path).fail_interrupted_turns() == 1
    else:
        repo.finish_turn(turn["id"],"failed",result={"reason":"api_shutdown"})
    saved=repo.get_turn(turn["id"])
    assert saved["result"].get("response",{}).get("execution_status") is None
    assert {"kind":"execution_status","id":"old-job","input_id":first} in saved["result"]["goal_business_actions"]
    old={"type":"business_result","event_id":"late-first","task_id":"t","status":"succeeded","input_id":first}
    repo.project_business_result(turn["id"],"w","m",old,response_patch={"execution_status":{"job":{"id":"old-job"}}},message="旧作业成功")
    assert repo.get_turn(turn["id"])["result"].get("response",{}).get("execution_status") is None
    assert repo.get_task_goal(turn["id"])["status"] == "invalidated"
    assert repo.register_task_goal(turn["id"],running_execution=True) is None
