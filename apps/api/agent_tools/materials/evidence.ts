import type {ToolRegistrar, BusinessRequest} from '../diary/contracts.ts';
import {materialText} from './projections.ts';

export function registerMaterialEvidence(pi: ToolRegistrar, request: BusinessRequest) {
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
