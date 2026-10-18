import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';

const profileReference = {type: ["object", "null"], properties: {
  profile_id: {type: "string", minLength: 1}, version: {type: "string", minLength: 1},
}, required: ["profile_id", "version"], additionalProperties: false};
const deliveryProperties = {
  language: {type: "string", enum: ["zh-CN", "en-US", "zh-CN/en-US"]},
  aspect_ratio: {type: "string", enum: ["9:16", "16:9", "1:1"]},
  duration_target_seconds: {type: "integer", minimum: 15, maximum: 600},
  presenter_share_target: {type: "integer", enum: [0, 20, 40, 60, 80, 100]},
  lipsync_quality: {type: "string", enum: ["high", "fast"]},
  selected_voice: profileReference, selected_person: profileReference,
};
type IntakeIntent = {kind: "discussion" | "goal_delegation" | "final_script"; topic_title?: string;
  final_script?: {title: string; text: string; reply_origin?: {conversation_id: string; turn_id: string; request_id: string}}};
const intentSchema = {type: "object", properties: {
  kind: {type: "string", enum: ["discussion", "goal_delegation", "final_script"]},
  topic_title: {type: "string", minLength: 1, maxLength: 80},
  final_script: {type: "object", properties: {title: {type: "string", minLength: 1, maxLength: 160},
    text: {type: "string", minLength: 1, maxLength: 80000},
    reply_origin: {type: "object", properties: {conversation_id: {type: "string", format: "uuid"},
      turn_id: {type: "string", format: "uuid"}, request_id: {type: "string", format: "uuid"}},
      required: ["conversation_id", "turn_id", "request_id"], additionalProperties: false}},
    required: ["title", "text"], additionalProperties: false},
}, required: ["kind"], additionalProperties: false};

