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

// 后端真实的入参校验（照 main.py）：
//   list_production_materials: limit > 100 → 422
//   search_production_material_segments: usage_role ∉ {a_roll,b_roll} → 422
// 上一包没模拟这些，工具写 ?limit=200 时桩照样 200，于是 bug 在全绿测试下溜过去。
// 下面把这两条搬到桩里：工具再写越界值，桩会像真后端一样报 422。
const ROUTE_LIMITS = { "/api/production/materials/search": 100, "/api/production/materials?": 100 };
const USAGE_ROLES = new Set(["a_roll", "b_roll"]);

function backendRejects(urlString) {
  let parsed;
  try { parsed = new URL(urlString); } catch { return null; }
  const path = parsed.pathname;
  const params = parsed.searchParams;
  const limit = params.get("limit");
  if (limit !== null) {
    const n = Number(limit);
    if (!Number.isInteger(n) || n < 1) {
      return { status: 422, detail: "素材检索 limit 必须在 1 到 100 之间" };
    }
    if (path.endsWith("/materials/search") || path.endsWith("/materials")) {
      if (n > 100) return { status: 422, detail: "limit 超出上限" };
    }
  }
  const role = params.get("usage_role");
  if (role !== null && !USAGE_ROLES.has(role)) {
    return { status: 422, detail: "invalid material usage role" };
  }
  return null;
}

function installFetchStub(routes, opts) {
  const strict = Boolean(opts && opts.strictQuery);
  globalThis.fetch = async (url) => {
    requestedUrls.push(String(url));
    const urlString = String(url);
    const rejected = backendRejects(urlString);
    if (rejected) {
      return new Response(JSON.stringify({ detail: rejected.detail }), { status: rejected.status, headers: { "Content-Type": "application/json" } });
    }
    const key = Object.keys(routes).find((k) => urlString.includes(k));
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
    // 用例可声明它期望的查询参数（__expect_query）；不匹配就抛错，绝不静默通过。
    // opt-in，不声明的用例行为不变（兼容既有测试）。
    if (strict && value && typeof value === "object" && !Array.isArray(value) && value.__expect_query) {
      const expect = value.__expect_query;
      let parsed;
      try { parsed = new URL(urlString); } catch { parsed = null; }
      const actual = Object.fromEntries(parsed ? parsed.searchParams.entries() : []);
      for (const [name, want] of Object.entries(expect)) {
        if (actual[name] !== String(want)) {
          throw new Error(
            `stub query mismatch: ${name} expected ${want} but got ${actual[name]} (url=${urlString})`,
          );
        }
      }
      const { __expect_query, ...body } = value;
      return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
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
      installFetchStub(cmd.routes || {}, cmd.options || {});
      requestedUrls = [];
      const tool = tools[cmd.tool];
      if (!tool) throw new Error("no such tool: " + cmd.tool);
      const result = await tool.execute("id", cmd.params || {});
      // requests 只是附带信息，供测试断言工具实际请求了什么 URL（例如分页
      // limit 是否越界）；call() 仍只读 result。
      process.stdout.write(JSON.stringify({ ok: true, result: (result && result.details) ?? result, requests: requestedUrls }) + "\n");
    } else if (cmd.op === "probe") {
      // 直接拿桩去请求一个 URL（不经过任何工具），用来验证「桩现在会不会报错」。
      // 上一包那个洞就是：工具写了 ?limit=200，桩却回 200，全绿溜过去。
      installFetchStub(cmd.routes || {}, cmd.options || {});
      const resp = await globalThis.fetch(cmd.url);
      const body = await resp.text();
      process.stdout.write(JSON.stringify({ ok: true, status: resp.status, body }) + "\n");
    } else {
      process.stdout.write(JSON.stringify({ ok: false, error: "unknown op" }) + "\n");
    }
  } catch (e) {
    process.stdout.write(JSON.stringify({ ok: false, error: String((e && e.message) || e) }) + "\n");
  }
}
