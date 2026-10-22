# Decisions

One line each: what was decided and why. Newest at the bottom of each section.

## Setup

- Language for the first verified set: `ja`. Japanese is what the specified stack (fugashi, unidic-lite, pykakasi) is built for, and it has the most short clips with info.json in Vako.
- Budget cap: 10 USD to start. Change `budget_usd` in clipbench.toml.
- Settings live in `clipbench.toml`, overridable by `CLIPBENCH_*` env vars, so tests can point all paths at a tmp dir.
- Raw per-run text (hypotheses, reference snapshot) lives in `data/runs/`, not `results/runs/`: hypotheses of a good model are nearly the creator's captions, and results/ is published. `results/runs/<run_id>/` keeps only numbers.
- CLI is one typer app; each feature module registers its own commands so modules can be built independently.

## Review UI

- Binds 127.0.0.1 only; API docs routes are off because it is a local tool.
- A flagged (re-review) clip counts as done only if its review is newer than data/rereview.json, so an old review cannot hide a new flag.
- Skipped clips are saved with empty text and the original kept, so a creator caption can never become a verified reference by accident.
- Space types a space inside the textarea; alt+space plays/pauses anywhere and Esc leaves the textarea. Typing is the main job, so the textarea wins.
- "Save and next" jumps to the next clip that is not done, which is faster when flagged clips sit at the top.
- Unsaved edits are kept in memory per clip while navigating; only an explicit save writes a verified file.
- Tags are stored in TAGS order with duplicates removed, for stable diffs.
- Reviewer name: --reviewer, then CLIPBENCH_REVIEWER, then the OS user name.

## Scoring

- Bracketed content ([..] and (..), after NFKC) is stripped in all languages: in captions it is almost always sound tags, annotations, speaker labels or furigana.
- Punctuation is every Unicode P* and S* character. ー and 々 are letters (category Lm) and are kept.
- Punctuation becomes a space in es and ko and is deleted in ja, so "hola,amigo" does not merge into one word.
- Latin letters are lowercased in every language, since ja and ko captions contain English words.
- es `wer_no_accents` strips every combining mark, including ñ to n, so the variant isolates every diacritic-caused error.
- French (`fr`) is the sixth benchmark language, scored like Spanish: `wer` primary, `wer_no_accents` secondary (ç to c too). Hyphens split words (peut-être is two tokens) so hyphenated and spaced spellings agree, elided words are their own token (l' homme) so the space after an elision is never an error, inner apostrophes stay (aujourd'hui), œ is oe. Numbers are spelled in France French (soixante et onze, quatre vingts), not Belgian or Swiss.
- AWS French is fr-FR (France), not fr-CA; Gemini Live streaming also gets fr-FR.
- datasets/fr/channels.csv holds 36 curated, unverified French channel handles across the six categories; discovery has not been run on it.
- es "1.500" and "1,500" are read as thousands; "1" is "uno" because gender cannot be known from digits.
- ja `cer` keeps digits; `cer_reading` converts digits to readings with an in-house converter (pykakasi leaves digits alone).
- ja reading uses the unidic `kana` field (spelling based, 東京 is トウキョウ), not `pron`, because it is closer to how people write kana.
- Korean digits are not converted: Sino-Korean vs native depends on the counter.
- Headline rates are corpus rates, sum(errors) / sum(ref_len), the usual WER/CER convention; per-clip rates divide by max(ref_len, 1).
- Hallucination, timing method: a hypothesis word whose midpoint is in a reference gap over 1.0 s, or over 1.0 s outside the reference speech. Length fallback when timings are missing: max(0, hyp_len - 1.5 * ref_len); long_output means ratio > 1.5.
- Bootstrap seed is `sample_seed` from clipbench.toml, one seed for the project. Paired p = 2 x share of resamples with the opposite sign, capped at 1.
- Paired rows are ordered alphabetically (provider_a < provider_b), one stable row per pair.
- Cost per hour uses the list cost of every row, cached or not; actual money spent is tracked separately in data/spend.json.
- Clips over 90 s go in the 60-90s bucket.
- Stability disagreement is symmetric edit distance / max length between repeats, since neither repeat is the reference.

## Runner and budget

