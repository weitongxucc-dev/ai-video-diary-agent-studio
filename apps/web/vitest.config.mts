import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    css: true,
    globals: true,
    // Each editor scenario mounts the full media workbench. Keep concurrent
    // jsdom instances bounded on shared CI runners instead of inflating deadlines.
    maxWorkers: 2,
    setupFiles: ["./src/lib/__tests__/setup.ts"],
  },
  resolve: {
    alias: {
      "@": import.meta.dirname + "/src",
    },
  },
});
