import path from "node:path";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

const here = path.dirname(new URL(import.meta.url).pathname);

export default defineConfig(async () => {
  const migrations = await readD1Migrations(path.join(here, "../migrations"));
  return {
    plugins: [
      cloudflareTest({
        wrangler: { configPath: path.join(here, "../wrangler.jsonc") },
        miniflare: {
          // Test-only values. Not secrets.
          bindings: {
            TEST_MIGRATIONS: migrations,
            BETTER_AUTH_SECRET: "q7Vd2LkX9pRz4WmN8sTb3HcJ6yFa1Ue5GiKo0Qw",
            BETTER_AUTH_URL: "http://localhost:8787",
            GITHUB_CLIENT_ID: "test-github-id",
            GITHUB_CLIENT_SECRET: "test-github-secret",
            GOOGLE_CLIENT_ID: "",
            GOOGLE_CLIENT_SECRET: "",
            RESEND_API_KEY: "",
            EMAIL_FROM: "",
            ADMIN_TOKEN: "test-admin-token",
            TRUSTED_ORIGINS: "",
            AUTO_NOTIFY: "false",
            ACCOUNTS: "true",
          },
          // Fixture run data instead of the real dist/ build.
          assets: {
            directory: path.join(here, "test/fixtures/assets"),
            binding: "ASSETS",
            routerConfig: { has_user_worker: true, invoke_user_worker_ahead_of_assets: false },
            assetConfig: { not_found_handling: "single-page-application" },
          },
        },
      }),
    ],
    test: {
      setupFiles: ["./test/setup.ts"],
    },
  };
});
