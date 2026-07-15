import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    // e2e/ is a separate Playwright suite (playwright.config.ts) driving a
    // real browser against mocked network calls -- it must never be picked
    // up by vitest's own *.spec.ts glob. Vitest's `exclude` option replaces
    // (rather than extends) its built-in defaults, so the standard defaults
    // are repeated here alongside the new e2e/ entry.
    exclude: [
      "**/node_modules/**",
      "**/dist/**",
      "**/cypress/**",
      "**/.{idea,git,cache,output,temp}/**",
      "**/{karma,rollup,webpack,vite,vitest,jest,ava,babel,nyc,cypress,tsup,build}.config.*",
      "e2e/**",
    ],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
});
