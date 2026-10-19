import { createExecutionContext, env, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { badgeSvg, escapeXml, percent } from "../src/badge";
import { app } from "../src/index";
import { pickRun } from "../src/routes/results";
import { anon, json, ORIGIN } from "./helpers";

describe("leaderboards", () => {
  it("ranks a published language by the primary metric with CIs", async () => {
    const res = await anon("/api/v1/leaderboard/ja");
    expect(res.status).toBe(200);
    expect(res.headers.get("access-control-allow-origin")).toBe("*");
    expect(res.headers.get("cache-control")).toBe("public, max-age=300");
    const body = await json(res);
    expect(body).toMatchObject({ language: "ja", run_id: "r1", published: true, metric: "cer", tier: "verified" });
    expect(body.rows.map((r: { provider: string; rank: number }) => [r.rank, r.provider])).toEqual([
      [1, "model-a"],
      [2, "model-b"],
    ]);
    expect(body.rows[0]).toMatchObject({ label: "Model A", error_rate: 0.1, n_clips: 3 });
    expect(body.slices).toContainEqual({ slice_kind: "audio_condition", slice: "music" });
  });

  it("filters by slice and metric, and validates parameters", async () => {
    const music = await json(await anon("/api/v1/leaderboard/ja?slice_kind=audio_condition&slice=music"));
    expect(music.rows.map((r: { provider: string }) => r.provider)).toEqual(["model-b", "model-a"]);
    const reading = await json(await anon("/api/v1/leaderboard/ja?metric=cer_reading"));
    expect(reading.rows).toHaveLength(1);
    expect((await anon("/api/v1/leaderboard/ja?metric=wer")).status).toBe(400);
    expect((await anon("/api/v1/leaderboard/ja?tier=gold")).status).toBe(400);
    expect((await anon("/api/v1/leaderboard/JA")).status).toBe(400);
    expect((await anon("/api/v1/leaderboard/ko")).status).toBe(404);
  });

  it("gives an unpublished language its status and no numbers", async () => {
    const body = await json(await anon("/api/v1/leaderboard/en"));
    expect(body).toMatchObject({ language: "en", published: false, run_id: "r3-en" });
    expect(body.status).toMatchObject({ verified_clips: 12, published: false });
    expect(body.rows).toBeUndefined();
    for (const path of ["/api/v1/history/en", "/api/v1/drift/en", "/api/v1/reliability/en"]) {
      const b = await json(await anon(path));
      expect(b.published, path).toBe(false);
      expect(JSON.stringify(b), path).not.toContain("error_rate");
      expect(JSON.stringify(b), path).not.toContain("failure_rate");
    }
  });

  it("answers a matching If-None-Match with 304", async () => {
    const first = await anon("/api/v1/leaderboard/ja");
    const tag = first.headers.get("etag");
    expect(tag).toMatch(/^"[0-9a-f]{32}"$/);
    const again = await anon("/api/v1/leaderboard/ja", { headers: { "if-none-match": tag! } });
    expect(again.status).toBe(304);
    expect(await again.text()).toBe("");
  });
});

describe("per model, history, drift, reliability", () => {
  it("collects a model's metrics, history, drift and reliability across languages", async () => {
    const body = await json(await anon("/api/v1/models/model-a"));
    expect(body.provider).toBe("model-a");
    const ja = body.languages.find((l: { language: string }) => l.language === "ja");
    expect(ja.published).toBe(true);
    expect(ja.aggregates.every((a: { provider: string }) => a.provider === "model-a")).toBe(true);
    expect(ja.history).toHaveLength(2);
    expect(ja.drift.map((e: { kind: string }) => e.kind).sort()).toEqual(["provider_version", "score"]);
    expect(ja.reliability).toMatchObject({ failure_rate: 0.05, latency_p99: 2100 });
    const en = body.languages.find((l: { language: string }) => l.language === "en");
    expect(en).toEqual({ language: "en", run_id: "r3-en", published: false });
    expect((await anon("/api/v1/models/nobody")).status).toBe(404);
    expect((await anon("/api/v1/models/Bad%20Id")).status).toBe(400);
  });

  it("serves history, drift and reliability of a published language", async () => {
    const hist = await json(await anon("/api/v1/history/ja?provider=model-b"));
    expect(Object.keys(hist.history.series)).toEqual(["model-b"]);
    expect(hist.history.cadence_days).toBe(7);
    const drift = await json(await anon("/api/v1/drift/ja"));
    expect(drift.drift[0]).toMatchObject({ provider: "model-a", date: "2026-10-08", ci: [0.08, 0.12] });
    const rel = await json(await anon("/api/v1/reliability/ja"));
    expect(rel.reliability.all_runs.map((r: { provider: string }) => r.provider)).toEqual(["model-a", "model-b"]);
  });

  it("picks the newest verified run of a language", () => {
    const idx = {
      runs: [
        { run_id: "old", created_at: "2026-10-01", language: "ja", tiers: ["verified"] },
        { run_id: "creator-only", created_at: "2026-10-09", language: "ja", tiers: ["creator"] },
        { run_id: "new", created_at: "2026-10-05", language: "ja", tiers: ["verified", "creator"] },
      ],
    };
    expect(pickRun(idx, "ja")?.run_id).toBe("new");
    expect(pickRun(idx, "ko")).toBeNull();
  });
});

describe("badges", () => {
  it("renders the primary metric from data with caching headers", async () => {
    const res = await anon("/api/v1/badge/model-a/ja.svg");
    expect(res.status).toBe(200);
    expect(res.headers.get("content-type")).toBe("image/svg+xml; charset=utf-8");
    expect(res.headers.get("cache-control")).toBe("public, max-age=3600");
    expect(res.headers.get("etag")).toBeTruthy();
    const svg = await res.text();
    expect(svg).toContain(">clipbench<");
    expect(svg).toContain(">10.0% CER ja<");
    expect(svg).not.toMatch(/<script|href=/);
    const again = await anon("/api/v1/badge/model-a/ja.svg", { headers: { "if-none-match": res.headers.get("etag")! } });
    expect(again.status).toBe(304);
  });

  it("takes ?metric=, and says not published or no data", async () => {
    expect(await (await anon("/api/v1/badge/model-a/ja.svg?metric=cer_reading")).text()).toContain(">6.2% CER_READING ja<");
    const unpub = await anon("/api/v1/badge/model-a/en.svg");
    expect(unpub.status).toBe(200);
    expect(await unpub.text()).toContain(">not published en<");
    expect(await (await anon("/api/v1/badge/model-a/ko.svg")).text()).toContain(">not published ko<");
    const none = await anon("/api/v1/badge/nobody/ja.svg");
    expect(none.status).toBe(404);
    expect(await none.text()).toContain(">no data ja<");
    expect((await anon("/api/v1/badge/model-a/ja.png")).status).toBe(404);
    expect((await anon("/api/v1/badge/model-a/ja.svg?metric=%3Cx%3E")).status).toBe(400);
  });

  it("is not held to the per-IP limit (image proxies share IPs)", async () => {
    const ip = "203.0.113.99";
    let last = 0;
    for (let i = 0; i < 35; i++) last = (await anon("/api/v1/badge/model-b/ja.svg", { headers: { "cf-connecting-ip": ip } })).status;
    expect(last).toBe(200);
  });

  it("escapes text and formats percentages", () => {
    expect(escapeXml(`<a&"'>`)).toBe("&lt;a&amp;&quot;&apos;&gt;");
    expect(percent(0.0623)).toBe("6.2%");
    expect(percent(0.0001)).toBe("<0.1%");
    expect(percent(0)).toBe("0.0%");
    expect(badgeSvg("clipbench", "x<y")).toContain("x&lt;y");
    expect(badgeSvg("a", "b") + badgeSvg("clipbench", "not published")).not.toMatch(new RegExp("[\\u2013\\u2014]"));
  });
});

describe("accounts off", () => {
  it("serves the new endpoints without auth secrets", async () => {
    const offEnv = { ...env, ACCOUNTS: "false", BETTER_AUTH_SECRET: undefined, BETTER_AUTH_URL: undefined } as unknown as Env;
    for (const path of ["/api/v1/leaderboard/ja", "/api/v1/models/model-a", "/api/v1/badge/model-a/ja.svg", "/api/v1/drift/ja"]) {
      const ctx = createExecutionContext();
      const res = await app.request(`${ORIGIN}${path}`, { headers: { "cf-connecting-ip": "198.51.100.10" } }, offEnv, ctx);
      await waitOnExecutionContext(ctx);
      expect(res.status, path).toBe(200);
    }
  });
});
