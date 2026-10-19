import { betterAuth } from "better-auth";
import { magicLink } from "better-auth/plugins";
import { sendEmail } from "./email";
import { magicLinkEmail } from "./emails/magic-link";

export type AuthProviders = { github: boolean; google: boolean; email: boolean };

const set = (v: string | undefined): v is string => typeof v === "string" && v.trim() !== "";

export function isLocalUrl(url: string | undefined): boolean {
  if (!set(url)) return true;
  try {
    const host = new URL(url).hostname;
    return host === "localhost" || host === "127.0.0.1" || host === "[::1]" || host.endsWith(".localhost");
  } catch {
    return false;
  }
}

/** Which sign-in methods have credentials. Email works without Resend only locally (links go to the log). */
export function authProviders(env: Env): AuthProviders {
  return {
    github: set(env.GITHUB_CLIENT_ID) && set(env.GITHUB_CLIENT_SECRET),
    google: set(env.GOOGLE_CLIENT_ID) && set(env.GOOGLE_CLIENT_SECRET),
    email: set(env.RESEND_API_KEY) ? set(env.EMAIL_FROM) : isLocalUrl(env.BETTER_AUTH_URL),
  };
}

export function trustedOrigins(env: Env): string[] {
  const out = new Set<string>();
  if (set(env.BETTER_AUTH_URL)) out.add(new URL(env.BETTER_AUTH_URL).origin);
  for (const o of (env.TRUSTED_ORIGINS ?? "").split(",")) {
    if (o.trim()) out.add(o.trim().replace(/\/$/, ""));
  }
  return [...out];
}

export function createAuth(env: Env) {
  const providers = authProviders(env);
  const socialProviders: Record<string, { clientId: string; clientSecret: string; scope?: string[] }> = {};
  if (providers.github) {
    socialProviders.github = { clientId: env.GITHUB_CLIENT_ID, clientSecret: env.GITHUB_CLIENT_SECRET, scope: ["user:email"] };
  }
  if (providers.google) {
    socialProviders.google = { clientId: env.GOOGLE_CLIENT_ID, clientSecret: env.GOOGLE_CLIENT_SECRET };
  }
  const baseURL = set(env.BETTER_AUTH_URL) ? env.BETTER_AUTH_URL : "http://localhost:8787";

  return betterAuth({
    appName: "clipbench",
    baseURL,
    basePath: "/api/auth",
    secret: env.BETTER_AUTH_SECRET,
    database: env.DB,
    trustedOrigins: trustedOrigins(env),
    socialProviders,
    account: { accountLinking: { enabled: true, trustedProviders: ["github", "google"] } },
    session: { expiresIn: 60 * 60 * 24 * 30, updateAge: 60 * 60 * 24 },
    rateLimit: {
      enabled: true,
      storage: "database",
      window: 60,
      max: 60,
      customRules: { "/sign-in/magic-link": { window: 60, max: 3 } },
    },
    advanced: {
      useSecureCookies: baseURL.startsWith("https://"),
      defaultCookieAttributes: { httpOnly: true, sameSite: "lax" },
      ipAddress: { ipAddressHeaders: ["cf-connecting-ip"] },
      // The instance is built per request; the schema is checked by test/schema.test.ts instead.
      database: { validateSchema: false },
    },
    plugins: providers.email
      ? [
          magicLink({
            expiresIn: 60 * 10,
            sendMagicLink: async ({ email, url }) => {
              if (!set(env.RESEND_API_KEY)) {
                // Local development only (authProviders() hides email elsewhere when Resend is unset).
                console.log(JSON.stringify({ event: "magic_link", email, url }));
                return;
              }
              await sendEmail(env, { to: email, ...magicLinkEmail(url, baseURL) });
            },
          }),
        ]
      : [],
  });
}

export type Auth = ReturnType<typeof createAuth>;
export type SessionData = NonNullable<Awaited<ReturnType<Auth["api"]["getSession"]>>>;
