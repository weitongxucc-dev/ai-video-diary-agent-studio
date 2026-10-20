import { readFile } from "node:fs/promises";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

export const PI_VERSION = "1.3.0";
const resources = dirname(fileURLToPath(import.meta.url));

/** One SDK session owns the model loop, history, compaction and input queues.
 * Product repositories retain authorization, draft CAS and operation receipts.
 * No project/user extensions, coding tools or personal context are discovered.
 */
export async function openCreationSession({ modulePath, cwd, sessionDir, sessionId,
  modelId, systemPrompt, extensionPath, tools, customTools = [], extensionFactories = [], businessCall }) {
  const packageRoot = dirname(dirname(modulePath));
  const installed = JSON.parse(await readFile(join(packageRoot, "package.json"), "utf8"));
  if (installed.version !== PI_VERSION) {
    throw new Error(`视频创作运行候选需要 Pi ${PI_VERSION}，当前为 ${installed.version}；请先验证升级兼容性。`);
  }
  const sdk = await import(pathToFileURL(modulePath).href);
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
${await readFile(join(resources,"prompts/review-result.md"),"utf8")}`,
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
    resourceLoader, noTools: "builtin", tools: [...tools, skillReader.name],
    customTools: [...customTools, skillReader],
  });
  return { ...result, resourceLoader, version: installed.version, rebindTools: productExtension?.rebind };
}
