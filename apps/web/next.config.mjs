import { fileURLToPath } from "node:url";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: fileURLToPath(new URL("../..", import.meta.url)),
  // Keep Windows installer builds within the bounded local process/memory budget.
  ...(process.platform === "win32" ? { experimental: { cpus: 2 } } : {}),
};

export default nextConfig;
