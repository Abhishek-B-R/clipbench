# Web app contract

`web/` is the public product: a landing page, the results dashboard, and signed-in features.
One Cloudflare Worker serves the built SPA as static assets and the API under `/api/*`.
The old static report (`clipbench report` -> `site/index.html`) stays as a zero-dependency fallback.

## Layout and ownership

| path | what | owner |
|---|---|---|
| `web/package.json`, `web/vite.config.ts`, `web/index.html`, `web/src/**`, `web/public/**` (except `public/data`) | React SPA | frontend |
| `web/worker/**`, `web/wrangler.jsonc`, `web/migrations/**` | Worker API, auth, D1 | backend |
| `clipbench/report/export.py`, `web/public/data/**` (generated) | run data as JSON | backend |

Stack: bun, Vite, React 19, TypeScript strict, Tailwind v4, React Router. Worker: Hono, Better Auth (D1 via Kysely/D1 adapter), D1. One `wrangler.jsonc` with `assets` pointing at `dist/` and `run_worker_first` for `/api/*`.

## Run data (`web/public/data/`)

Written by `clipbench export <run_id|latest>`; the publish check scans it like site/.

- `index.json`: `{"latest": run_id, "runs": [{"run_id", "created_at", "language", "tiers": [...], "n_clips": {tier: n}, "providers": [ids]}]}`
- `runs/<run_id>.json`:

```json
{
  "run_id": "...", "created_at": "...", "language": "ja", "git_commit": "...", "sample_seed": 7,
  "metrics": [{"name": "cer", "primary": true, "label": "Character error rate"}, {"name": "cer_reading", "primary": false, "label": "Reading-normalised CER"}],
  "providers": [{"id": "deepgram", "model_id": "nova-3", "label": "Deepgram", "pricing_checked_on": "2026-10-19", "usd_per_hour": 0.258, "paid": true, "uploads_audio": true}],
  "aggregates": [ /* rows of aggregates.parquet as objects */ ],
  "paired": [ /* rows of paired.parquet */ ],
  "stability": [ /* rows of stability.parquet */ ],
  "findings": [{"tier": "verified", "text": "DRAFT: verify. ...", "source": "aggregates.parquet, tier=..."}],
  "errors": {"providers": {"<id>": {"top_substitutions": [["ref", "hyp", 3]], "worst_clips": [{"video_id": "...", "error_rate": 0.4, "others_median": 0.1}]}}, "examples": [{"provider": "...", "video_id": "...", "ref_snippet": "...", "hyp_snippet": "..."}]},
  "clips": [{"video_id": "...", "tier": "verified", "duration_s": 31.2, "channel": "...", "tags": ["background_music"]}],
  "per_clip": [{"provider": "...", "video_id": "...", "tier": "verified", "metric": "cer", "error_rate": 0.12, "hallucination_rate": 0.0, "latency_ms": 900}],
  "limitations": ["..."]
}
```

Never caption text beyond error snippets. Video ids are fine (they are how anyone rebuilds the set).
`web/public/data/sample/` holds a SYNTHETIC demo run (fake providers named like real ones is NOT allowed: use "Model A", "Model B"...) so the frontend works before a real run exists; the UI shows a "demo data" banner when `index.json` has `"demo": true`.

## API (`/api/*`, Worker)

- `GET|POST /api/auth/*`: Better Auth (GitHub OAuth, Google OAuth, email magic link via Resend). Session cookie, httpOnly, SameSite=Lax.
- `GET /api/me` -> `{user: {id, name, email, image} | null}`
- Watchlist: `GET /api/watchlist`, `PUT /api/watchlist/:providerId`, `DELETE /api/watchlist/:providerId`. Signed in.
- Alerts: `GET|PUT /api/alerts` `{email_on_new_run: bool, email_on_rank_change: bool}`. Signed in.
- Saved comparisons: `GET /api/comparisons`, `POST /api/comparisons` `{name, providers[], tier, metric, tags[]}`, `DELETE /api/comparisons/:id`. Signed in. Shareable read: `GET /api/comparisons/:id/public` when `public=true`.
- Requests board: `GET /api/requests` (public, sorted by votes), `POST /api/requests` `{kind: "provider"|"language", title, body}` (signed in), `POST /api/requests/:id/vote` toggles (signed in, one per user).
- API keys: `GET /api/keys`, `POST /api/keys` -> shows the secret once, stored hashed; `DELETE /api/keys/:id`. Signed in.
- Public data API: `GET /api/v1/runs`, `GET /api/v1/runs/:runId` serving the export JSON; `Authorization: Bearer <key>` raises the rate limit (anonymous gets a low limit). Rate limit with the Workers rate limiting binding or a D1 counter.
- Errors: JSON `{error: {code, message}}`. Zod validation on every body. CSRF: Better Auth origin check plus same-origin for mutating routes.

Env/secrets (`.dev.vars` locally, `wrangler secret` in prod; `.dev.vars.example` committed): `BETTER_AUTH_SECRET`, `BETTER_AUTH_URL`, `GITHUB_CLIENT_ID/SECRET`, `GOOGLE_CLIENT_ID/SECRET`, `RESEND_API_KEY`, `EMAIL_FROM`.
Locally, a provider with no credentials is hidden from the sign-in page, and magic links are printed to the worker log when `RESEND_API_KEY` is unset.

## Pages (frontend)

- `/` landing (the showpiece), `/leaderboard` (dashboard), `/providers/:id`, `/compare` (pick providers, tier, metric, clip types; shareable URL; save when signed in), `/picker` ("which model for my clips": language, clip types, budget per hour, latency need -> ranked recommendation with the paired-test caveat), `/clips` (browse clip ids with tags, link to YouTube), `/methodology`, `/requests`, `/changelog` (runs over time), `/sign-in`, `/account` (profile, watchlist, alerts, saved comparisons, API keys), `/docs/api`, 404.
