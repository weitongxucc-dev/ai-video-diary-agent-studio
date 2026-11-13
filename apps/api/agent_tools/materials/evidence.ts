import type {ToolRegistrar, BusinessRequest} from '../diary/contracts.ts';
import {materialText} from './projections.ts';

export function registerMaterialEvidence(pi: ToolRegistrar, request: BusinessRequest) {
  pi.registerTool({name:'read_material_governance',label:'读取素材治理状态',
    description:'读取当前工作区已授权素材的治理闭环摘要：分析运行、证据、候选断言、索引新鲜度、质量档案、片段血缘和重建任务。只读、不采用、不修改时间线；stale/failed/unavailable必须按真实状态表达，不能把候选断言当成已确认事实。',
    parameters:{type:'object',properties:{asset_id:{type:'string',minLength:1}},required:['asset_id'],additionalProperties:false},
    execute:async (_id:string,params:{asset_id:string})=>{
      const value=await request(`/api/production/materials/${encodeURIComponent(params.asset_id)}/data-governance`);
      const details=JSON.parse(JSON.stringify(value,(_key,item)=>typeof item==='string'?materialText(item):item));
      if(details.asset_id && details.asset_id!==params.asset_id) throw new Error('素材治理归属不一致');
      const runs=Array.isArray(details.analysis_runs)?details.analysis_runs:[];
      const evidence=Array.isArray(details.evidence)?details.evidence:[];
      const assertions=Array.isArray(details.assertions)?details.assertions:[];
      const indexes=Array.isArray(details.indexes)?details.indexes:[];
      const quality=Array.isArray(details.quality_profiles)?details.quality_profiles:[];
      const output={
        asset_id:params.asset_id,
        analysis_runs:runs,
        evidence,
        assertions,
        indexes,
        lineage:Array.isArray(details.lineage)?details.lineage:[],
        quality_profiles:quality,
        index_rebuild_jobs:Array.isArray(details.index_rebuild_jobs)?details.index_rebuild_jobs:[],
        summary:{analysis_runs:runs.length,evidence_refs:evidence.length,assertions:assertions.length,
          candidate_assertions:assertions.filter((item:any)=>item.status==='candidate').length,
          confirmed_assertions:assertions.filter((item:any)=>item.status==='confirmed').length,
          stale_indexes:indexes.filter((item:any)=>item.status==='stale').length,
          unavailable_quality_metrics:quality.flatMap((item:any)=>Object.values(item.metrics||{})).filter((item:any)=>item?.status==='unavailable').length},
        instruction:'这是治理回执，不是自动采用许可。使用素材前仍需核对用途状态、证据有效性、源修订和当前任务采用回执。'
      };
      return {content:[{type:'text',text:JSON.stringify(output)}],details:output};
    }});
  pi.registerTool({name:'read_material_evidence',label:'读取素材时间证据',
    description:'按实际检索/引用中的asset_id读取可见素材的有界原片证据。segments含人工描述/纠正、采样画面/OCR；speech为原ASR句段；words为实际word/token对齐，绝不按字数推时间。时间是源时钟，执行前映射到当前时间线；跨页固定expected_updated_at。只读不自动采用，不外发媒体。',
    parameters:{type:'object',properties:{asset_id:{type:'string',minLength:1},
      expected_updated_at:{type:'string'},kind:{type:'string',enum:['segments','speech','words']},
      start_seconds:{type:'number',minimum:0},end_seconds:{type:'number',exclusiveMinimum:0},
      offset:{type:'integer',minimum:0},limit:{type:'integer',minimum:1,maximum:20}},required:['asset_id'],additionalProperties:false},
    execute:async (_id:string,params:{asset_id:string;expected_updated_at?:string;kind?:string;start_seconds?:number;end_seconds?:number;offset?:number;limit?:number})=>{
      const query=new URLSearchParams();
      for(const [key,value] of Object.entries(params)) if(key!=='asset_id' && value!==undefined) query.set(key,String(value));
      const value=await request(`/api/production/materials/${encodeURIComponent(params.asset_id)}/evidence?${query}`);
      const details=JSON.parse(JSON.stringify(value,(_key,item)=>typeof item==='string'?materialText(item):item));
      if(details.asset_id!==params.asset_id) throw new Error('素材证据归属不一致');
      return {content:[{type:'text',text:JSON.stringify(details)}],details};
    }});
}
