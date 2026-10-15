import { fileURLToPath } from "node:url";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// Component/unit tests (ADR 0022). Checks against the real `next build`
// output live in vitest.build.config.mts and run after a build.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) },
  },
  test: {
    environment: "jsdom",
    include: ["src/**/*.test.{ts,tsx}"],
    // Texas business: date/time handling is asserted in Central time.
    env: { TZ: "America/Chicago" },
  },
});
