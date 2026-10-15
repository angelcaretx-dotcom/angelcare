import { fileURLToPath } from "node:url";
import { defineConfig } from "vitest/config";

// Checks against the prerendered HTML from `next build` (ADR 0022).
// Run `npm run build` first; `npm run test:build` fails loudly if the
// build output is missing rather than passing vacuously.
export default defineConfig({
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "node",
    include: ["tests/build/**/*.test.ts"],
  },
});