- Rows store the list cost even on a cache hit, and the original call's latency; data/spend.json records only real requests. Cost per hour stays comparable across reruns. Latency aggregates (p50, p95, RTF) leave cache hits out (2026-10-21): the first real run's cache hits came from a smoke run with ten providers uploading at once (Deepgram 38 s for a clip it does in 4.6 s alone), so their latency describes another run's load. Compute and reliability already counted only fresh requests.
- The budget ledger is shared by every clipbench process: each read-modify-write holds an fcntl lock on data/spend.json.lock, and open reservations are written into the ledger so two concurrent runs cannot both reserve up to the cap (2026-10-21). Without fcntl (Windows) only the in-process lock applies.
- Creator-tier clips have audio_condition `unknown`, not `no_music` (2026-10-21): no reviewer listened, so the first real report's "Music vs no music" table claiming all 150 clips were music-free was wrong.
- The budget guard applies to any provider with a nonzero price, not only `paid=True`, so the fake provider can test it.
- Stability reruns (20 seeded clips x 3) run only for paid providers; local and on-disk systems are deterministic or free. Each repeat has its own cache key, so rerunning a run is still free.
- `--limit` and the stability subset sort by video_id first, so they do not depend on input order.
- On resume, errors and budget skips are retried; ok rows and other skips are kept.
- A timeout on a billable request counts the reserved estimate as spent: the provider may have billed anyway.
- Unavailable providers produce no rows; they are listed in run_meta.skipped_providers.
- Whisper backend (mlx-whisper, else faster-whisper) is chosen by an actual import and is part of the cache key, since the two decode differently (greedy vs beam 5). condition_on_previous_text is off to reduce repetition loops.
- Wayin: Vako keeps Wayin output only in Postgres (`Transcript.source = 'wayin'`), nothing under .vr/. The adapter reads data/wayin/<videoId>.json, or the DB read-only only when CLIPBENCH_WAYIN_DB=1 and DATABASE_URL are set, and only whole-video transcripts (slices are rebased and do not match full audio). Otherwise it skips cleanly.

## Providers

- Model ids and prices were checked against each provider's docs on 2026-10-19; URLs are in clipbench/providers/pricing.toml and docs/providers.md.
- Groq uses whisper-large-v3, not turbo: accuracy matters more for ja/ko than the price gap ($0.111/hr).
- AssemblyAI uses universal-3-5-pro with universal-2 fallback (3.5 Pro covers ja/es, not ko); cost follows the model that actually ran.
- Training/retention opt-outs: Deepgram `mip_opt_out=true` on every request. AssemblyAI has no per-request flag, so each transcript is deleted after use; AWS S3 objects and jobs are deleted after each clip. ElevenLabs zero retention (`enable_logging=false`) is Enterprise only, so it is opt-in via CLIPBENCH_ELEVENLABS_ZERO_RETENTION=1. Gemini: use a paid-tier key (paid tier data is not used to improve products; free tier data is). Groq has no request flag (no retention by default). AWS opt-out is an Organizations policy the account owner sets.
- ElevenLabs `tag_audio_events=false`, since tags like "(laughter)" would count as insertions.
- Gemini 3.8 Flash cannot disable thinking; `thinkingLevel=low` keeps cost and latency down. Its prices double on 2027-01-01, so dated cost claims need care.
- AWS price recorded as $0.024/min (tier 1) because the official table did not render; it is the conservative figure for the budget guard. CHECK before publishing costs.
- AWS Spanish defaults to es-US, since most Spanish Shorts are Latin American.
- Latency excludes our own retry backoff and waiting for a concurrency slot, but includes upload and polling.
- Test hooks and privacy-only flags stay out of the cache key, since they do not change the output.

## Error analysis, report, publish check

