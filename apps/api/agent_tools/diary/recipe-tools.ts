import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {modelParameters, isHostEditField} from './schemas.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';

export function registerRecipeTools(pi: ToolRegistrar, {request, taskId, selectedTaskVersion, isTaskRead, readActionSource, typedRequestId, getScriptToolCapabilities, getDeclaredCapabilities = getScriptToolCapabilities, readMediaScriptToolCapabilities, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; isTaskRead: () => boolean; readActionSource: () => ActionSource; typedRequestId: (turn: string, call: string) => string; getScriptToolCapabilities: () => any; getDeclaredCapabilities?: () => any; readMediaScriptToolCapabilities: () => any; markPreview: () => void;
}) {
  const knownTools = new Map<string, any>();
  const describeTool = async (toolId: string, version?: number) => {
    const value = await request(`/api/media-script-tools/${encodeURIComponent(toolId)}${version === undefined ? "" : `?version=${version}`}`);
    if (value.tool_id !== toolId || !Number.isInteger(value.version)) throw new Error("配方身份或版本不可核验");
    knownTools.set(toolId, value);
    const {definition, ...metadata} = value;
    return {...metadata, input_slots: definition?.input_slots, requested_outputs: definition?.requested_outputs,
      purpose: definition?.purpose};
  };
  pi.registerTool({name: "read_media_script_tool_capabilities", label: "读取可复用图片工具能力",
    description: "读取实际保存/更新/停用/调用schema；codemode返回结构化对象，直接读schemas，不JSON.parse对象。不安装、不加载程序到宿主。",
    parameters: {type: "object", properties: {}, additionalProperties: false},
    execute: async () => {const details = await readMediaScriptToolCapabilities();
      return {content: [{type: "text", text: JSON.stringify(details)}], details};},
  });
  pi.registerTool({name: "list_media_script_tools", label: "查找可复用图片工具",
    description: "只列当前成员保存的可复用图片脚本及active事实，不是产品全部能力清单；空列表不代表内建配音等业务能力不可用。最多24项及实际truncated标记。codemode返回结构化对象，直接读items，不JSON.parse对象；发现不授予旧任务/输入权限。",
    parameters: {type: "object", properties: {include_inactive: {type: "boolean"}}, additionalProperties: false},
    execute: async (_id: string, params: {include_inactive?: boolean} = {}) => {
      const page = await request(`/api/media-script-tools${params.include_inactive ? "?include_inactive=true" : ""}`);
      if (!Array.isArray(page.items) || page.items.length > 24 || typeof page.truncated !== "boolean") throw new Error("工具列表不符合有界契约");
      for (const value of page.items) knownTools.set(value.tool_id, value);
      const details = page;
      return {content: [{type: "text", text: JSON.stringify(details)}], details};
    },
  });
  pi.registerTool({name: "describe_media_script_tool", label: "查看工具参数和版本",
    description: "用户明确给出的tool_id可直接只读GET，由后端核当前成员权限，不要求先列举。回读真实版本、参数schema、输入名槽与输出契约；codemode返回结构化对象，直接读version/parameter_schema，不JSON.parse对象。不取出程序代码供宿主执行。",
    parameters: {type: "object", properties: {tool_id: {type: "string"}, version: {type: "integer", minimum: 1}}, required: ["tool_id"], additionalProperties: false},
    execute: async (_id: string, params: {tool_id: string; version?: number}) => {
      const details = await describeTool(params.tool_id, params.version);
      return {content: [{type: "text", text: JSON.stringify(details)}], details};
    },
  });
  pi.registerTool({name: "read_media_script_tool_creation", label: "查询原工具保存结果",
    description: "只GET原创建UUID，不重复保存，不把原v1元数据冒充不同新输入的成功。",
    parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
    execute: async (_id: string, params: {request_id: string}) => {
      const value = await request(`/api/media-script-tools/requests/${encodeURIComponent(params.request_id)}`);
      if (!value.tool_id || value.version !== 1) throw new Error("原工具创建回执不可核验");
      knownTools.set(value.tool_id, value);
      const details = {kind: "media_script_tool_result", action: "query", tool: value, request_id: params.request_id};
      return {content: [{type: "text", text: JSON.stringify(details)}], details};
    },
  });
  if (getDeclaredCapabilities()) {
    const writes = new Set<string>();
    const sourceTurn = () => {
      const turn = readActionSource().turnId;
      if (!turn || !readActionSource().userInstruction) throw new Error("本轮明确要求和可信轮次未绑定，未写入工具");
      return turn;
    };
    const guardedFields = (params: Record<string, unknown>, schema: any) => {
      if (Object.keys(params).some(key => isHostEditField(key) || !Object.hasOwn(schema.properties, key)))
        throw new Error("工具参数包含未知/宿主字段，未提交");
    };
    for (const action of ["update", "revoke"] as const) {
      const parameters = modelParameters(getDeclaredCapabilities().schemas[action]);
      parameters.properties.tool_id = {type: "string"}; parameters.required.push("tool_id");
      pi.registerTool({name: `${action}_media_script_tool`, label: action === "update" ? "更新可复用工具" : "停用可复用工具",
        description: "仅按用户明确更新/停用要求提交真实CAS；先读取实际工具版本，不因程序成功自动修改。丢回应只读取原工具，不自动重复写。停用不删除既有图片/视频。",
        parameters,
        execute: async (_id: string, params: any) => {
          sourceTurn(); guardedFields(params, parameters);
          const value = knownTools.get(params.tool_id);
          if (!value || value.version !== params.expected_version) throw new Error("请先读取该工具实际CAS版本，未写入");
          if (writes.has(params.tool_id)) throw new Error("本轮已提交该工具写操作，不重复未知结果");
          writes.add(params.tool_id);
          const {tool_id, ...body} = params;
          const locator = `/api/media-script-tools/${encodeURIComponent(tool_id)}`;
          try {
            const tool = await request(locator + (action === "revoke" ? "/revoke" : ""), {method: action === "update" ? "PATCH" : "POST", body: JSON.stringify(body)});
            knownTools.set(tool_id, tool);
            const details = {kind: "media_script_tool_result", action, tool, expected_version: params.expected_version, submitted_input: body};
            return {content: [{type: "text", text: JSON.stringify(details)}], details};
          } catch (error) {
            const details = {kind: "media_script_tool_status", action, tool_id, expected_version: params.expected_version,
              status: error instanceof BusinessRequestError && error.httpStatus < 500 ? "rejected" : "unknown", query_url: locator, submitted_input: body,
              instruction: "仅回读实际HEAD；不把HEAD变化归因于本次请求，不自动重复CAS写入。",
              failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500)};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
        },
      });
    }
    if (taskId) {
      const parameters = modelParameters(getDeclaredCapabilities().schemas.save);
      let saveAttempted = false;
      pi.registerTool({name: "save_media_script_tool", label: "保存可复用图片工具",
        description: "只有用户明确要求保存时，将本任务真实成功producer Job保存成可复用工具。程序成功不自动保存；代码/定义/输出槽由API回读，不接受模型代码。创建UUID由宿主绑定，unknown仅查询原UUID。",
        parameters,
        execute: async (call: string, params: any) => {
          const turn = sourceTurn(); guardedFields(params, parameters);
          if (!isTaskRead() || !selectedTaskVersion) throw new Error("请先读取可信任务和版本");
          if (saveAttempted) throw new Error("本轮工具保存已提交，不重复未知结果");
          if (!call) throw new Error("可信工具调用身份缺失");
          saveAttempted = true;
          const request_id = typedRequestId(turn, call);
          const locator = `/api/media-script-tools/requests/${request_id}`;
          try {
            const tool = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-script-tools`, {method: "POST",
              body: JSON.stringify({...params, request_id, assistant_turn_id: turn})});
            if (!tool.tool_id || tool.version !== 1) throw new Error("工具创建未返回可核验v1，结果未知");
            knownTools.set(tool.tool_id, tool);
            const details = {kind: "media_script_tool_result", action: "save", tool, request_id, query_url: locator};
            return {content: [{type: "text", text: JSON.stringify(details)}], details};
          } catch (error) {
            const details = {kind: "media_script_tool_status", action: "save", request_id, query_url: locator,
              task_id: taskId, assistant_turn_id: turn, tool_call_id: call,
              status: error instanceof RequestNotSubmitted ? "not_submitted" : error instanceof BusinessRequestError && error.httpStatus < 500 ? "rejected" : "unknown",
              submitted_input: params, failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500)};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
        },
      });
      const invokeParameters = modelParameters(getDeclaredCapabilities().schemas.invoke);
      invokeParameters.properties.tool_id = {type: "string"}; invokeParameters.required.push("tool_id");
      let invoked = false;
      pi.registerTool({name: "preview_media_script_tool", label: "用已有工具准备图片来源",
        description: "用本轮真实回读的tool_id/version、当前任务/新输入/参数调用普通API。codemode返回结构化对象，直接读media_job，不JSON.parse对象。用户明确复用指定已有来源时，输入绑定该真实来源及修订，不重新生成同名替代；缺失或同名歧义先说明或询问。只取得graphic_source Job，不继承旧输入或权限，不自动采用或出片；代码和输出槽由服务端读取。",
        parameters: invokeParameters,
        execute: async (call: string, params: any) => {
          const turn = sourceTurn(); guardedFields(params, invokeParameters);
          if (!isTaskRead() || !selectedTaskVersion || !call) throw new Error("当前可信任务/版本/调用未绑定");
          const value = knownTools.get(params.tool_id);
          if (!value || value.version !== params.tool_version || !value.active) throw new Error("请先读取该实际active工具版本");
          if (invoked) throw new Error("本轮工具调用已提交，不重复未知结果");
          invoked = true; markPreview();
          const request_id = typedRequestId(turn, call), {tool_id, ...body} = params;
          const query_url = `/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/edit-requests/${request_id}?phase=script`;
          try {
            const job = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/media-script-tools/${encodeURIComponent(tool_id)}/preview`, {method: "POST",
              body: JSON.stringify({...body, request_id, assistant_turn_id: turn,
                expected_timeline_version_id: selectedTaskVersion.timeline_version_id, expected_timeline_sequence: selectedTaskVersion.timeline_sequence})});
            if (!job.job_id || job.result_kind !== "graphic_source") throw new Error("调用未返回可核验图片来源Job，结果未知");
            const details = {kind: "task_media_job", media_job: {...job, request_id, request_query_url: query_url}};
            return {content: [{type: "text", text: JSON.stringify(details)}], details};
          } catch (error) {
            const details = {kind: "edit_request_status", task_id: taskId, phase: "script", request_id, query_url,
              assistant_turn_id: turn, tool_call_id: call, base_version_id: selectedTaskVersion.timeline_version_id,
              base_sequence: selectedTaskVersion.timeline_sequence,
              status: error instanceof RequestNotSubmitted ? "not_submitted" : error instanceof BusinessRequestError && error.httpStatus < 500 ? "rejected" : "result_unknown",
              failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500)};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
        },
      });
    }
  }
}
