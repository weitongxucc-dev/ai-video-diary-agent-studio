import { spawn } from "node:child_process";
import { readFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";

const repositoryRoot = fileURLToPath(new URL("../../..", import.meta.url));
const webRoot = fileURLToPath(new URL("..", import.meta.url));
const checkUrl = "http://127.0.0.1:3100/ui-preview";
const session = `phase-a-browser-${process.pid}`;
const executable = (name) =>
  process.platform === "win32" ? `${name}.cmd` : name;

function run(command, args, options = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd: repositoryRoot,
      stdio: options.capture ? ["ignore", "pipe", "pipe"] : "inherit",
    });
    let stdout = "";
    let stderr = "";
    child.stdout?.on("data", (chunk) => {
      stdout += chunk;
    });
    child.stderr?.on("data", (chunk) => {
      stderr += chunk;
    });
    child.once("error", reject);
    child.once("exit", (code, signal) => {
      if (code === 0) {
        resolve(stdout);
        return;
      }
      reject(
        new Error(
          `${command} exited with ${code ?? signal}\n${stdout}${stderr}`.trim(),
        ),
      );
    });
  });
}

async function serverIsReady() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 5_000);
  try {
    const response = await fetch(checkUrl, { signal: controller.signal });
    return response.ok;
  } catch {
    return false;
  } finally {
    clearTimeout(timeout);
  }
}

async function waitForServer(server) {
  const deadline = Date.now() + 150_000;
  while (Date.now() < deadline) {
    if (server.exitCode !== null) {
      throw new Error(`Web server exited before ${checkUrl} became ready.`);
    }
    if (await serverIsReady()) {
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 500));
  }
  throw new Error(`Timed out waiting for ${checkUrl}.`);
}

const cliArgs = [
  "--yes",
  "--prefer-offline",
  "--package",
  "@playwright/cli",
  "playwright-cli",
  "--session",
  session,
];
const playwright = (args, options) =>
  run(executable("npx"), [...cliArgs, ...args], options);

let server = null;

try {
  if (!(await serverIsReady())) {
    server = spawn(
      executable("corepack"),
      ["pnpm", "--dir", webRoot, "dev", "--port", "3100"],
      { cwd: repositoryRoot, stdio: "inherit" },
    );
    await waitForServer(server);
  }

  const browserCheck = await readFile(
    new URL("./phase-a-browser-check.js", import.meta.url),
    "utf8",
  );
  await playwright(["open", checkUrl]);
  const output = await playwright(["run-code", browserCheck], { capture: true });
  process.stdout.write(output);
} finally {
  await playwright(["close"], { capture: true }).catch(() => undefined);
  server?.kill("SIGTERM");
}