- "Every provider fails" means primary error rate above 0.5 for every provider, with at least 2 providers scored, so one bad model cannot flag a reference. data/rereview.json is overwritten each run so it reflects the latest references.
- Error analysis uses the verified reference when a clip has one, otherwise the creator track.
- ja/ko substitution spans over 8 characters are dropped as garbled regions; error snippets are at most 4 words or 8 characters and always shorter than the whole reference.
- Publish check, unspaced text: 9 fugashi words compared on characters through a prefix index, since MeCab can split the same span differently in another context; with no tokenizer, more than 12 characters fails. It decodes \uXXXX escapes and HTML entities first, because json.dumps escapes Japanese by default and would hide a leak.
- Too-small slices are greyed and labelled "too small to conclude" in labels and tooltips, not just by colour.
- Zero-cost providers are plotted at one third of the cheapest paid cost with a square marker and a "$0/h" label, since a log axis cannot show 0.
- Charts and tables are made per tier, never mixed.
- The site loads vega-embed from a pinned jsdelivr version and shows a PNG until it renders, so it works offline and on Cloudflare Pages as is. `clipbench publish` runs the check and prints the wrangler command; it never deploys by itself.

## Web app

- The public frontend is a real web app in web/ (Vite, React 19, TypeScript, Tailwind v4) served by one Cloudflare Worker (Hono API, D1, Better Auth), because the owner asked for a landing page, auth and more than a static report. The static `clipbench report` site stays as a zero-dependency fallback.
- Signed-in features: provider watchlist, email alerts on new runs, saved and shareable comparisons, a vote board for new providers and languages, and API keys for the public data API. Browsing results never needs an account.
- The web app reads run results from JSON written by `clipbench export`; the publish check scans that JSON too.
- Demo data before the first real run uses "Model A".."Model F", never real provider names on fake numbers.

## Dataset

- Track kind comes only from info.json (full rule in docs/data_rules.md): `subtitles` means creator; `automatic_captions` with `-orig`, or a plain key when the info language matches, means auto; any other automatic key means auto_translation; no info.json means unknown. This mirrors Vako `chooseTrack`/`parseInfo`. A key counts only if its format list is non-empty.
- `ja-<suffix>` keys are named creator tracks: the fetched info.json lists them under `subtitles`.
- Vako has info.json for only 182 of 1344 ja videos, so `clipbench data fetch-info` fetches the missing ones with yt-dlp into data/info/ (5 s apart, resumable). It only fetches ids whose json3 suggests 95 s or less (`--all` overrides); checked against Vako's probed durations, this loses nothing.
- Fetched info.json drops formats, thumbnails and heatmap to keep files small.
- The sample visits channels round robin in a seeded order, capped at floor(0.1 * n) per channel, to spread clips across as many channels as possible.
- `load_clips("verified")` skips a clip whose verified audio_sha256 no longer matches the WAV, since the reviewer heard different audio.
- Fetchers stop on a bot check or HTTP 429 and keep going on other failures.
- Better Auth 1.7.11 runs on D1 through its built-in Kysely adapter; one auth instance per isolate, because one per request made later getSession calls hang in workerd.
- Auth schema SQL is generated by web/worker/scripts/auth-schema.mjs (the Better Auth CLI cannot reach D1) and checked by a test.
- Magic links: 3 per minute per IP, limits stored in D1 so they hold across isolates. Email sign-in is only offered when Resend is configured or the site runs on localhost, so a log-only magic link never reaches production.
- Data API rate limits: Workers Rate Limiting binding, 30/min anonymous and 600/min with a key; D1 counter as fallback.
- CSRF: mutating routes need a same-site Origin (or Sec-Fetch-Site same-origin). The admin notify route uses a Bearer ADMIN_TOKEN compared in constant time.
- API keys are `cb_` + 32 random bytes; only the SHA-256 is stored, at most 10 per user.
- `clipbench export` replaces demo data entirely when a real run exists, never mixing the two.
- Rank-change alerts are stored but not sent yet; new-run emails go out once per run via the admin route or the hourly cron when AUTO_NOTIFY is on.

## v2 reposition (contract: docs/V2.md)

