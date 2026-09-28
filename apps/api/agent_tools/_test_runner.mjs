// 测试运行器：加载 diary_tools 扩展，用假 pi 捕获工具，用桩 fetch 驱动工具执行，
// 然后从 stdin 读 JSON 命令、往 stdout 输出 JSON 结果。由 pytest 拉起。
// 刻意不引入任何外部依赖；只用 node 内置能力。
import { createInterface } from "node:readline";

const EXT = process.env.DIARY_TOOLS_PATH;

// 收集注册的工具
const tools = {};
const fakePi = { registerTool: (t) => { tools[t.name] = t; } };

const mod = await import(EXT);
mod.default(fakePi);

let requestedUrls = [];

function installFetchStub(routes) {
  globalThis.fetch = async (url) => {
    requestedUrls.push(String(url));
    const key = Object.keys(routes).find((k) => String(url).includes(k));
    if (key === undefined) {
      return new Response(JSON.stringify({ detail: "no stub for " + url }), { status: 404, headers: { "Content-Type": "application/json" } });
    }
    const value = routes[key];
    // 路由值可以写成 { __status: 404, detail: "..." } 来模拟非 2xx（例如素材不属于
    // 当前工作区时后端就抛 404）。默认仍按 200 返回。
    if (value && typeof value === "object" && !Array.isArray(value) && "__status" in value) {
      const { __status, ...body } = value;
      return new Response(JSON.stringify(body), { status: __status, headers: { "Content-Type": "application/json" } });
    }
    return new Response(JSON.stringify(value), { status: 200, headers: { "Content-Type": "application/json" } });
  };
}

const rl = createInterface({ input: process.stdin });
for await (const line of rl) {
  if (!line.trim()) continue;
  const cmd = JSON.parse(line);
  try {
    if (cmd.op === "list") {
      process.stdout.write(JSON.stringify({ ok: true, tools: Object.keys(tools) }) + "\n");
    } else if (cmd.op === "call") {
      installFetchStub(cmd.routes || {});
      requestedUrls = [];
      const tool = tools[cmd.tool];
      if (!tool) throw new Error("no such tool: " + cmd.tool);
      const result = await tool.execute("id", cmd.params || {});
      // requests 只是附带信息，供测试断言工具实际请求了什么 URL（例如分页
      // limit 是否越界）；call() 仍只读 result。
      process.stdout.write(JSON.stringify({ ok: true, result: (result && result.details) ?? result, requests: requestedUrls }) + "\n");
    } else {
      process.stdout.write(JSON.stringify({ ok: false, error: "unknown op" }) + "\n");
    }
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }) + "\n");
  }
}
