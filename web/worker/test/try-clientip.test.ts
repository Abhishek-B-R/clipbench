import { env, SELF } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { clientIp, viaProxy } from "../src/http";

const req = (h: Record<string, string>) => new Request("https://clipbench.abhishekbr.workers.dev/api/v1/runs", { headers: h });
const withKey = { ...env, PROXY_KEY: "shared-proxy-key" } as Env;

/** clientIp() feeds every per-IP limit: /api/try and the /api/v1 rate limiter (routes/data.ts). */
describe("clientIp", () => {
  it("uses cf-connecting-ip by default", async () => {
    expect(await clientIp(req({ "cf-connecting-ip": "198.51.100.7" }), withKey)).toBe("198.51.100.7");
    expect(await clientIp(req({}), withKey)).toBe("unknown");
  });

  it("ignores a spoofed x-clipbench-client-ip without the proxy key", async () => {
    expect(await clientIp(req({ "cf-connecting-ip": "198.51.100.7", "x-clipbench-client-ip": "203.0.113.1" }), withKey)).toBe("198.51.100.7");
    expect(await clientIp(req({ "cf-connecting-ip": "198.51.100.7", "x-clipbench-client-ip": "203.0.113.1", "x-clipbench-proxy-key": "guess" }), withKey)).toBe("198.51.100.7");
  });

  it("never trusts the header when PROXY_KEY is unset, even with an empty key sent", async () => {
    const noKey = { ...env, PROXY_KEY: "" } as Env;
    expect(await clientIp(req({ "cf-connecting-ip": "198.51.100.7", "x-clipbench-client-ip": "203.0.113.1", "x-clipbench-proxy-key": "" }), noKey)).toBe("198.51.100.7");
    expect(await viaProxy(req({ "x-clipbench-proxy-key": "" }), noKey)).toBe(false);
  });

  it("uses the forwarded visitor IP with the right key", async () => {
    expect(await clientIp(req({ "cf-connecting-ip": "192.0.2.1", "x-clipbench-client-ip": "203.0.113.1", "x-clipbench-proxy-key": "shared-proxy-key" }), withKey)).toBe("203.0.113.1");
  });

  it("the public data API limit ignores a spoofed header without the key", async () => {
    const statuses: number[] = [];
    for (let i = 0; i < 32; i++) {
      const res = await SELF.fetch("http://localhost:8787/api/v1/runs", { headers: { "cf-connecting-ip": "198.51.100.250", "x-clipbench-client-ip": `203.0.113.${i}` } });
      statuses.push(res.status);
      await res.body?.cancel();
    }
    expect(statuses).toContain(429);
  });
});
