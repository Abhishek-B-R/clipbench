// clipbench.abhishekbr.com front door. abhishekbr.com lives in a different Cloudflare account from the site
// (a Worker can only claim a domain in its own account), so this Worker forwards every request to the
// site's workers.dev origin. It passes the visitor's IP with a shared secret so the site's rate limits
// apply per visitor rather than to this proxy.

interface Env {
  UPSTREAM: string; // e.g. https://clipbench.abhishekbr.workers.dev
  PROXY_KEY: string; // secret shared with the site Worker
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);
    const target = new URL(url.pathname + url.search, env.UPSTREAM);
    const headers = new Headers(request.headers);
    headers.set("x-forwarded-host", url.host);
    headers.set("x-forwarded-proto", "https");
    headers.set("x-clipbench-client-ip", request.headers.get("cf-connecting-ip") ?? "");
    headers.set("x-clipbench-proxy-key", env.PROXY_KEY);
    const hasBody = request.method !== "GET" && request.method !== "HEAD";
    const upstream = await fetch(target, {
      method: request.method,
      headers,
      body: hasBody ? request.body : undefined,
      redirect: "manual",
    });
    return new Response(upstream.body, upstream);
  },
};
