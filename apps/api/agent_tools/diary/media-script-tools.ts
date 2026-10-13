import type {BusinessRequest, ToolRegistrar, TaskVersion, ActionSource} from './contracts.ts';
import {modelParameters, isHostEditField} from './schemas.ts';
import {BusinessRequestError, RequestNotSubmitted} from './transport.ts';
import {boundedText} from '../task_facts.ts';

export function registerMediaScriptTools(pi: ToolRegistrar, {request, taskId, selectedTaskVersion, readMediaScriptCapabilities, getMediaScriptCapabilities, isTaskRead, readActionSource, typedRequestId, hasCreationAction, hasEditControlWrite, markPreview}: {
  request: BusinessRequest; taskId: string | undefined; selectedTaskVersion: TaskVersion | null; readMediaScriptCapabilities: () => any; getMediaScriptCapabilities: () => any; isTaskRead: () => boolean; readActionSource: () => ActionSource; typedRequestId: (turn: string, call: string) => string; hasCreationAction: () => boolean; hasEditControlWrite: () => boolean; markPreview: () => void;
}) {
    pi.registerTool({
      name: "read_media_script_capabilities", label: "读取图片程序能力",
      description: "读取真实Python/Pillow单PNG能力、schema、工具链和预算缺口；codemode返回结构化对象，直接读schema/available，不JSON.parse对象。不可用时说明实际reason，不安装或猜路径。",
      parameters: {type: "object", properties: {}, additionalProperties: false},
      execute: async () => {const details = await readMediaScriptCapabilities();
        return {content: [{type: "text", text: JSON.stringify(details)}], details};},
    });
    pi.registerTool({
      name: "read_task_graphic_sources", label: "读取当前任务图片来源",
      description: "只读取本任务已成功登记的可编辑PNG来源和输入修订；codemode返回结构化对象，直接读items，不JSON.parse对象。明确复用指定已有图片时使用该真实source_ref和修订，不另生成同名替代；缺失或同名歧义先说明或询问。不读取图片文件，不把模型自报来源当有效数据。",
      parameters: {type: "object", properties: {offset: {type: "integer", minimum: 0}}, additionalProperties: false},
      execute: async (_id: string, params: {offset?: number} = {}) => {
        const offset = params.offset ?? 0;
        if (!Number.isInteger(offset) || offset < 0) throw new Error("图片来源读取位置须为非负整数");
        const sources = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/graphics/sources`);
        if (!Array.isArray(sources)) throw new Error("图片来源列表不可读");
        const items = sources.slice(offset, offset + 24).map(source => Object.fromEntries(
          ["source_ref", "artifact_id", "sha256", "size_bytes", "name", "width", "height"].map(key => [key, source[key]])));
        const details = {items, total_count: sources.length, offset, truncated: offset > 0 || offset + 24 < sources.length};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
    pi.registerTool({
      name: "read_media_script_request", label: "回读原图片程序请求",
      description: "仅GET原phase=script请求回执，不重POST、不换绑新版。recorded是已存受理回执，不表示Job成功或图层已应用。",
      parameters: {type: "object", properties: {request_id: {type: "string", format: "uuid"}}, required: ["request_id"], additionalProperties: false},
      execute: async (_id: string, params: {request_id: string}) => {
        const query = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${encodeURIComponent(params.request_id)}?phase=script`;
        const receipt = await request(query);
        if (receipt.request_id !== params.request_id || receipt.phase !== "script") throw new Error("程序请求回执身份不一致");
        const details = receipt.status === "recorded" && receipt.result?.job_id
          ? {kind: "task_media_job", media_job: {...receipt.result, request_id: params.request_id, request_query_url: query}}
          : {kind: "edit_request_status", task_id: taskId, phase: "script", request_id: params.request_id,
            query_url: query, status: receipt.status, input_digest: receipt.input_digest, submitted_input: receipt.submitted_input, result: receipt.result};
        return {content: [{type: "text", text: JSON.stringify(details)}], details};
      },
    });
    if (getMediaScriptCapabilities()) {
      const parameters = modelParameters(getMediaScriptCapabilities().schema);
      let submitted = false;
      pi.registerTool({
        name: "preview_media_script", label: "创建可编辑图片来源",
        description: "提交API真实声明的Python/Pillow程序，只产生一个PNG来源Job；不应用、不出片。parameters是API parameters参数对象；inputs[name]按input_refs的name映射授权PNG读取路径字符串，用Image.open(inputs[name])读取。outputs[name]按requested_outputs的name映射宿主提供的可信输出文件路径字符串，用Pillow图像对象的image.save(outputs['name.png'])写声明PNG；name.png须与声明名称一致。不要将outputs[name]替换成bytes/BytesIO，不自行新增路径或输出名称；0输入可绘图。所有文件/网络/进程及预算边界由普通API/运行时校验，不支持video/audio脚本。先读取真实能力和输入来源；参数不能覆盖宿主身份、版本或运行时路径。",
        parameters,
        execute: async (_id: string, params: Record<string, unknown>) => {
          if (!isTaskRead() || !selectedTaskVersion) throw new Error("请先读取可信任务与版本");
          if (!getMediaScriptCapabilities().available) throw new Error(getMediaScriptCapabilities().reason || "程序工具链或专用预算不可用");
          if (Object.keys(params).some(key => isHostEditField(key) || !Object.hasOwn(parameters.properties, key)))
            throw new Error("程序参数包含未知或可信宿主字段，未提交");
          if (submitted || hasCreationAction() || hasEditControlWrite()) throw new Error("本轮程序请求已提交或已有其他执行动作，不重复执行");
          const turn = readActionSource().turnId;
          if (!turn || !_id || !readActionSource().userInstruction) throw new Error("程序请求来源未绑定");
          submitted = true;
          markPreview();
          const requestId = typedRequestId(turn, _id);
          const query = `/api/creation-tasks/${encodeURIComponent(taskId!)}/assistant/edit-requests/${requestId}?phase=script`;
          try {
            const job = await request(`/api/creation-tasks/${encodeURIComponent(taskId!)}/media-scripts/preview`, {method: "POST",
              body: JSON.stringify({...params, request_id: requestId, assistant_turn_id: turn,
                expected_timeline_version_id: selectedTaskVersion.timeline_version_id, expected_timeline_sequence: selectedTaskVersion.timeline_sequence})});
            if (!job.job_id || job.result_kind !== "graphic_source") throw new Error("程序未返回可核验来源作业，结果未知");
            const details = {kind: "task_media_job", media_job: {...job, request_id: requestId, request_query_url: query},
              meaning: "已提交PNG来源Job，未应用到时间线或生成成片。"};
            return {content: [{type: "text", text: JSON.stringify(details)}], details};
          } catch (error) {
            const details = {kind: "edit_request_status", task_id: taskId, phase: "script", request_id: requestId,
              assistant_turn_id: turn, tool_call_id: _id, query_url: query,
              status: error instanceof RequestNotSubmitted ? "not_submitted" : error instanceof BusinessRequestError && error.httpStatus < 500 ? "rejected" : "result_unknown",
              base_version_id: selectedTaskVersion.timeline_version_id, base_sequence: selectedTaskVersion.timeline_sequence,
              failure_reason: boundedText(error instanceof Error ? error.message : String(error), 500),
              instruction: "只查询原程序请求，不重POST或换绑最新版本。"};
            return {isError: true, content: [{type: "text", text: JSON.stringify(details)}], details};
          }
        },
      });
    }
}
