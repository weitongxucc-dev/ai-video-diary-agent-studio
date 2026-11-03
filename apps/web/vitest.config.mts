import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  test: {
    environment: "jsdom",
    css: true,
    globals: true,
    setupFiles: ["./src/lib/__tests__/setup.ts"],
  },
  resolve: {
    alias: {
      "@": import.meta.dirname + "/src",
    },
  },
});
