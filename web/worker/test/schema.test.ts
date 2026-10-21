import { env } from "cloudflare:test";
import { getMigrations } from "better-auth/db/migration";
import { describe, expect, it } from "vitest";
import { createAuth } from "../src/auth";
import { newRunEmail, summarize } from "../src/emails/new-run";

describe("migrations", () => {
  it("leave nothing for Better Auth to create or add", async () => {
    const auth = createAuth(env);
    const { toBeCreated, toBeAdded } = await getMigrations(auth.options);
    expect(toBeCreated.map((t) => t.table)).toEqual([]);
    expect(toBeAdded.map((t) => t.table)).toEqual([]);
  });
});

describe("new run email", () => {
  it("is plain, ranks the watchlist from the export's ranking, and has no dashes", () => {
    const gate = { verified_clips_ok: true, providers_ok: true, diversity_ok: true };
    const s = summarize("r1", {
      language: "ja",
      metrics: [{ name: "cer", primary: true }],
      providers: [{ id: "a", label: "Model A" }, { id: "b", label: "Model B" }, { id: "c", label: "Model C" }],
      languages: [{ lang: "ja", published: true, gate, providers_eligible: ["a", "b"] }],
      aggregates: [
        { provider: "a", tier: "verified", metric: "cer", slice_kind: "all", slice: "all", error_rate: 0.2, eligible: true },
        { provider: "b", tier: "verified", metric: "cer", slice_kind: "all", slice: "all", error_rate: 0.1, eligible: true },
        { provider: "c", tier: "verified", metric: "cer", slice_kind: "all", slice: "all", error_rate: 0.05, eligible: false },
      ],
      ranking: {
        verified: {
          cer: [
            { provider: "b", rank: 1, error_rate: 0.1, ci_low: 0.08, ci_high: 0.12, n_clips: 120 },
            { provider: "a", rank: 2, error_rate: 0.2, ci_low: 0.18, ci_high: 0.22, n_clips: 120 },
          ],
        },
      },
    });
    expect(s.ranks.has("c")).toBe(false);
    expect(s.n_providers).toBe(2);
    const mail = newRunEmail(s, ["a", "c"], "https://clipbench.example");
    expect(mail.text).not.toContain("Model C");
    expect(mail.text).toContain("#2 Model A: 20.0%");
    expect(mail.text).toContain("https://clipbench.example/leaderboard?lang=ja");
    expect(mail.html).toContain("Model A");
    expect(mail.html).not.toMatch(/<script|<img/);
    expect(mail.text + mail.html).not.toMatch(/[\u2013\u2014]/);
  });

  it("ranks nothing for an export without gate fields", () => {
    const s = summarize("old", {
      language: "ja",
      metrics: [{ name: "cer", primary: true }],
      languages: [{ lang: "ja", published: true }],
      aggregates: [{ provider: "a", tier: "verified", metric: "cer", slice_kind: "all", slice: "all", error_rate: 0.2 }],
    });
    expect(s.ranks.size).toBe(0);
  });
});
