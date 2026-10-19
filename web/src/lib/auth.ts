import { createAuthClient } from "better-auth/react";
import { magicLinkClient } from "better-auth/client/plugins";

/**
 * Better Auth client against the Worker at /api/auth (docs/WEB.md, "API").
 * Sign-in methods: GitHub, Google, email magic link.
 *
 * Degrades gracefully: when /api is down (no Worker running, network error), useSession resolves with
 * no data, so every component sees a signed-out user. Never block rendering on auth.
 */
export const authClient = createAuthClient({
  baseURL: typeof window === "undefined" ? "http://localhost" : window.location.origin,
  basePath: "/api/auth",
  plugins: [magicLinkClient()],
  fetchOptions: {
    // Do not spam the console with retries when the API is not there.
    retry: 0,
  },
});

export const { useSession, signOut } = authClient;

export type SessionUser = { id: string; name: string; email: string; image?: string | null };

export async function signInSocial(provider: "github" | "google", callbackURL = "/account") {
  return authClient.signIn.social({ provider, callbackURL });
}

export async function signInMagicLink(email: string, callbackURL = "/account") {
  return authClient.signIn.magicLink({ email, callbackURL });
}
