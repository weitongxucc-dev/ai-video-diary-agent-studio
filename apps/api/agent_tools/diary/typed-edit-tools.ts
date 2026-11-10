import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {typedEditSchema, isHostEditField} from './schemas.ts';
import {BusinessRequestError, BusinessResultUnknown, RequestNotSubmitted, safeBusinessFailure} from './transport.ts';
import {boundedText} from '../task_facts.ts';
import {createHash} from 'node:crypto';

export function createTypedEditTools({request, taskId, selectedTaskVersion, isTaskRead, readActionSource, typedRequestId, getEditCapabilities, getDeclaredCapabilities = getEditCapabilities, hasCreationAction, hasEditControlWrite, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; isTaskRead: () => boolean; readActionSource: () => ActionSource; typedRequestId: (turn: string, call: string) => string; getEditCapabilities: () => any; getDeclaredCapabilities?: () => any; hasCreationAction: () => boolean; hasEditControlWrite: () => boolean; markPreview: () => void;
}) {
  let typedPreviewBlocked = false;
  const typedInputs = new Set<string>();
  function registerNarrationRevision(pi: ToolRegistrar) {
    const contexts = new Map<string, any>();
    const submitted = new Set<string>();
    const sectionAttempts = new Map<string, string>();
    const originals = new Map<string, any>();
    const root = `/api/creation-tasks/${encodeURIComponent(taskId!)}`;
    const outcome = (action: 'submit' | 'query' | 'resume', requestId: string, revision: any, error?: unknown) => {
      const original = error instanceof BusinessRequestError || error instanceof BusinessResultUnknown || error instanceof RequestNotSubmitted
        ? error.businessFailure : undefined;
      const failure = error ? {...safeBusinessFailure({...original,
        reason: original?.reason ?? (error instanceof Error ? error.message : String(error))}),
        submission_effect: original?.submission_effect ?? (error instanceof RequestNotSubmitted ? 'not_submitted'
          : error instanceof BusinessRequestError ? 'rejected' : 'unknown')} : undefined;
      const details = {kind: 'segment_revision', action, request_id: requestId, revision,
        ...(failure ? {failure} : {})};
      return {isError: Boolean(error), content: [{type: 'text' as const, text: JSON.stringify(details)}], details};
    };
    pi.registerTool({
      name: 'record_input_understanding', label: '记录本次要求',
      description: '阅读当前对象后记录用户是在查询、澄清、试稿、正式修改、采用还是提出独立目标。这里只记录理解，不声明业务生效或已完成；试稿和查询不改变当前采用版本。',
      parameters: {type:'object',properties:{kind:{type:'string',enum:['query','clarification','candidate','effective_modification','adoption','independent_goal']},
        summary:{type:'string',minLength:1},target_refs:{type:'array',items:{type:'string'}}},required:['kind','summary','target_refs'],additionalProperties:false},
      execute: async (_id:string, params:any) => {
        const source=readActionSource();
        if (!source.turnId) throw new Error('当前原输入身份不可用');
        const result=await request(`${root}/assistant/input-understanding`,{method:'POST',body:JSON.stringify({
          ...params,assistant_turn_id:source.turnId,input_id:source.inputId ?? source.turnId})});
        return {content:[{type:'text',text:JSON.stringify(result)}],details:{kind:'input_understanding',understanding:result}};
      },
    });
    pi.registerTool({
      name:'read_narration_segment_context',label:'读取段落全文',
      description:'按真实ScriptSection.id读取本段和相邻段完整正文、脚本与时间线版本、来源依据和锁定规则。先用read_narration_segments找到用户明确指定的段；不以输入框默认选择覆盖用户“第1段”等指向。观察只以已有明确回执为依据。',
      parameters:{type:'object',properties:{segment_id:{type:'string',minLength:1}},required:['segment_id'],additionalProperties:false},
      execute:async (_id:string,params:{segment_id:string}) => {
        if (!isTaskRead()) throw new Error('请先读取当前任务及实际段落身份');
        const value=await request(`${root}/segments/${encodeURIComponent(params.segment_id)}/context`);
        if (value.task_id!==taskId || value.section_id!==params.segment_id || typeof value.text!=='string'
            || typeof value.segment_version!=='string' || !value.script_id || value.text_truncated===true) throw new Error('段落全文或版本未完整返回，不能据此改写');
        contexts.set(params.segment_id,value);
        return {content:[{type:'text',text:JSON.stringify(value)}],details:{kind:'narration_segment_context',context:value}};
      },
    });
    pi.registerTool({name:'read_narration_segment_sources',label:'读取本段固定依据',
      description:'先读本段上下文，再按页读取其原来源绑定的正文窗口。matched才有正文；changed/missing/unavailable/unknown分别保留，不用同ID最新内容追认旧依据。window_digest只标识当前文字窗口，不是原来源版本。',
      parameters:{type:'object',properties:{segment_id:{type:'string',minLength:1},offset:{type:'integer',minimum:0},
        limit:{type:'integer',minimum:1,maximum:24},text_offset:{type:'integer',minimum:0},max_chars:{type:'integer',minimum:1,maximum:8000}},
        required:['segment_id'],additionalProperties:false},
      execute:async (_id:string,params:{segment_id:string;offset?:number;limit?:number;text_offset?:number;max_chars?:number})=>{
        const context=contexts.get(params.segment_id);
        if (!context) throw new Error('先读取本段完整正文、版本和原来源绑定');
        const query=new URLSearchParams({offset:String(params.offset??0),limit:String(params.limit??8),
          text_offset:String(params.text_offset??0),max_chars:String(params.max_chars??3000)});
        const value=await request(`${root}/segments/${encodeURIComponent(params.segment_id)}/sources?${query}`);
        if (value.task_id!==taskId || value.section_id!==params.segment_id || value.script_id!==context.script_id
            || value.segment_version!==context.segment_version || !Array.isArray(value.items))
          throw new Error('本段原读取依据已变化，不能把新来源绑定为原证据');
        const keys=['reference_id','kind','object_id','version_or_identity','content_digest','binding_status'];
        const items=value.items.map((item:any)=>{
          const original=context.source_bindings?.find((binding:any)=>binding.reference_id===item.binding?.reference_id);
          if (original?.binding_status==='fixed' && keys.every(key=>original[key]===item.binding?.[key])) return item;
          const {text,title,window_digest,content_digest,...metadata}=item;
          return {...metadata,status:original?.binding_status==='fixed'?'changed':'unknown',
            reason_code:original?.binding_status==='fixed'?'fixed_source_binding_changed':'original_source_binding_unavailable'};
        });
        const details={...value,items};
        return {content:[{type:'text',text:JSON.stringify(details)}],details};
      }});
    const rule={type:'object',properties:{id:{type:'string',minLength:1},kind:{type:'string',enum:['exact_text','number','qualified_claim']},
      source_text:{type:'string',minLength:1},required_text:{type:'string',minLength:1},source_reference:{type:'string',minLength:1}},
      required:['id','kind','source_text','required_text','source_reference'],additionalProperties:false};
    const finding={type:'object',properties:{rule_id:{type:'string',minLength:1},before:{type:'string',minLength:1},after:{type:'string',minLength:1},
      assessment:{type:'string',enum:['preserved','uncertain','conflict']},reason:{type:'string',minLength:1}},
      required:['rule_id','before','after','assessment','reason'],additionalProperties:false};
    pi.registerTool({
      name:'revise_narration_segment',label:'修改本段文案',
      description:'基于刚读到的完整段落形成具体改写，并逐条对照卖点、数字、适用条件和“不超过/最高可达”等限定。紧凑通常需删冗余、调整信息顺序；不得把模糊要求固定映射到倍速。代码检查与Pi语义对照分别记录；存疑或冲突先说明，不能宣称确定保真。遵守定稿锁定和原授权。工具保存Pi复核，再沿原身份提交真实修改；candidate仅试稿，apply按已授权要求采用，不普遍追加人工确认。失败先查询原request_id。',
      parameters:{type:'object',properties:{segment_id:{type:'string',minLength:1},replacement_text:{type:'string',minLength:1},
        protected_rules:{type:'array',items:rule},conclusion:{type:'string',enum:['preserved','uncertain','conflict']},
        findings:{type:'array',minItems:1,items:finding},adoption_mode:{type:'string',enum:['apply','candidate']}},
        required:['segment_id','replacement_text','protected_rules','conclusion','findings','adoption_mode'],additionalProperties:false},
      execute:async (id:string,params:any) => {
        const context=contexts.get(params.segment_id), source=readActionSource();
        if (!source.turnId || !context || !id) throw new Error('须先读取本段完整上下文并绑定原输入');
        if (hasCreationAction() || hasEditControlWrite()) throw new Error('本轮已有其他执行或历史控制动作，请核对结果');
        if (params.findings.some((f:any)=>!context.text.includes(f.before) || !params.replacement_text.includes(f.after)))
          throw new Error('语义对照引用必须来自实际完整原文与本次改写');
        if (params.protected_rules.some((r:any)=>!params.findings.some((f:any)=>f.rule_id===r.id)))
          throw new Error('每项受保护事实与限定都须有语义对照依据');
        const requestId=typedRequestId(source.turnId,id);
        const attemptKey=JSON.stringify([source.inputId ?? source.turnId,params.segment_id]);
        if (sectionAttempts.has(attemptKey)) throw new Error(`本段修改已尝试提交；先查询原请求 ${sectionAttempts.get(attemptKey)}，不换新身份重复提交`);
        if (submitted.has(requestId)) throw new Error('原修改已尝试提交，请按原请求身份回读结果');
        submitted.add(requestId);
        sectionAttempts.set(attemptKey,requestId);
        const digest=(value:string)=>createHash('sha256').update(value,'utf8').digest('hex');
        const review={request_id:requestId,section_id:params.segment_id,script_id:context.script_id,expected_segment_version:context.segment_version,
          dependency_digest:context.dependency_digest,
          base_version_id:context.base_version_id,source_text_sha256:digest(context.text),
          replacement_text_sha256:digest(params.replacement_text),protected_rules:params.protected_rules,
          conclusion:params.conclusion,findings:params.findings,authorized_locked_section_ids:[]};
        try {
        const receipt=await request(`${root}/assistant/narration-semantic-review`,{method:'POST',body:JSON.stringify({
          ...review,assistant_turn_id:source.turnId,input_id:source.inputId ?? source.turnId})});
        if (!receipt.semantic_review_ref) throw new Error('语义复核未返回持久引用，尚未提交改稿');
        const payload={request_id:requestId,input_id:source.inputId ?? source.turnId,assistant_turn_id:source.turnId,
          section_id:params.segment_id,script_id:context.script_id,expected_segment_version:context.segment_version,
          dependency_digest:context.dependency_digest,
          base_version_id:context.base_version_id,source_text_sha256:review.source_text_sha256,
          replacement_text_sha256:review.replacement_text_sha256,replacement_text:params.replacement_text,
          protected_rules:params.protected_rules,semantic_review_ref:receipt.semantic_review_ref,adoption_mode:params.adoption_mode};
          const result=await request(`${root}/segments/${encodeURIComponent(params.segment_id)}/revision`,{method:'POST',body:JSON.stringify(payload)});
          return outcome('submit',requestId,result);
        } catch (error) {
          if (error instanceof RequestNotSubmitted && sectionAttempts.get(attemptKey)===requestId) sectionAttempts.delete(attemptKey);
          return outcome('submit',requestId,null,error);
        }
      },
    });
    pi.registerTool({
      name:'read_narration_revision',label:'查看本段修改',
      description:'沿原request_id读取真实修改、排队、检查、采用与有声产物回执；未知结果不重发。',
      parameters:{type:'object',properties:{request_id:{type:'string',minLength:1}},required:['request_id'],additionalProperties:false},
      execute:async (_id:string,params:{request_id:string}) => {
        const revision=await request(`${root}/segment-requests/${encodeURIComponent(params.request_id)}`);
        if (revision.task_id!==taskId || revision.request_id!==params.request_id) throw new Error('原修改回执身份不一致');
        originals.set(params.request_id,revision);
        return outcome('query',params.request_id,revision);
      },
    });
    pi.registerTool({name:'resume_narration_revision',label:'恢复原段修改',
      description:'先回读原段修改请求，仅当业务回执recovery.action为resume_original时沿同一request/job和累计预算恢复；未知先查询，不换ID重做，不把历史可播放视为当前采用。',
      parameters:{type:'object',properties:{request_id:{type:'string',minLength:1}},required:['request_id'],additionalProperties:false},
      execute:async (_id:string,params:{request_id:string})=>{
        const source=readActionSource();
        if (!source.turnId) throw new Error('当前恢复要求的可信回合不可用');
        if (!originals.has(params.request_id)) throw new Error('先回读原段修改请求及可用恢复动作');
        const current=await request(`${root}/segment-requests/${encodeURIComponent(params.request_id)}`);
        if (current.task_id!==taskId || current.request_id!==params.request_id || current.recovery?.action!=='resume_original')
          throw new Error('原段修改当前不可恢复，请按原请求回执处理');
        try {
          const revision=await request(`${root}/segment-requests/${encodeURIComponent(params.request_id)}/resume`,{
            method:'POST',body:JSON.stringify({assistant_turn_id:source.turnId})});
          if (revision.task_id!==taskId || revision.request_id!==params.request_id || revision.job_id!==current.job_id)
            throw new Error('恢复回执未保留原请求和作业，请先查询原请求');
          return outcome('resume',params.request_id,revision);
        } catch(error) {return outcome('resume',params.request_id,null,error);}
      }});
  }
  function registerRequestReader(pi: ToolRegistrar) {
    pi.registerTool({
      name: "read_typed_edit_request", label: "回读已提交编辑请求",
      description: "用原请求身份查询真实结果；只读，不重POST，不换绑当前版本或从对话推测成功。execution_readiness是该次保存时的授权检查：null表示无记录，not_ready不撤销已保存修改，authorization_ready不表示设备已准入或产物已生成。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}, phase:{type:"string",enum:["preview","apply"]}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string; phase?:"preview"|"apply"}) => {
        const phase=params.phase ?? "preview";
        const query = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${encodeURIComponent(params.request_id)}?phase=${phase}`;
        const receipt = await request(query);
        if (receipt.request_id !== params.request_id || receipt.phase !== phase) throw new Error("编辑请求回执身份不一致");
        let details: any = {kind: "edit_request_status", phase, task_id:taskId, request_id: params.request_id, query_url: query,
          status: receipt.status, result: receipt.result};
        if (phase === "apply" && receipt.status === "recorded" && receipt.result_status_code < 400) {
          details={...details,kind:"edit_applied"};
        } else if (phase === "preview" && receipt.status === "recorded" && receipt.result?.proposal?.id) {
          typedPreviewBlocked = false;
          details = {kind: "edit_preview", preview: {...receipt.result, request_id: params.request_id, request_query_url: query}};
        } else if (receipt.status === "recorded" && receipt.result?.job_id) {
          details = {kind: "task_media_job", media_job: {...receipt.result, request_id: params.request_id, request_query_url: query}};
        }
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
  }
  function registerPreview(pi: ToolRegistrar) {
    if (getDeclaredCapabilities()) {
      const parameters = typedEditSchema(getDeclaredCapabilities());
      pi.registerTool({
        name: "preview_typed_edit", label: "预览类型化修改",
        description: "使用 API 真实 schema 提交类型化编辑；任务、用户原句、身份和基础版本由宿主绑定。音频EditAudioTimeline的audio_edit指定真实target_id；update修改片段、track修改音轨、add添加、split分割、duplicate复制、delete删除，values按action schema；画面AdjustVisualEffects指定真实clip_ids和非空effects patch。先读当前对象，降低6dB须用当前gain_db计算新绝对值，只传指定字段，遗漏保留、重置显式提交；淡入淡出音频用毫秒，画面用秒。duck_under_voice只音乐轨；gain_points从editor_gain_points读取，局部毫秒坐标，完整替换所选片段点列表；锁定对象先单独解锁。solo持久化与独立音频变速未开放。不绕过人像/资源许可。只生成提案/实际预览，不应用或宣称成片。按当前授权、实际成本与进展有限探索；同输入、未知结果或授权/版本拒绝不重放，无进展不盲循环。",
        parameters,
        execute: async (_id: string, params: Record<string, unknown>) => {
          if (!isTaskRead() || !selectedTaskVersion) throw new Error("请先读取本轮可信任务与版本");
          if (hasCreationAction() || hasEditControlWrite()) throw new Error("本轮已有执行或历史控制动作");
          if (typedPreviewBlocked) throw new Error("本轮预览尚待核验结果；不重复未知操作");
          if (Object.keys(params).some(key => isHostEditField(key) || !Object.hasOwn(parameters.properties, key)))
            throw new Error("模型参数包含未知字段或可信宿主字段，未提交预览");
          const capability = getEditCapabilities()?.operations.find((item: any) => item.operation_type === params.operation_type);
          if (!capability || !capability.available) throw new Error(capability?.reason || "当前 API 未声明该类型化预览可执行");
          const instruction = readActionSource().userInstruction;
          const turn = readActionSource().turnId;
          if (!instruction || !turn) throw new Error("本轮原句或身份未绑定，未提交预览");
          if (typeof _id !== "string" || !_id) throw new Error("本轮可信工具调用身份缺失，未提交预览");
          const input = JSON.stringify(params, (_key, value) => value && typeof value === "object" && !Array.isArray(value)
            ? Object.fromEntries(Object.entries(value).sort(([a], [b]) => a.localeCompare(b))) : value);
          if (typedInputs.has(input)) throw new Error("本轮已提交相同预览，不重复请求");
          typedInputs.add(input); typedPreviewBlocked = true; markPreview();
          const requestId = typedRequestId(turn, _id);
          const queryUrl = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${requestId}?phase=preview`;
          let preview: any;
          try {
            const result = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/edits/exact/preview`, {
              method: "POST", body: JSON.stringify({...params, assistant_turn_id: turn, request_id: requestId,
                expected_timeline_version_id: selectedTaskVersion.timeline_version_id,
                expected_timeline_sequence: selectedTaskVersion.timeline_sequence}),
            });
            preview = {...result, request_id: requestId, request_query_url: queryUrl};
          } catch (error) {
            const knownRejection = error instanceof BusinessRequestError && error.httpStatus < 500;
            // Only the API's parameter validation rejection permits another
            // distinct candidate. Auth/version denial and lost receipts stay closed.
            const parameterRejected = error instanceof BusinessRequestError && error.httpStatus === 422;
            if (parameterRejected) typedPreviewBlocked = false;
            const details = {kind: "edit_request_status", task_id: taskId, request_id: requestId, query_url: queryUrl,
              phase: "preview", assistant_turn_id: turn, tool_call_id: _id,
              status: error instanceof RequestNotSubmitted ? "not_submitted" : knownRejection ? "rejected" : "result_unknown",
              base_version_id: selectedTaskVersion.timeline_version_id, base_sequence: selectedTaskVersion.timeline_sequence,
              failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500),
              correction_allowed: parameterRejected,
              instruction: parameterRejected ? "参数已明确拒绝，没有生成修改；可在原授权和版本内纠正参数准备另一候选，不重发相同输入；按实际成本与进展有限探索，无进展则停止。"
                : "原请求身份已保留；先回读实际结果，不自动重POST或换绑最新版本。"};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
          let details: any;
          if (preview.job_id && preview.result_kind === "edit_preview") {
            details = {kind: "task_media_job", media_job: preview};
          } else {
            if (!preview.proposal?.id) throw new Error("预览未返回可核验提案；结果未知，不重放");
            typedPreviewBlocked = false;
            details = {kind: "edit_preview", preview};
          }
          return {content: [{type: "text", text: JSON.stringify(details)}], details};
        },
      });
    }
  }
  function onPreviewJobObserved(media_job: any) {
    if (media_job.status === "succeeded" && media_job.result?.proposal?.id
        && media_job.base_version_id === selectedTaskVersion?.timeline_version_id) typedPreviewBlocked = false;
  }
  return {registerRequestReader, registerPreview, registerNarrationRevision, onPreviewJobObserved};
}
