/** The API paths the site documents and embeds (src/lib/config.ts API_V1) are the ones the Worker serves. */
import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { API_V1, badgeSnippets } from "@/lib/config";

const routes = readFileSync(join(__dirname, "..", "worker", "src", "routes", "results.ts"), "utf8");
const data = readFileSync(join(__dirname, "..", "worker", "src", "routes", "data.ts"), "utf8");

describe("API_V1 matches web/worker/src/routes", () => {
  it.each([
    ["/leaderboard/:lang", API_V1.leaderboard("ja"), /^\/api\/v1\/leaderboard\/ja$/],
    ["/models/:provider", API_V1.model("deepgram"), /^\/api\/v1\/models\/deepgram$/],
    ["/history/:lang", API_V1.history("ja"), /^\/api\/v1\/history\/ja$/],
    ["/drift/:lang", API_V1.drift("ja"), /^\/api\/v1\/drift\/ja$/],
    ["/reliability/:lang", API_V1.reliability("ja"), /^\/api\/v1\/reliability\/ja$/],
    ["/badge/:provider/:file", API_V1.badge("deepgram", "ja"), /^\/api\/v1\/badge\/deepgram\/ja\.svg$/],
  ])("%s", (route, path, shape) => {
    expect(routes).toContain(`results.get("${route}"`);
    expect(path).toMatch(shape);
  });

  it("runs routes and the badge snippet", () => {
    expect(data).toContain('data.get("/runs"');
    expect(data).toContain('data.get("/runs/:runId"');
    const b = badgeSnippets("https://x.test", "deepgram", "ja", "Deepgram");
    expect(b.markdown).toBe("[![clipbench: Deepgram, ja](https://x.test/api/v1/badge/deepgram/ja.svg)](https://x.test/providers/deepgram)");
  });
});
