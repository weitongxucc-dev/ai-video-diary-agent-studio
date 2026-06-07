import { chmod, mkdir, writeFile } from "node:fs/promises";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const scriptDir = path.dirname(fileURLToPath(import.meta.url));
const desktopDir = path.resolve(scriptDir, "..");
const shimDir = path.join(desktopDir, ".desktop-bin");
const shimPath = path.join(shimDir, "pnpm");

await mkdir(shimDir, { recursive: true });
await writeFile(
  shimPath,
  "#!/usr/bin/env sh\nexec corepack pnpm \"$@\"\n",
  "utf8",
);
await chmod(shimPath, 0o755);

const [command, ...args] = process.argv.slice(2);
if (!command) {
  console.log(shimDir);
  process.exit(0);
}

const child = spawn(command, args, {
  cwd: desktopDir,
  env: {
    ...process.env,
    PATH: `${shimDir}${path.delimiter}${process.env.PATH ?? ""}`,
  },
  stdio: "inherit",
});

child.on("exit", (code, signal) => {
  if (signal) {
    process.kill(process.pid, signal);
    return;
  }
  process.exit(code ?? 0);
});

child.on("error", (error) => {
  console.error(error.message);
  process.exit(1);
});
