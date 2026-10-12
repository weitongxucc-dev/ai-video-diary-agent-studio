/** Layout-only synthetic records. Never written to an API or business database. */
import type { CreationTask } from "@/lib/types";

export const home100LayoutTasks: CreationTask[] = Array.from({length:100},(_,index) => ({
  id:`home-layout-fixture-${index}`,workspace_id:"fixture-only",plan_id:"fixture-only",
  visibility:"private",name:`布局样例 ${String(index+1).padStart(3,"0")}`,scenario:"非生产布局数据",
  stage:(["topic", "script", "narration", "montage", "review"] as const)[index % 5],stage_status:index < 40 ? "waiting_for_user" : index < 70 ? "running" : "completed",
  status:index < 40 ? "waiting_for_user" : index < 70 ? "running" : "completed",
  progress:0,recovery_point:"fixture-only",selected_topic_id:null,confirmed_script_id:null,
  narration_cache_key:null,montage_plan_id:null,current_timeline_version_id:null,timeline_sequence:0,
  can_undo:false,output_ready:false,stage_reason:null,stage_impact:null,stage_repair_action:null,stage_retryable:null,
  created_at:"2026-10-10T00:00:00Z",updated_at:"2026-10-10T00:00:00Z",
}));
