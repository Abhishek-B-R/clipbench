import { defineConfig } from "vitest/config";
import { fileURLToPath, URL } from "node:url";

// Web tests (bun run test). Node environment: the checks scan source and build output, and test pure helpers.
export default defineConfig({
  resolve: { alias: { "@": fileURLToPath(new URL("./src", import.meta.url)) } },
  test: { include: ["test/**/*.test.ts"], environment: "node" },
});
