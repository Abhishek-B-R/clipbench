# Decisions

One line each: what was decided and why. Newest at the bottom of each section.

## Setup

- Language for the first verified set: `ja`. The build prompt left it unfilled; Japanese is what the specified stack (fugashi, unidic-lite, pykakasi) is built for, and it has the most short clips with info.json in Vako.
- Budget cap: 10 USD (the example value in the prompt, left unfilled). Change `budget_usd` in clipbench.toml.
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

- Rows store the list cost even on a cache hit, and the original call's latency; data/spend.json records only real requests. Cost per hour and latency stay comparable across reruns.
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