- Positioned as the benchmark for captions on short-form video (timing, hallucination over music, caption readiness, code-switching, cost), because plain ASR leaderboards already exist and are better funded.
- clip_id (`<video_id>` or `<video_id>@<start_ms>-<end_ms>`) stays in the field the code calls `video_id`, with the platform id in `source_id`: the least invasive way to support windows and non-YouTube platforms.
- A language is published only at >= 100 verified clips and >= 6 providers; below that the site shows method and progress, never a ranking.
- Demo data is removed from the public site entirely (kept only for tests and dev): synthetic numbers on a benchmark site are a liability even when labelled.
- Accounts, sign-in, requests and alerts are hidden behind VITE_ACCOUNTS (off); the Worker code stays for later.
- Word timings for verified references come from forced alignment of the corrected text (`clipbench align`), since corrected text no longer matches the creator track's timings.
- Generated `site/` is gitignored; it is rebuilt from a run by `clipbench report`.
- Review UI: code-switch spans are stored as {start, end, lang} character offsets; the browser re-anchors them on every edit and the server re-checks on save, dropping (with a warning) any span whose text was replaced, so an English span overwritten in another language is never silently kept.
- Non-speech spans are clipped to the WAV length on save. Creator caption gaps of 1 s or more are offered as one-click candidate spans; the reviewer still confirms by ear.
- Category is optional when saving so review stays fast; `clipbench data check` enforces category coverage per language.
- Window clip ids use milliseconds (`<video_id>@<start_ms>-<end_ms>`), per docs/V2.md.
- The publication rule lives only in clipbench/report/rules.py, so report, site, export and OG images gate the same way. providers_ok counts only providers with ok rows on verified clips.
- Findings: comparisons only when the paired test says one model is better on at least 15 clips; "no detectable difference" is not a finding. Single-model facts need at least 15 clips with that metric; a null metric produces nothing, never 0.
- OG images are rendered from SVG with vl-convert (no new dependency), with Gloock unpacked from the site's font package.
- Export output (web/public/data, web/public/og) is gitignored: it is generated from a local run with `clipbench export` right before a deploy, so test runs never land in the repo.
- Publish check: Latin runs inside Japanese and Korean references are compared as word 9-grams, after a false positive where an English phrase in a Japanese caption matched ordinary prose letter by letter.
- Cloudflare Workers AI adds four cheap providers (cf-whisper-large-v3-turbo about $0.03/h, cf-whisper, cf-whisper-tiny-en for English, cf-deepgram-nova-3 about $0.31/h) behind one WorkersAIProvider base; Cloudflare states it does not train on or keep Workers AI inputs. Whisper models are chunked at 240 s since no size limit is documented; cost comes from neurons (10,000 free per day).

## v3 (contract: docs/V3.md)

- A "beats" claim must survive Benjamini-Hochberg (q = 0.05) across every paired test of its tier in the run, because a run makes dozens of comparisons and plain alpha 0.05 would publish false positives. The raw verdict is kept as verdict_raw.
- A comparison finding quotes only numbers measured on the clips both models scored (paired difference, its CI, n), never each model's slice-wide rate, which can cover different clips.
- Each v3 feature exposes export_section(run_id, settings); export.py merges them, keeps every v3 key present (null when absent), and a failing hook is logged and skipped so one broken feature cannot block an export.
- Run options (diarize, detect_language, keyterms) are provider options and therefore part of the cache key; rows record retries, timeouts, HTTP status and detected language for the reliability board.
- Dataset: clips.csv is the only list of which clips exist and importers only add rows; category guesses live in the manifest as category_guess, never in clips.csv. The sampler rotates categories and caps each channel at 5%, counting Shorts and windows together.
- Window audio is re-encoded at the cut (a stream copy starts early) and must be within 0.15 s of the window; windows keep a 1 s margin before the reported video duration because info.json durations run slightly long.
- Discovery stops on HTTP 429 and forgets that Short so a later run retries it. English discovery is paused on a 429 at 68 Shorts from 11 channels.
- Streaming runs are their own run kind (run_meta.kind = "stream"): `clipbench score` hands them to the streaming scorer and `latest` never resolves to one, so streaming and batch numbers never mix.
- Canary scheduler: GitHub Actions cron on a self-hosted runner on the owner's Mac, because canaries need the private references and audio and YouTube often refuses cloud IPs; scheduled runs stay dry until CANARY_LIVE=true, with a separate monthly cap (canary_budget_usd_per_month).
- Semantic WER is published only after >= 50 human-labelled pairs per language with Spearman >= 0.7 and MAE <= 0.2 against the judge; no labels exist yet and none are invented.
- Rows record which run options a provider ignored (unsupported_options), so comparisons such as keyterms on/off exclude providers that never applied them.

