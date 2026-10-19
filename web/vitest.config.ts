import { defineConfig } from "vitest/config";
import { fileURLToPath, URL } from "node:url";

// Web tests (bun run test). Node environment: the checks scan source and build output, test pure helpers, and
// server-render v3 views against the Python-made export (test/python-export.test.tsx).
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  esbuild: { jsx: "automatic" },
  test: { include: ["test/**/*.test.ts", "test/**/*.test.tsx"], environment: "node" },
});
