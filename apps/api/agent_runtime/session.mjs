import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import {createNativeTaskTools} from './native-tools.mjs';

export const PI_VERSION = "1.4.0";
const resources = dirname(fileURLToPath(import.meta.url));

/** One SDK session owns the model loop, history, compaction and input queues.
 * Product repositories retain authorization, draft CAS and operation receipts.
 * No project/user extensions or personal context are discovered. Native SDK
 * definitions are installed only with task-bound host operations, never local defaults.
 */
export async function openCreationSession({ modulePath, cwd, sessionDir, sessionId,
  modelId, systemPrompt, extensionPath, tools, customTools = [], extensionFactories = [], businessCall, nativeCall, nativeEnabled=false }) {
  const packageRoot = dirname(dirname(modulePath));
  const installed = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
  if (installed.version !== PI_VERSION) {
    throw new Error(`视频创作运行候选需要 Pi ${PI_VERSION}，当前为 ${installed.version}；请先验证升级兼容性。`);
  }
  const sdk = await import(pathToFileURL(modulePath).href);
  const nativeRuntime=nativeEnabled?createNativeTaskTools({sdk,callNative:nativeCall,
    turnId:process.env.AI_VIDEO_DIARY_AGENT_TURN_ID,inputId:process.env.AI_VIDEO_DIARY_AGENT_INPUT_ID || process.env.AI_VIDEO_DIARY_AGENT_TURN_ID}):null;
  const nativeTools=nativeRuntime?.tools ?? [];
  process.env.AI_VIDEO_DIARY_AGENT_NATIVE_TOOLS=nativeTools.map(tool=>tool.name).join(',');
  const productModule = extensionPath ? await import(pathToFileURL(extensionPath).href) : null;
  const productExtension = productModule?.createDiaryExtension(process.env, businessCall);
  const settingsManager = sdk.SettingsManager.inMemory({
    steeringMode: "one-at-a-time", followUpMode: "one-at-a-time",
    retry: { enabled: false },
  });
  const resourceLoader = new sdk.DefaultResourceLoader({
    cwd, agentDir: cwd, settingsManager,
    noExtensions: true, noSkills: true, noContextFiles: true,
    noPromptTemplates: true, noThemes: true,
    additionalSkillPaths: [join(resources, "skills")],
    additionalPromptTemplatePaths: [join(resources, "prompts")],
    extensionFactories: [...(productExtension ? [productExtension.extension] : []), ...extensionFactories],
    systemPrompt: `${systemPrompt}
创作方法按需用 read_creation_skill 读取：原声口播 source-speech、旁白配素材 narration-materials、局部修改 local-edit。
${await readFile(join(resources,"prompts/review-result.md"),"utf8")}
${nativeEnabled?'原生工具通过当前任务宿主执行。先读 open_task_working_context；文件API的 /task 是虚拟路径，Shell从work执行并使用相对路径（../inputs、../outputs），不把虚拟绝对路径当真实磁盘位置。可按真实错误改程序；产物先登记，再沿现有编辑入口预览和采用，不能把执行成功说成作品已采用。文字模型不直接接收原始图片/音视频；必要观察沿已授权局部分析取得。':''}`,
  });
  await resourceLoader.reload();
  const errors = resourceLoader.getExtensions().errors;
  if (errors.length) throw new Error(errors.map(error => error.error).join("\n"));
  const modelRuntime = await sdk.ModelRuntime.create({
    authPath: join(cwd, "auth.json"), modelsPath: join(cwd, "models.json"),
    allowModelNetwork: false,
  });
  const model = modelRuntime.getModel("ai-video-diary", modelId);
  if (!model) throw new Error("当前视频创作模型没有加载；未选择其他服务。");
  const previous = sdk.SessionManager.findById(cwd, sessionId, sessionDir);
  const sessionManager = previous
    ? sdk.SessionManager.open(previous, sessionDir, cwd)
    : sdk.SessionManager.create(cwd, sessionDir, { id: sessionId });
  const skillReader = {
    name: "read_creation_skill", label: "读取创作方法",
    description: "按名称读取本产品随版本发布的创作 Skill；不接受文件路径或用户提供的执行代码。",
    parameters: { type: "object", properties: { name: { type: "string" } }, required: ["name"], additionalProperties: false },
    execute: async (_id, { name }) => {
      const skill = resourceLoader.getSkills().skills.find(item => item.name === name);
      if (!skill) throw new Error("此创作方法未随当前版本发布。");
      return { content: [{ type: "text", text: await readFile(skill.filePath, "utf8") }], details: { name } };
    },
  };
  const result = await sdk.createAgentSession({
    cwd, agentDir: cwd, modelRuntime, model, settingsManager, sessionManager,
    resourceLoader, noTools: "builtin", tools: [...tools, skillReader.name,...nativeTools.map(tool=>tool.name)],
    customTools: [...customTools, skillReader,...nativeTools],
  });
  if (nativeRuntime) result.session.subscribe(event=>{if (event.type==='turn_start') nativeRuntime.beginRound();});
  return { ...result, resourceLoader, version: installed.version, rebindTools:async environment=>{
    await productExtension?.rebind(environment);nativeRuntime?.rebind(environment);
  }};
}
