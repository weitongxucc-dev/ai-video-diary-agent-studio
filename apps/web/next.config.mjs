import { fileURLToPath } from "node:url";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  outputFileTracingRoot: fileURLToPath(new URL("../..", import.meta.url)),
};

export default nextConfig;