## Research safeguards (review fixes)

- Providers count toward publication only when eligible: ok repeat-0 results on at least `min_provider_coverage` (clipbench.toml, 0.9) of the language's verified evaluation clips. Before, any provider with one ok verified row counted toward the six, so a provider with 3 of 100 clips could unlock a ranking. Exactly at the threshold counts. Excluded providers carry their coverage and scored/skipped/errored counts as the reason.
- The setting is read in clipbench/report/rules.py (not clipbench/config.py) so the gate has one source of truth; env CLIPBENCH_MIN_PROVIDER_COVERAGE overrides it.
- `providers_ok` keeps its v2 name but now means eligible providers, so older readers of the export (web Gate, OG images) gate on the right number without a code change.
- Publication also requires the verified clips themselves to pass the diversity rules, with categories from the verified files only (guesses never count). The legacy channels/categories fields stay descriptive.
- The verified evaluation set is the union of verified repeat-0 rows in rows.parquet and verified clips in scores.parquet, so clips that every provider failed still count in the coverage denominator.
- Excluded providers are filtered out before ranking, findings and the Benjamini-Hochberg step. Exclusion depends on coverage, not results, so shrinking the test family this way keeps BH valid. Their aggregates rows are still exported with eligible=false so the site can explain the exclusion.
- The ranking order is defined once (rules.rank_order: eligible only, error rate ascending, ties by provider id) and exported as `ranking`, so the site no longer sorts on its own.
- The export's `paired` table is now the eligible-only, BH-adjusted table. Before, the web read raw unadjusted verdicts.
- Bootstrap intervals are expanded percentile intervals (Hesterberg 2015). Simulation showed the plain percentile interval called 8.5% of null datasets a win at n = 15 (findings allow slices from 15 clips); the expanded one 5.35%.
- Bootstrap p values use (k + 1) / (B + 1) and are inverted from the same expanded interval, so p <= 0.05 exactly when the CI excludes zero. The old 2k/B gave p = 0 whenever no resample crossed zero, which BH accepts at any number of tests: 7.3% of all-null runs had a "beats" against 4.0% now. Cost: the smallest p with 1000 resamples is about 0.003, which lowers BH power in runs with many tests.
- Channel-clustered bootstrap is reported alongside, never instead: in a simulation with channel-level provider effects the clip bootstrap called 11.7% of null datasets a win and the cluster bootstrap 3.7%, but with few channels the cluster interval is unstable. A "beats" whose clustered interval crosses zero is flagged cluster_sensitive and says so.
- The estimand is stated as the corpus error rate (sum of errors over sum of reference tokens); the mean of per clip error rates is reported as a secondary number.
- `clipbench data check --verified` (a small edit to clipbench/data/cli.py) runs the same verified-set check on the local sample.

## Deployment

- The site runs on a dedicated Cloudflare account (separate from the owner's personal domain) at https://clipbench.abhishekbr.workers.dev: one Worker serving the built SPA plus /api/v1, a D1 database, and the rate limiting bindings. No other backend is needed.
- The custom domain clipbench.abhishekbr.com lives in a different Cloudflare account from the site, and a Worker can only claim a domain in its own account, so a small forwarding Worker (web/proxy/) in the domain's account passes every request to the site. It forwards the visitor's IP with a shared secret (PROXY_KEY on both Workers); the site trusts that header only with the key, so per-visitor limits cannot be spoofed. Visits straight to the workers.dev origin are sent to the custom domain in the browser, which cannot loop because through the proxy the browser always sees the custom domain.
- Try it runs visitors' own clips through five low-cost models in one streamed request (a Turnstile token works once), behind Turnstile, per-visitor limits keyed by a hash of the IP, and a daily budget reserved before any call. Audio and transcripts are never stored or logged. Its latency is measured with all providers called at once and is labelled as not the benchmark latency.
- Early look shows the creator-tier run publicly, labelled as scored against unverified captions and not a ranking; the ranking gate is unchanged.
- Accounts stay off in production (ACCOUNTS=false, VITE_ACCOUNTS unset), so no auth secrets exist on the Worker.
