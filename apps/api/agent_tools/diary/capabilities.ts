import type {BusinessRequest} from './contracts.ts';

export function createCapabilities({request, taskId, verifyTaskVersion, initialEditCapabilities, initialMediaScriptCapabilities, initialScriptToolCapabilities}: {
  request: BusinessRequest; taskId?: string; verifyTaskVersion: (workspace: any) => void;
  initialEditCapabilities: any; initialMediaScriptCapabilities: any; initialScriptToolCapabilities: any;
}) {
  let editCapabilities = initialEditCapabilities;
  let mediaScriptCapabilities = initialMediaScriptCapabilities;
  let scriptToolCapabilities = initialScriptToolCapabilities;
  async function readMediaScriptToolCapabilities() {
    const value = await request("/api/media-script-tools/capabilities");
    if (!["save", "update", "revoke", "invoke"].every(name => value.schemas?.[name]?.type === "object" && value.schemas[name].properties)
        || value.invocation_phase !== "script" || value.result_kind !== "graphic_source") throw new Error("配方能力声明不可读");
    scriptToolCapabilities = value;
    return value;
  }

  async function readTaskEditCapabilities() {
    if (!taskId) throw new Error("当前创作任务不可用");
    const value = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/edit-capabilities`);
    if (value.task_id !== taskId || value.schema?.type !== "object" || !value.schema.properties
        || !Array.isArray(value.supported_operations)) throw new Error("任务编辑能力声明不可读");
    verifyTaskVersion({task: {current_timeline_version_id: value.timeline_version_id, timeline_sequence: value.timeline_sequence}});
    editCapabilities = value;
    return value;
  }

  async function readMediaScriptCapabilities() {
    if (!taskId) throw new Error("当前任务不可用");
    const value = await request(`/api/creation-tasks/${encodeURIComponent(taskId)}/assistant/media-script-capabilities`);
    if (value.task_id !== taskId || value.schema?.type !== "object" || !value.schema.properties || typeof value.available !== "boolean")
      throw new Error("媒体脚本能力声明不可读");
    mediaScriptCapabilities = value;
    return value;
  }

  return {readTaskEditCapabilities, readMediaScriptCapabilities, readMediaScriptToolCapabilities,
    getEditCapabilities: () => editCapabilities, getMediaScriptCapabilities: () => mediaScriptCapabilities,
    getScriptToolCapabilities: () => scriptToolCapabilities};
}
