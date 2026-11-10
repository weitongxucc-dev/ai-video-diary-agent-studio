import type {BusinessRequest,ToolRegistrar,ActionSource} from './contracts.ts';
import {BusinessRequestError,RequestNotSubmitted} from './transport.ts';

export function registerVisualTemplateTools(pi:ToolRegistrar,{request,readActionSource,typedRequestId}:{
  request:BusinessRequest;readActionSource:()=>ActionSource;typedRequestId:(turn:string,call:string)=>string;
}) {
  const known=new Map<string,any>();
  const attempted=new Map<string,string>();
  const result=(details:any)=>({content:[{type:'text' as const,text:JSON.stringify(details)}],details});
  pi.registerTool({name:'read_visual_template',label:'读取固定版动画源码',
    description:'读取视觉组件固定版、参数绑定、来源许可和HTML源码。source_offset/source_limit分页，truncated为真时继续；结构模板没有视觉源码。已有任务实例先read_task_object读取graphic_overlays，人工改动保存在definition和keyframes中，不用库默认值覆盖。',
    parameters:{type:'object',properties:{template_id:{type:'string'},version:{type:'integer',minimum:1},
      source_offset:{type:'integer',minimum:0},source_limit:{type:'integer',minimum:1,maximum:65536}},required:['template_id','version'],additionalProperties:false},
    execute:async (_id:string,p:any)=>{
      const value=await request(`/api/video-templates/${encodeURIComponent(p.template_id)}?version=${p.version}`);
      const visual=value.settings?.visual;
      if(!visual||value.id!==p.template_id||value.version!==p.version)throw Error('该固定版不是可编辑视觉组件');
      known.set(`${value.id}:${value.version}`,value);
      const {source,...definition}=visual;
      const offset=p.source_offset??0,limit=p.source_limit??32768;
      if(!Number.isInteger(offset)||offset<0||offset>source.length||!Number.isInteger(limit)||limit<1||limit>65536)throw Error('源码读取范围无效');
      const end=Math.min(source.length,offset+limit);
      return result({template:{id:value.id,version:value.version,name:value.name,origin:value.origin},definition,
        source_window:{content:source.slice(offset,end),offset,next_offset:end,total_chars:source.length,truncated:end<source.length},
        visual_schema:value.visual_schema,
        usage:'EditGraphicOverlay kind=template，固定template_id/template_version，definition内放完整源码及当前参数；使用真实编辑schema，不自动制作。保存我的模板需用户明确要求。'});
    }});
  pi.registerTool({name:'save_visual_template',label:'另存我的视觉模板',
    description:'用户明确要求保存模板时另存源码、绑定与参数，不自动保存成功经验。先read_visual_template读取来源；visual遵循原definition结构并保留provenance，可改source、bindings和values。响应未知先按query_url查询，不重放。',
    parameters:{type:'object',properties:{source_template_id:{type:'string'},source_template_version:{type:'integer',minimum:1},
      name:{type:'string',minLength:1,maxLength:80},visual:{type:'object',additionalProperties:true}},
      required:['source_template_id','source_template_version','name','visual'],additionalProperties:false},
    execute:async (_call:string,p:any)=>{
      const source=readActionSource();
      if(!source.turnId||!source.userInstruction||!known.has(`${p.source_template_id}:${p.source_template_version}`))throw Error('请先读取来源固定版，并绑定本轮明确保存要求');
      const requestId=typedRequestId(source.turnId,`visual-template-save:${source.inputId??source.turnId}`),id=`template_${requestId.replaceAll('-','')}`;
      const effect=`${source.inputId??source.turnId}:save`;
      if(attempted.has(effect))throw Error('原保存已提交，请读取原模板结果：'+attempted.get(effect));
      const query=`/api/video-templates/${encodeURIComponent(id)}?version=1`;
      attempted.set(effect,query);
      try{return result({status:'saved',template:await request('/api/video-templates',{method:'POST',body:JSON.stringify({...p,request_id:requestId})}),query_url:query});}
      catch(error){const status=error instanceof RequestNotSubmitted?'not_submitted':error instanceof BusinessRequestError&&error.httpStatus<500?'rejected':'result_unknown';
        if(status!=='result_unknown')attempted.delete(effect);
        return {isError:true,...result({status,query_url:query,reason:(error as Error).message})};}
    }});
  pi.registerTool({name:'update_visual_template',label:'保存视觉模板下一版本',
    description:'用户明确要求更新自己的模板时追加固定新版本。先read_visual_template读取expected_version；保留原始provenance，不能覆盖内置或他人模板。响应未知只读query_url，不重放。',
    parameters:{type:'object',properties:{template_id:{type:'string'},expected_version:{type:'integer',minimum:1},
      name:{type:'string',minLength:1,maxLength:80},visual:{type:'object',additionalProperties:true}},
      required:['template_id','expected_version','name','visual'],additionalProperties:false},
    execute:async (_call:string,p:any)=>{
      const source=readActionSource(),key=`${p.template_id}:${p.expected_version}`;
      const previous=known.get(key);
      if(!source.turnId||!source.userInstruction||!previous||previous.origin!=='user')throw Error('请先读取自己的模板固定版，并绑定本轮更新要求');
      const effect=`${source.inputId??source.turnId}:update:${p.template_id}`;
      if(attempted.has(effect))throw Error('本轮已提交此模板更新，请先查询原版本：'+attempted.get(effect));
      const query=`/api/video-templates/${encodeURIComponent(p.template_id)}?version=${p.expected_version+1}`;
      attempted.set(effect,query);
      const {template_id,...payload}=p;
      try{return result({status:'saved',template:await request(`/api/video-templates/${encodeURIComponent(template_id)}/versions`,
        {method:'POST',body:JSON.stringify({...payload,style_instruction:previous.settings.style_instruction})}),query_url:query});}
      catch(error){const status=error instanceof RequestNotSubmitted?'not_submitted':error instanceof BusinessRequestError&&error.httpStatus<500?'rejected':'result_unknown';
        if(status!=='result_unknown')attempted.delete(effect);
        return {isError:true,...result({status,query_url:query,reason:(error as Error).message})};}
    }});
}
