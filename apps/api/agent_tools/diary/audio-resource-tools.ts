import type {BusinessRequest, ToolRegistrar} from './contracts.ts';
import {typedRequestId} from './request-identity.ts';
import {BusinessRequestError} from './transport.ts';

/** Fixed resource imports use the original host-bound request journal. */
export function registerAudioResourceTools(pi: ToolRegistrar, {request, taskId, readActionSource}: {
  request: BusinessRequest; taskId: string | undefined; readActionSource?:()=>{turnId?:string;inputId?:string};
}) {
  const attempted=new Set<string>();
  pi.registerTool({name:'read_audio_resource_history',label:'回查资源采用与撤销来源',
    description:'按固定resource_id/version读取当前任务正式导入、采用、渲染、观察和撤销来源。仅回读原事件，不改时间线；采用/撤销继续使用既有编辑工具。',
    parameters:{type:'object',properties:{resource_id:{type:'string',minLength:1,maxLength:160},version:{type:'integer',minimum:1}},required:['resource_id','version'],additionalProperties:false},
    execute:async(_id:string,params:{resource_id:string;version:number})=>{
      if(!taskId||!Number.isInteger(params.version)||params.version<1)throw Error('缺少可信任务或固定版本');
      const details=await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/resources/${encodeURIComponent(params.resource_id)}/history?version=${params.version}`);
      return {details,content:[{type:'text',text:JSON.stringify(details)}]};
    }});
  pi.registerTool({name:'import_audio_resource',label:'导入固定资源音频',
    description:'读取剪辑资源目录后，按connected音乐/音效的固定resource_id/version导入当前任务。宿主绑定原回合与请求；unknown先read_audio_resource_request原键查询，不换键重复POST。返回Job不表示来源成功、采用或成片；查Job取得source_ref，再用既有apply_typed_edit采用，read_edit_history/undo_last_edit回查撤销。',
    parameters:{type:'object',properties:{resource_id:{type:'string',minLength:1,maxLength:160},version:{type:'integer',minimum:1}},required:['resource_id','version'],additionalProperties:false},
    execute:async(_id:string,params:{resource_id:string;version:number})=>{
      const source=readActionSource?.();
      if(!taskId||!source?.turnId)throw Error('缺少可信任务与原回合，未提交');
      if(Object.keys(params).some(key=>!['resource_id','version'].includes(key))||!params.resource_id||!Number.isInteger(params.version)||params.version<1)throw Error('固定资源参数无效');
      const request_id=typedRequestId(taskId,source.turnId,JSON.stringify([source.inputId??source.turnId,params.resource_id,params.version]));
      const query=`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/resource-requests/${request_id}`;
      let receipt;
      try{receipt=await request(query);}catch(error){
        if(!(error instanceof BusinessRequestError)||error.httpStatus!==404||attempted.has(request_id))throw error;
      }
      if(!receipt){
        attempted.add(request_id);
        try{receipt=await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/resources`,{method:'POST',body:JSON.stringify({...params,request_id,assistant_turn_id:source.turnId})});}
        catch{return {isError:true,details:{kind:'audio_resource_request',task_id:taskId,request_id,status:'unknown',query_url:query},content:[{type:'text',text:'导入结果未知；查询原请求，不重复提交。'}]};}
      }
      if(receipt.request_id!==request_id||receipt.task_id!==taskId)throw Error('原资源请求回执身份不符');
      const details={kind:'audio_resource_request',task_id:taskId,request_id,status:receipt.status,job_id:receipt.job_id??null,query_url:query};
      return {isError:receipt.status!=='recorded',details,content:[{type:'text',text:JSON.stringify(details)}]};
    }});
  pi.registerTool({name:'read_audio_resource_request',label:'回查资源音频原请求',
    description:'按导入返回的原request_id只读查询；unknown/404不能证明未提交，不重复POST。recorded沿原job_id查询来源。',
    parameters:{type:'object',properties:{request_id:{type:'string',format:'uuid'}},required:['request_id'],additionalProperties:false},
    execute:async(_id:string,params:{request_id:string})=>{
      if(!taskId)throw Error('缺少可信任务');
      const details=await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/audio/resource-requests/${encodeURIComponent(params.request_id)}`);
      return {details,content:[{type:'text',text:JSON.stringify(details)}]};
    }});
  pi.registerTool({name:'read_audio_resource_import', label:'回查资源音频导入',
    description:'按原job_id只读当前任务音频导入结果。queued/running/failed/unknown不返回可采用来源；只有成功的audio_source作业返回真实source_ref。未知先读原Job，不能重复POST；成功仅为音频来源，采用沿apply_typed_edit，回查read_edit_history，撤销undo_last_edit。',
    parameters:{type:'object',properties:{job_id:{type:'string',minLength:1,maxLength:120}},
      required:['job_id'],additionalProperties:false},
    execute:async (_id:string,params:{job_id:string})=>{
      if (!taskId) throw new Error('音频导入回查需要当前可信任务');
      if (!params.job_id || Object.keys(params).some(key=>key!=='job_id'))
        throw new Error('仅接受原作业身份，不接受路径或来源替换');
      const job=await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-jobs/${encodeURIComponent(params.job_id)}`);
      // TaskMediaJobResponse has no task_id. The task-scoped read is
      // authorized by the host, which verifies the stored Job's task/actor.
      if (job.job_id!==params.job_id || job.result_kind!=='audio_source')
        throw new Error('原音频导入作业身份或类型不符，未采用其他来源');
      const source=job.status==='succeeded'?job.result?.source_ref:null;
      if (job.status==='succeeded' && (typeof source!=='string' || !source))
        throw new Error('成功作业缺少真实音频来源，请核对原回执');
      const details={kind:'audio_resource_import',task_id:taskId,job_id:params.job_id,status:job.status,
        ...(source?{source_ref:source}:{}),queried_only:true,adopted:false};
      return {content:[{type:'text',text:JSON.stringify(details)}],details};
    }});
}
