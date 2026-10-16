import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {typedEditSchema, isHostEditField} from './schemas.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';

export function registerDirectEdit(pi: ToolRegistrar, {request, taskId, selectedTaskVersion, isTaskRead,
  readActionSource, typedRequestId, getEditCapabilities, getDeclaredCapabilities, hasCreationAction,
  hasEditControlWrite, markDirectEditAttempt, verifyDirectEdit}: {
  request:BusinessRequest; taskId:string|undefined; selectedTaskVersion:TaskVersion|null;
  isTaskRead:()=>boolean; readActionSource:()=>ActionSource; typedRequestId:(turn:string,call:string)=>string;
  getEditCapabilities:()=>any; getDeclaredCapabilities:()=>any;
  hasCreationAction:()=>boolean; hasEditControlWrite:()=>boolean; markDirectEditAttempt:()=>void; verifyDirectEdit:()=>void;
}) {
  const declaration=getDeclaredCapabilities();
  if (!declaration?.direct_apply?.operations?.length) return;
  const parameters=typedEditSchema({...declaration,
    supported_operations:declaration.direct_apply.operations.map((item:any)=>item.operation_type)});
  pi.registerTool({name:"apply_typed_edit",label:"执行明确修改",
    description:"用户已明确要求的可撤销修改直接保存到当前时间线。先读真实任务、目标、字段与单位；仅使用 API direct_apply 声明的操作和字段。不确定对象、数值或扩大范围时先询问或准备提案。使用同一业务仓储/CAS/操作回执，成功后可整项撤销；不会自动生成成片。结果未知只能按原 request_id 回读，禁止新调用重放。每个已消费用户输入最多一次直接编辑。",
    parameters,
    execute:async (callId:string,params:Record<string,unknown>)=>{
      if (!isTaskRead() || !selectedTaskVersion) throw new Error("请先读取本次输入的可信任务与版本");
      if (hasCreationAction() || hasEditControlWrite()) throw new Error("本次输入已有执行或编辑动作，请先查询其真实结果");
      const capability=getEditCapabilities()?.direct_apply;
      const operation=capability?.operations.find((item:any)=>item.operation_type===params.operation_type);
      const endpoint=`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/exact`;
      if (!operation?.available || capability.endpoint!==endpoint) throw new Error(operation?.reason || "API 未声明此操作可直接执行，请读取能力或准备提案");
      if (Object.keys(params).some(key=>isHostEditField(key) || !Object.hasOwn(parameters.properties,key)
          || (key!=="operation_type" && !operation.parameter_fields.includes(key))))
        throw new Error("直接编辑参数包含未声明操作字段或可信宿主字段，未提交");
      if (operation.required_fields.some((key:string)=>params[key]===undefined || params[key]===null))
        throw new Error("直接编辑缺少 API 声明的必填参数，未提交");
      if (params.operation_type === "EditAudioTimeline"
          && !["update","track"].includes((params.audio_edit as {action?:string})?.action ?? ""))
        throw new Error("直接音频编辑仅支持修改现有片段或音轨；其他操作需准备提案");
      const source=readActionSource();
      if (!source.turnId || !source.userInstruction || !callId) throw new Error("本次输入的明确要求或调用身份缺失，未提交");
      const requestId=typedRequestId(source.turnId,callId);
      const query=`/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${requestId}?phase=apply`;
      markDirectEditAttempt();
      let result:any;
      try {
        result=await request(endpoint,{method:"POST",body:JSON.stringify({...params,
          assistant_turn_id:source.turnId,request_id:requestId,
          expected_timeline_version_id:selectedTaskVersion.timeline_version_id,
          expected_timeline_sequence:selectedTaskVersion.timeline_sequence})});
      } catch(error) {
        const details={kind:"edit_request_status",phase:"apply",task_id:taskId,request_id:requestId,query_url:query,
          assistant_turn_id:source.turnId,tool_call_id:callId,
          status:error instanceof RequestNotSubmitted ? "not_submitted"
            : error instanceof BusinessRequestError && error.httpStatus<500 ? "rejected" : "result_unknown",
          failure_reason:boundedText(error instanceof Error ? error.message : String(error),500)};
        return {isError:true,content:[{type:"text",text:JSON.stringify(details)}],details};
      }
      let continuationBlocker:string|undefined;
      try {
        // Only the acknowledged original journal may advance this invocation's
        // CAS fence. A lost/rejected reply keeps the direct attempt locked.
        const receipt=await request(query);
        const operation=result.operation;
        if (receipt.status!=="recorded" || receipt.result_status_code!==200
            || receipt.request_id!==requestId || receipt.phase!=="apply"
            || receipt.submitted_input?.assistant_turn_id!==source.turnId
            || receipt.submitted_input?.expected_timeline_version_id!==selectedTaskVersion.timeline_version_id
            || receipt.submitted_input?.expected_timeline_sequence!==selectedTaskVersion.timeline_sequence
            || operation?.task_id!==taskId || operation.status!=="applied"
            || operation.base_version_id!==selectedTaskVersion.timeline_version_id || !operation.result_version_id
            || receipt.result?.operation?.id!==operation.id
            || receipt.result.operation.result_version_id!==operation.result_version_id)
          throw new Error("原编辑回执无法证明新版本，未继续制作；请查询原请求");
        const current=await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}`);
        if (current.task?.id!==taskId || current.task.current_timeline_version_id!==operation.result_version_id
            || current.task.timeline_sequence!==selectedTaskVersion.timeline_sequence+1)
          throw new Error("当前版本不是本次已提交编辑的直接结果，未继续制作");
        selectedTaskVersion.timeline_version_id=operation.result_version_id;
        selectedTaskVersion.timeline_sequence=current.task.timeline_sequence;
        verifyDirectEdit();
      } catch (error) {
        continuationBlocker=boundedText(error instanceof Error ? error.message : String(error),500).text;
      }
      // The host re-reads the original journal and validates operation/target/version.
      const details={kind:"edit_applied",phase:"apply",task_id:taskId,request_id:requestId,query_url:query,
        assistant_turn_id:source.turnId,tool_call_id:callId,result, ...(continuationBlocker ? {continuation_blocker:continuationBlocker} : {})};
      return {content:[{type:"text",text:JSON.stringify(details)}],details};
    },
  });
}