export function registerCreationPlans(pi: ToolRegistrar, {request, readActionSource, hasPreview, markPreview}: {
  request: BusinessRequest; readActionSource: () => ActionSource; hasPreview: () => boolean; markPreview: () => void;
}) {
  let creationRequest: any = null;
  let parentCreationPromise: Promise<any> | null = null;
  let creationInputKey: string | undefined;
  pi.registerTool({
    name: "preview_creation_plan",
    label: "预检新视频方案",
    description: "保存用户的完整制作目标和总数量，读取真实澄清状态。数量可以很大，只保留总目标，不一次创建全部任务。未明确多条关系或素材范围时先澄清；当前不会开始制作。",
    parameters: {
      type: "object",
      properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 },
        target_count: { type: "integer", minimum: 1, maximum: 9007199254740991 },
        output_relation: { type: "string", enum: ["single", "different_topics", "variants", "series"] },
        material_scope: { type: "string", enum: ["workspace_library", "today_received"], description: "默认workspace_library，使用现有可用素材。仅用户明确将素材范围限制为今日接收时才选today_received；今天制作、刚导入或稍后导入不构成该限制。" },
        name: { type: "string", minLength: 1, maxLength: 120 },
        ...deliveryProperties,
        intent: intentSchema,
        template_id: { type: "string", minLength: 1, maxLength: 120 },
        template_version: { type: "integer", minimum: 1 },
      },
      required: ["goal", "intent"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { goal: string; intent: IntakeIntent; target_count?: number; output_relation?: string; material_scope?: string; name?: string; language?: string; aspect_ratio?: string; duration_target_seconds?: number; presenter_share_target?: number; lipsync_quality?: "high" | "fast"; template_id?: string; template_version?: number; selected_voice?: any; selected_person?: any }) => {
      if (hasPreview()) throw new Error("本轮已生成新视频预检，请用户检查后再决定下一步");
      const source = readActionSource();
      const turnId = source.turnId;
      if (!turnId) throw new Error("当前创作回合不可核验，没有保存制作目标");
      if (!params.intent) throw new Error("先明确本轮是讨论、自主制作委托还是按准确定稿制作，没有保存制作授权");
      markPreview();
      creationInputKey = source.inputId ?? turnId;
      parentCreationPromise = request("/api/creation-requests", {
        method: "POST",
        body: JSON.stringify({
          goal: params.goal,
          request_key: source.inputId ?? turnId, assistant_turn_id: turnId,
          intent: params.intent,
          ...(params.name ? { name: params.name } : {}),
          target_count: params.target_count ?? 1,
          output_relation: params.output_relation ?? null,
          material_scope: params.material_scope ?? "workspace_library",
          language: params.language || "zh-CN",
          aspect_ratio: params.aspect_ratio || "9:16",
          duration_target_seconds: params.duration_target_seconds || 60,
          ...((params.presenter_share_target !== undefined || !params.template_id)
            ? { presenter_share_target: params.presenter_share_target ?? 0 } : {}),
          ...((params.lipsync_quality !== undefined || !params.template_id)
            ? { lipsync_quality: params.lipsync_quality ?? "high" } : {}),
          ...(params.template_id ? { template_id: params.template_id, template_version: params.template_version } : {}),
          ...(params.selected_voice !== undefined ? {selected_voice: params.selected_voice} : {}),
          ...(params.selected_person !== undefined ? {selected_person: params.selected_person} : {}),
        }),
      }).then(created => {
        creationRequest = created;
        return created;
      });
      const created = await parentCreationPromise;
      return {
        content: [{ type: "text", text: JSON.stringify({
          goal: created.goal, target_count: created.target_count, output_relation: created.output_relation,
          clarification_questions: created.clarification_questions,
          instruction: "总目标已保存，尚未创建任务或成片。缺重要条件先询问；已明确时只准备当前至多8条具体目标，不用重复文案凑数量。",
        }) }],
        details: { kind: "plan_preview", preview: { kind: "creation_request", request: created } },
      };
    },
  });

  pi.registerTool({
    name: "prepare_creation_window",
    label: "准备当前这组视频",
    description: "展开当前至多8条独立方案，未给出的逐条要求继承共同要求。只讨论保持草稿；已保存自主委托或准确定稿时，沿原授权采用实际方案和文案版本，不增加人工确认。采用不是成片已生成。",
    parameters: { type: "object", properties: {
      goals: { type: "array", minItems: 1, maxItems: 8, items: { type: "object", properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 }, name: { type: "string", minLength: 1, maxLength: 120 },
        requirements: {type: "object", properties: deliveryProperties, additionalProperties: false},
      }, required: ["goal"], additionalProperties: false } },
    }, required: ["goals"], additionalProperties: false },
    execute: async (_id: string, params: {goals: {goal: string; name?: string; requirements?: Record<string, unknown>}[]}) => {
      const source = readActionSource();
      const inputKey = source.inputId ?? source.turnId;
      if (creationInputKey && creationInputKey !== inputKey) throw new Error("当前输入已变化，没有采用上一输入的制作目标");
      if (parentCreationPromise) {
        try { await parentCreationPromise; }
        catch (cause) { throw new Error("本轮完整目标保存失败，未展开方案；请查看目标保存的实际原因", { cause }); }
      }
      const boundRequestId = readActionSource().creationRequestId;
      if (!creationRequest && boundRequestId) {
        const saved = await request(`/api/creation-requests/${encodeURIComponent(boundRequestId)}`);
        if (saved.id !== boundRequestId) throw new Error("回读目标与本轮已保存请求不一致，没有展开任务");
        creationRequest = saved;
        creationInputKey = inputKey;
      }
      if (!creationRequest || creationRequest.clarification_questions.length) throw new Error("先保存本次完整目标并确认重要条件，没有展开任务");
      if (creationRequest.current_window?.status === "proposed" && !["goal_delegation", "final_script"].includes(creationRequest.intent?.kind)) {
        throw new Error("当前这组方案尚待采用，没有另建一组");
      }
      if (!creationRequest.current_window || creationRequest.current_window.status === "confirmed") {
        creationRequest = await request(`/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows`, {
          method: "POST", body: JSON.stringify({expected_revision: creationRequest.revision, goals: params.goals}),
        });
      }
      if (["goal_delegation", "final_script"].includes(creationRequest.intent?.kind)
          && creationRequest.current_window?.status === "proposed") {
        const window = creationRequest.current_window;
        creationRequest = await request(`/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows/${encodeURIComponent(window.id)}/confirm`, {
          method: "POST", body: JSON.stringify({expected_revision: creationRequest.revision,
            input_fingerprint: window.input_fingerprint, adoption_kind: "delegated_system", assistant_turn_id: source.turnId}),
        });
        if (creationRequest.current_window?.id !== window.id || creationRequest.current_window.status !== "confirmed"
            || creationRequest.current_window.entries.some((item: any) => !item.task?.id || item.task.id !== item.task_id)) {
          throw new Error("窗口采用没有返回相同窗口的实际任务身份，未宣称任务已创建");
        }
      }
      return { content: [{type:"text", text:JSON.stringify({
        target_count:creationRequest.target_count, proposed_count:creationRequest.current_window.entries.length,
        goals:creationRequest.current_window.entries.map((entry: any)=>({ordinal:entry.ordinal,
          goal:entry.plan.goal, name:entry.plan.name, language:entry.plan.language, task_id:entry.task?.id ?? null,
          blockers:entry.plan.blockers, confirmable:entry.plan.confirmable})),
        instruction: creationRequest.current_window.status === "confirmed"
          ? "这组已按原委托实际采用，任务身份来自保存回执；成片尚未生成。继续各任务的已授权步骤，不要求重新确认。"
          : creationRequest.intent?.kind === "discussion" ? "讨论方案已保存；没有采用、创建任务或开始制作。"
          : "方案已保存，尚未采用或创建任务；总目标其余部分还未规划。",
      })}], details: {kind:"plan_preview", preview:{kind:"creation_request",request:creationRequest}} };
    },
  });

}
