import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';

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
  let needsRead = false;
  let creationUnknown = false;
  let replanRequired = false;
  const inputKey = () => readActionSource().inputId ?? readActionSource().turnId;
  function verifyInput(key: string | undefined) {
    if (!key || key !== inputKey()) throw new Error("当前输入已变化，没有沿旧输入修订或采用制作目标");
  }
  async function currentRequest(refresh = false, allowMissing = false) {
    const key = inputKey();
    if (parentCreationPromise) await parentCreationPromise;
    // Unset host environment fields are empty strings, not request identities.
    const boundId = readActionSource().creationRequestId || undefined;
    const requestId = boundId ?? creationRequest?.id;
    if (!refresh && (needsRead || boundId && creationRequest && boundId !== creationRequest.id))
      throw new Error("原请求结果或修订待核对，请先read_creation_request，不重复提交旧窗口");
    if (refresh || !creationRequest) {
      const turnId = readActionSource().turnId;
      if (!requestId && !turnId) throw new Error("当前创作回合不可核验，不能猜原制作目标身份");
      const saved = await request(requestId ? `/api/creation-requests/${encodeURIComponent(requestId)}`
        : `/api/assistant/creation-request?assistant_turn_id=${encodeURIComponent(turnId!)}`);
      verifyInput(key);
      if (saved === null) {
        if (allowMissing && !requestId && !creationUnknown && !needsRead) return null;
        if (requestId || creationUnknown) needsRead = true;
        throw new Error("尚未查到原请求，不能证明请求未写入；保持原request_key，不能换ID重交。");
      }
      if (typeof saved.id !== "string" || !saved.id || requestId && saved.id !== requestId)
        throw new Error("回读目标与本轮已保存请求不一致，没有修改任务");
      creationRequest = saved;
      creationInputKey = key;
      needsRead = false;
      if (creationUnknown) markPreview();
      creationUnknown = false;
    }
    verifyInput(creationInputKey);
    return creationRequest;
  }
  async function writeRequest(path:string, init:RequestInit, key:string|undefined) {
    try {
      const saved = await request(path, init);
      verifyInput(key);
      creationRequest = saved;
      replanRequired = false;
      return saved;
    } catch (error) {
      needsRead = true;
      if (path.endsWith("/confirm")) replanRequired = true;
      throw error;
    }
  }
  function matchesGoals(window:any, goals:{goal:string; name?:string; requirements?:Record<string,unknown>}[]) {
    return window.entries.length === goals.length && window.entries.every((entry:any, index:number) => {
      const goal = goals[index], plan = entry.plan;
      return goal.goal.trim() === plan.goal && (!goal.name || goal.name === plan.name)
        && Object.keys(deliveryProperties).every(key => {
          const provided = goal.requirements && Object.hasOwn(goal.requirements,key);
          const expected = provided ? goal.requirements![key] : creationRequest.requirements?.[key];
          if (expected === undefined || !provided && expected === null && ["selected_voice","selected_person"].includes(key)) return true;
          if (expected === null) return plan[key] === null;
          return ["selected_voice","selected_person"].includes(key)
            ? (expected as any)?.profile_id === plan[key]?.profile_id && (expected as any)?.version === plan[key]?.version
            : expected === plan[key];
        });
    });
  }
  const actionPath = (path: string) => {
    const turnId = readActionSource().turnId;
    if (!turnId) throw new Error("当前创作回合不可核验，没有提交制作要求");
    return `${path}?assistant_turn_id=${encodeURIComponent(turnId)}`;
  };
  function result(created: any) {
    const window = created.current_window;
    const profile = (value: any) => value ? {profile_id: value.profile_id, version: value.version} : null;
    const delivery = (value: any) => Object.fromEntries(Object.keys(deliveryProperties).map(key =>
      [key, key === "selected_voice" || key === "selected_person" ? profile(value[key]) : value[key]]));
    const facts = {request_id: created.id, revision: created.revision, status: created.status, goal: created.goal,
      intent_kind: created.intent?.kind, target_count: created.target_count, confirmed_count: created.confirmed_count,
      output_relation: created.output_relation, material_scope: created.material_scope,
      requirements: delivery(created.requirements || {}), clarification_questions: created.clarification_questions,
      window_id: window?.id, window_status: window?.status, input_fingerprint: window?.input_fingerprint,
      goals: window?.entries.map((entry: any) => ({ordinal: entry.ordinal, goal: entry.plan.goal, name: entry.plan.name, language: entry.plan.language,
        requirements: delivery(entry.plan), task_id: entry.task?.id ?? null,
        blockers: entry.plan.blockers, confirmable: entry.plan.confirmable})),
      instruction: window?.status === "confirmed" ? "这组已实际采用，继续原委托的任务；不是成片已生成。"
        : "这是当前实际要求及候选；新要求用修订或prepare_creation_window保存，不能重复确认旧内容。"};
    return {content: [{type:"text", text:JSON.stringify(facts)}],
      details: {kind:"plan_preview", preview:{kind:"creation_request", request:created}}};
  }
  pi.registerTool({name: "read_creation_request", label: "读取制作目标",
    description: "只读本轮绑定的原制作请求、当前修订、共同要求、窗口与逐条要求和阻断；首次空查返回lookup_status:not_found，仅表示当前查询未发现，不证明从未提交。新目标沿原request_key保存；已有提交未知或旧绑定仍先对账，不创建新身份或采用旧窗口。",
    parameters: {type:"object", properties:{}, additionalProperties:false},
    execute: async () => {
      const current = await currentRequest(true, true);
      if (current) return result(current);
      const details = {kind: "creation_request_lookup", lookup_status: "not_found", request_key: inputKey(),
        instruction: "当前查询未发现请求，不表示提交未发生。新目标沿当前原request_key调用preview_creation_plan保存；曾提交而结果未知时先按原身份对账，不换ID或盲重试。"};
      return {content: [{type: "text", text: JSON.stringify(details)}], details};
    },
  });
  pi.registerTool({name: "revise_creation_request", label: "修订制作要求",
    description: "先读取当前制作目标，比较其revision后修订共同要求或多条关系/素材范围。只更新明确传入字段，保留原委托身份；会作废尚未采用的窗口，随后用prepare_creation_window保存实际逐条要求。已有采用任务不能改写，不自动创建或生成。",
    parameters: {type:"object", properties:{expected_revision:{type:"integer", minimum:1},
      output_relation:{type:"string", enum:["single","different_topics","variants","series"]},
      material_scope:{type:"string", enum:["workspace_library","today_received"]},
      requirements:{type:"object", properties:deliveryProperties, additionalProperties:false}},
      required:["expected_revision"], additionalProperties:false},
    execute: async (_id:string, params:{expected_revision:number; output_relation?:string; material_scope?:string; requirements?:Record<string,unknown>}) => {
      const current = await currentRequest();
      const {creation_intent, ...requirements} = current.requirements;
      const key = creationInputKey;
      const updated = await writeRequest(actionPath(`/api/creation-requests/${encodeURIComponent(current.id)}`), {
        method:"PATCH", body:JSON.stringify({...params,
          ...(params.requirements ? {requirements:{...requirements, ...params.requirements}} : {})})}, key);
      return result(updated);
    },
  });
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
      if (creationUnknown || needsRead) throw new Error("原目标保存结果或修订待核对，请先read_creation_request核对原请求，不另建目标");
      const source = readActionSource();
      const turnId = source.turnId;
      if (!turnId) throw new Error("当前创作回合不可核验，没有保存制作目标");
      if (!params.intent) throw new Error("先明确本轮是讨论、自主制作委托还是按准确定稿制作，没有保存制作授权");
      const key = source.inputId ?? turnId;
      creationInputKey = key;
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
        verifyInput(key);
        creationRequest = created;
        markPreview();
        return created;
      });
      const pending = parentCreationPromise;
      let created;
      try { created = await pending; }
      catch (error) {
        creationUnknown = !(error instanceof RequestNotSubmitted
          || error instanceof BusinessRequestError && error.httpStatus < 500);
        throw error;
      } finally {
        if (parentCreationPromise === pending) parentCreationPromise = null;
      }
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
    description: "按本次goals实际编译当前至多8条方案，已有proposed窗口则保存替代新版，绝不直接确认旧内容。逐条未给字段才继承共同要求。已采用的相同要求只回读原任务；不同新目标才展开后续窗口。冲突/未知先read_creation_request，修订共同要求用revise_creation_request。讨论保持草稿；自主委托或准确定稿沿原授权采用实际新版，不增加人工确认，采用不是出片。",
    parameters: { type: "object", properties: {
      goals: { type: "array", minItems: 1, maxItems: 8, items: { type: "object", properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 }, name: { type: "string", minLength: 1, maxLength: 120 },
        requirements: {type: "object", properties: deliveryProperties, additionalProperties: false},
      }, required: ["goal"], additionalProperties: false } },
    }, required: ["goals"], additionalProperties: false },
    execute: async (_id: string, params: {goals: {goal: string; name?: string; requirements?: Record<string, unknown>}[]}) => {
      const source = readActionSource();
      await currentRequest();
      const key = creationInputKey;
      if (!creationRequest || creationRequest.clarification_questions.length) throw new Error("先保存本次完整目标并确认重要条件，没有展开任务");
      const previous = creationRequest.current_window;
      const same = previous && matchesGoals(previous, params.goals);
      if (previous?.status === "confirmed") {
        if (same) return result(creationRequest);
        if (creationRequest.confirmed_count >= creationRequest.target_count)
          throw new Error("当前目标已采用，新要求不能覆盖已创建任务；请读取原任务或另建制作目标");
      }
      if (previous?.status !== "proposed" || !same || replanRequired) {
        const path = `/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows`
          + (previous?.status === "proposed" ? `/${encodeURIComponent(previous.id)}/replan` : "");
        await writeRequest(actionPath(path), {method:"POST",
          body:JSON.stringify({expected_revision:creationRequest.revision, goals:params.goals})}, key);
      }
      if (["goal_delegation", "final_script"].includes(creationRequest.intent?.kind)
          && creationRequest.current_window?.status === "proposed") {
        const window = creationRequest.current_window;
        await writeRequest(`/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows/${encodeURIComponent(window.id)}/confirm`, {
          method: "POST", body: JSON.stringify({expected_revision: creationRequest.revision,
            input_fingerprint: window.input_fingerprint, adoption_kind: "delegated_system", assistant_turn_id: source.turnId}),
        }, key);
        if (creationRequest.current_window?.id !== window.id || creationRequest.current_window.status !== "confirmed"
            || creationRequest.current_window.entries.some((item: any) => !item.task?.id || item.task.id !== item.task_id)) {
          throw new Error("窗口采用没有返回相同窗口的实际任务身份，未宣称任务已创建");
        }
      }
      return result(creationRequest);
    },
  });

}
