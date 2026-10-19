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
  it("is plain, ranks the watchlist, and has no dashes", () => {
    const s = summarize("r1", {
      language: "ja",
      metrics: [{ name: "cer", primary: true }],
      providers: [{ id: "a", label: "Model A" }, { id: "b", label: "Model B" }],
      aggregates: [
        { provider: "a", tier: "verified", metric: "cer", slice_kind: "all", error_rate: 0.2 },
        { provider: "b", tier: "verified", metric: "cer", slice_kind: "all", error_rate: 0.1 },
      ],
    });
    const mail = newRunEmail(s, ["a"], "https://clipbench.example");
    expect(mail.text).toContain("#2 Model A: 20.0%");
    expect(mail.text).toContain("https://clipbench.example/leaderboard?lang=ja");
    expect(mail.html).toContain("Model A");
    expect(mail.html).not.toMatch(/<script|<img/);
    expect(mail.text + mail.html).not.toMatch(/[\u2013\u2014]/);
  });
});
