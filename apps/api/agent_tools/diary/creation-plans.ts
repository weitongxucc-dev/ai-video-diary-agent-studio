import type {BusinessRequest, ToolRegistrar, ActionSource} from './contracts.ts';

export function registerCreationPlans(pi: ToolRegistrar, {request, readActionSource, hasPreview, markPreview}: {
  request: BusinessRequest; readActionSource: () => ActionSource; hasPreview: () => boolean; markPreview: () => void;
}) {
  let creationRequest: any = null;
  let parentCreationPromise: Promise<any> | null = null;
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
        language: { type: "string", enum: ["zh-CN", "en-US", "zh-CN/en-US"] },
        aspect_ratio: { type: "string", enum: ["9:16", "16:9", "1:1"] },
        duration_target_seconds: { type: "integer", minimum: 15, maximum: 600 },
        presenter_share_target: { type: "integer", enum: [0, 20, 40, 60, 80, 100] },
        lipsync_quality: { type: "string", enum: ["high", "fast"] },
        template_id: { type: "string", minLength: 1, maxLength: 120 },
        template_version: { type: "integer", minimum: 1 },
      },
      required: ["goal"],
      additionalProperties: false,
    },
    execute: async (_id: string, params: { goal: string; target_count?: number; output_relation?: string; material_scope?: string; name?: string; language?: string; aspect_ratio?: string; duration_target_seconds?: number; presenter_share_target?: number; lipsync_quality?: "high" | "fast"; template_id?: string; template_version?: number }) => {
      if (hasPreview()) throw new Error("本轮已生成新视频预检，请用户检查后再决定下一步");
      const turnId = readActionSource().turnId;
      if (!turnId) throw new Error("当前创作回合不可核验，没有保存制作目标");
      markPreview();
      parentCreationPromise = request("/api/creation-requests", {
        method: "POST",
        body: JSON.stringify({
          goal: params.goal,
          request_key: turnId, assistant_turn_id: turnId,
          ...(params.name ? { name: params.name } : {}),
          target_count: params.target_count ?? 1,
          output_relation: params.output_relation ?? null,
          material_scope: params.material_scope ?? "workspace_library",
          language: params.language || "zh-CN",
          aspect_ratio: params.aspect_ratio || "9:16",
          duration_target_seconds: params.duration_target_seconds || 60,
          ...((params.presenter_share_target !== undefined || !params.template_id)
            ? { presenter_share_target: params.presenter_share_target ?? 20 } : {}),
          ...((params.lipsync_quality !== undefined || !params.template_id)
            ? { lipsync_quality: params.lipsync_quality ?? "high" } : {}),
          ...(params.template_id ? { template_id: params.template_id, template_version: params.template_version } : {}),
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
    description: "将本轮已保存且条件明确的制作目标展开为当前至多8条具体方案供用户检查。不同主题、版本或系列需有各自具体目标；仅保存待确认方案，不确认、不生成成片。总数量与本组数量分开。",
    parameters: { type: "object", properties: {
      goals: { type: "array", minItems: 1, maxItems: 8, items: { type: "object", properties: {
        goal: { type: "string", minLength: 1, maxLength: 2000 }, name: { type: "string", minLength: 1, maxLength: 120 },
      }, required: ["goal"], additionalProperties: false } },
    }, required: ["goals"], additionalProperties: false },
    execute: async (_id: string, params: {goals: {goal: string; name?: string}[]}) => {
      if (parentCreationPromise) {
        try { await parentCreationPromise; }
        catch (cause) { throw new Error("本轮完整目标保存失败，未展开方案；请查看目标保存的实际原因", { cause }); }
      }
      const boundRequestId = readActionSource().creationRequestId;
      if (!creationRequest && boundRequestId) {
        const saved = await request(`/api/creation-requests/${encodeURIComponent(boundRequestId)}`);
        if (saved.id !== boundRequestId) throw new Error("回读目标与本轮已保存请求不一致，没有展开任务");
        creationRequest = saved;
      }
      if (!creationRequest || creationRequest.clarification_questions.length) throw new Error("先保存本次完整目标并确认重要条件，没有展开任务");
      if (creationRequest.current_window?.status === "proposed") throw new Error("当前这组方案尚待用户确认，没有另建一组");
      creationRequest = await request(`/api/creation-requests/${encodeURIComponent(creationRequest.id)}/windows`, {
        method: "POST", body: JSON.stringify({expected_revision: creationRequest.revision, goals: params.goals}),
      });
      return { content: [{type:"text", text:JSON.stringify({
        target_count:creationRequest.target_count, proposed_count:creationRequest.current_window.entries.length,
        goals:creationRequest.current_window.entries.map((entry: any)=>({ordinal:entry.ordinal,
          goal:entry.plan.goal, name:entry.plan.name, blockers:entry.plan.blockers, confirmable:entry.plan.confirmable})),
        instruction:"这组方案已保存，尚未创建视频任务或成片。请用户检查并确认；总目标其余部分还未规划。",
      })}], details: {kind:"plan_preview", preview:{kind:"creation_request",request:creationRequest}} };
    },
  });

}
