import type {BusinessRequest, ToolRegistrar} from './contracts.ts';
import {registerMaterialTools} from '../material_tools.ts';
import {taskObjectBinding,taskObjectCollections} from '../task_facts.ts';

/** Read one host-frozen reference; the model selects an index, never an ID/path. */
export function registerReferenceRead(pi:ToolRegistrar,request:BusinessRequest,references:any[]) {
  pi.registerTool({name:"read_selected_reference",label:"读取选中引用",
    description:"按本次宿主冻结集合的1起序号读取一项引用的真实固定版本/范围，支持多项素材、人物、声音、知识与任务对象。每次重新核权限和版本；集合标签不是内容证据。不接受ID、路径或新版本，不授权修改或用于生成。",
    parameters:{type:"object",properties:{reference_index:{type:"integer",minimum:1,maximum:32}},required:["reference_index"],additionalProperties:false},
    execute:async(callId:string,{reference_index:index}:{reference_index:number})=>{
      if (!Number.isInteger(index) || index<1 || index>references.length) throw new Error("引用序号不在本次固定集合中，请按宿主集合重新选择");
      const item=references[index-1];
      let data:any;
      if (item.kind === "person" || item.kind === "voice") {
        data=await request(`/api/assistant/profile-references/${item.kind}/${encodeURIComponent(item.profile_id)}?version=${encodeURIComponent(item.version)}`);
      } else if (item.kind === "knowledge") {
        const query=new URLSearchParams({version:item.version});
        if(item.locator)query.set("locator",item.locator);
        data=await request(`/api/assistant/knowledge-references/${encodeURIComponent(item.knowledge_item_id)}?${query}`);
      } else if (item.kind === "material") {
        const selection=item.selection;
        const tools=new Map<string,any>();
        registerMaterialTools({registerTool:tool=>tools.set(tool.name,tool)},{request,selection:{
          scope:selection.scope,assetId:selection.asset_id,segmentId:selection.segment_id,
          expectedUpdatedAt:selection.expected_updated_at,expectedSegmentUpdatedAt:selection.expected_segment_updated_at,
        }});
        return tools.get("read_selected_material").execute(callId,{});
      } else {
        const workspace=await request(`/api/creation-tasks/${encodeURIComponent(item.task_id)}`);
        if(workspace.task?.id!==item.task_id || workspace.task.current_timeline_version_id!==item.version)
          throw new Error("引用所属任务版本已变化，请重新选择；没有改绑当前对象");
        const collections=taskObjectCollections(workspace);
        const kinds:Record<string,string[]>={shot:["clips"],effect:["graphic_overlays"],narration:["narration_segments"],
          audio:["audio_tracks","audio_clips"],subtitle:["subtitle_tracks","subtitle_cues"]};
        const object=kinds[item.kind].flatMap(kind=>(collections as any)[kind]).find(object=>object.id===item.object_id);
        if(!object)throw new Error("引用对象在该固定版本中不可读取");
        data={...taskObjectBinding(workspace),reference:item,object};
      }
      return {content:[{type:"text",text:JSON.stringify(data)}],details:data};
    },
  });
}
