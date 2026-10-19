# v3 ops: canaries and drift, reliability, submissions, public API

Ops features. Covers A3, B19, A11 and A12 of docs/FEATURES-PROMPT.md. Decisions are logged at the
end of each section (this file stands in for DECISIONS.md entries; merge them there).

## A3 Continuous reruns and drift

**Canary set.** One fixed set of 30 clips per language, `datasets/canary/<lang>.csv`, chosen once from
the language's verified clips (with audio on disk) by `clipbench canary select --lang L`: clips are
ordered by `sha256("clipbench-canary:<sample_seed>:<clip_id>")` and taken greedily with at most 3 clips per
channel, then filled from the rest if the channel cap leaves the set short. Same verified clips and seed,
same set. The set is frozen: selecting again needs `--reselect`, and history across a reselect is not
comparable. Until 30 verified clips exist the command says so and writes nothing.

**Run.** `clipbench canary run --lang L [--providers all] [--dry-run]` runs the providers whose keys are
set on the canary clips with no cache (a canary must ask the provider again) and no stability reruns.
Rows and scores go to `results/canary/runs/<run_id>/` and `results/canary/<run_id>/` (run ids
`canary-<lang>-<YYYYMMDD-HHMM>`), apart from `results/runs/`, so a canary never becomes the `latest`
benchmark run and never enters a leaderboard.

**Money.** A monthly canary cap, `canary_budget_usd_per_month` in clipbench.toml (default 2, env
`CLIPBENCH_CANARY_BUDGET_USD`). The canary spend of a month is the sum of `data/spend.json` events whose run
id starts with `canary-` in that month; the file also keeps `canary_by_month` totals. A run is refused
when estimate plus this month's canary spend is over the cap, and the runner's guard stops mid-run at the
lower of the global `budget_usd` cap and what is left this month.

**History.** `results/history/<lang>.parquet`, append-only, one row per canary run x provider x metric:
`date, provider, model_id, provider_version, metric, error_rate, ci_low, ci_high, n, run_id` (verified tier,
whole set, corpus rate with the 95% clip bootstrap CI from scoring). A run id already in the file is never
added again and existing rows are never rewritten.

**Drift.** `results/history/drift.parquet`, rebuilt from the history on every run (`clipbench canary
drift` rebuilds it by hand). Each canary point is compared with the same provider's previous canary point
for the same metric. Events (`kind`):

- `score`: the new error rate is outside the previous run's bootstrap interval `[ci_low, ci_high]`.
- `model_id`: the model id changed.
- `provider_version`: the provider-reported version changed.

Columns: `language, provider, metric, date, run_id, kind, before, after, ci_low, ci_high,
model_id_before, model_id_after, provider_version_before, provider_version_after`. A score event is a
flag to look, not a verdict: two runs of 30 clips can differ by chance, which is why the rule uses the
interval and the page shows before, after and the interval with the date.

**Scheduler.** `.github/workflows/canary.yml`. Decision: GitHub Actions cron for the schedule, running
on the owner's Mac as a self-hosted runner (label `clipbench-canary`). Why:

- Secrets, logs, manual dispatch and artifacts already live in GitHub; a Cloudflare Cron Trigger cannot
  run Python, ffmpeg or a local Whisper.
- A canary needs the audio and the verified references. References are caption text and never leave the
  owner's machine; a GitHub hosted runner would need them shipped in (secret or encrypted file), which
  breaks the "no caption text in anything public" rule in spirit. Fetching the audio on a hosted runner
  with yt-dlp would also hit YouTube's refusals of cloud IPs and redownload the same clips every week.
- So the schedule is GitHub's, the work is the Mac's, where `data/` already holds audio, references and
  the spend ledger (repository variables `CLIPBENCH_DATA_DIR` and `CLIPBENCH_RESULTS_DIR` point at them).
  If the Mac is off, the queued job expires and the next day's check catches up (the cadence is measured
  from the last canary in the history, not from the cron).

Cadence: the cron fires daily at 03:17 UTC and `clipbench canary due` runs a language only when its last
canary is at least `canary_cadence_days` old (clipbench.toml, default 7, env
`CLIPBENCH_CANARY_CADENCE_DAYS`), so changing the cadence never needs a workflow edit. Scheduled runs are
dry runs until the repository variable `CANARY_LIVE` is `true` (owner's approval for recurring spend);
`workflow_dispatch` has `lang`, `force` and `live` inputs. The workflow never runs on pull requests, and
the repository setting "Require approval for all outside collaborators" must stay on because the runner is
self-hosted. The history parquet files (numbers only) are uploaded as an artifact.

**Export.** `clipbench.canary.export_section(run_id, settings)` returns, for the run's language:

- `history`: `{language, canary_clips, canary_sha256, cadence_days, series: {provider: [{date, model_id,
  provider_version, metric, error_rate, ci_low, ci_high, n, run_id}]}}`, or null with no history.
- `drift`: `[{language, provider, metric, date, run_id, kind, before, after, model_id_before,
  model_id_after, provider_version_before, provider_version_after, ci: [low, high]}]`, or null.

## B19 API reliability

From the numeric rows of every run (`rows.parquet`: `status, timed_out, retries, http_status,
latency_ms, cached`), per provider. Only requests that went out count: cached rows (served from the
result cache) and skipped rows (budget stop, nothing for the clip) are left out.

- `failure_rate` = errors / requests, with a 95% Wilson interval (`failure_ci_low`, `failure_ci_high`).
- `timeout_rate` = rows the runner gave up on after the job timeout / requests.
- `mean_retries`, `max_retries`: retries the adapter made (after 429, 5xx or network errors).
- `latency_p50`, `latency_p95`, `latency_p99`: milliseconds over successful requests.
- `rate_limited_share` = requests whose last HTTP status was 429 / requests. Only the last status is
  stored, so a request that hit 429 and then succeeded on retry shows up in `mean_retries`, not here.

Files: `results/<run_id>/reliability.parquet` (one row per provider for the run) and
`results/history/reliability.parquet` (per provider across every run in `results/runs/` and every canary
run in `results/canary/runs/`). `clipbench reliability [run_id|latest|all]` writes them; the export hook
and every canary run refresh them too. Columns: `provider, model_id, tier, language, slice_kind, slice,
n_runs, n_clips, n_requests, n_ok, n_errors, n_timeouts, n_rate_limited, failure_rate, failure_ci_low,
failure_ci_high, timeout_rate, mean_retries, max_retries, latency_p50, latency_p95, latency_p99,
rate_limited_share, first_run, last_run` (tier and language are null in the history file).

Decision: canary requests count towards reliability. They are real requests made the same way, and
weekly canaries are the only steady sample of a provider's uptime between full runs.

**Export.** `clipbench.scoring.reliability.export_section(run_id, settings)` returns
`reliability: {run: [rows for this run], all_runs: [rows across every run]}`, or null when there are no
runs. NaN values are null.

## A11 Open harness and submissions

`clipbench submit --adapter path/to/module.py:Class --lang L` (clipbench/submit.py, documented for
contributors in CONTRIBUTING.md):

1. **Contract** (always first, network blocked by patching socket connect, create_connection and
   getaddrinfo; the adapter file is imported under the same block): Provider subclass with a new valid
   id; attribute types; `availability()` returns `(False, msg)` with its keys unset; `settings()` is JSON
   with a stable hash; a paid adapter has a price; a valid `Result` for 2 s of synthetic tone (shape,
   audio seconds, cost, retries, HTTP status, word timings in order and inside the audio, speakers
   parallel to words); `parse_response` raises on malformed responses; with dummy keys and no network
   `transcribe` raises within 15 s (no hang, no silent empty Result, no ProviderSkip for a failure).
   Decision: API adapters expose `fixture_response` + `parse_response(raw, audio_seconds)` so the shape
   is checked offline from the same parser `transcribe` uses; offline local models are checked through
   `transcribe` itself. Missing word timings is a warning, not a failure.
2. **Dry run** on the public canary ids of L (audio the submitter fetched into data/audio): clip count,
   missing audio, audio minutes and the estimate. No request without `--run`.
3. **Run** (`--run`, submitter's own keys): refused over `--max-usd` (default 1); mid-run the next
   request is skipped once it would cross the cap.
4. **Bundle** `submissions/<name>/`: `submission.json`, `rows.csv`, `adapter.py`. Numbers only: per clip
   status, exception type (never the message, which can echo response bodies), latency, cost, retries,
   HTTP status, detected language, hypothesis length, word count and sha256 of the hypothesis.
   Decision: no error rates in bundles. The verified references are private, so a submitter cannot
   compute them and must not type them; maintainers rerun the canary set and score it.

`clipbench submission-check <dir>|--all` is what `.github/workflows/submissions.yml` runs on pull
requests (GitHub hosted, no secrets, read-only token): exactly the three files, adapter sha256 matches,
`scored` is false, no long text fields, rows are numbers and exception names, clips are in the current
canary set and `canary_sha256` matches, the summary is recomputed from the rows, and the adapter passes
the contract offline. "Reruns the canary set before merging" (brief A11) is a maintainer step with keys
(`clipbench canary run` after adding the adapter, or the canary workflow's dispatch), never PR CI.

## A12 Public results API and badges

Worker routes under `/api/v1` (web/worker/src/routes/results.ts), all read-only, computed on request
from the exported JSON under `/data` (index.json and runs/<run_id>.json). For each language the run is
the newest one with a verified tier, else the newest. A language that is not published (docs/V2.md) gets
`{language, run_id, published: false, status, message}` and no numbers from every route.

| route | returns |
|---|---|
| `GET /leaderboard/:lang?metric=&tier=verified&slice_kind=all&slice=all` | rows ranked by error rate with CI, n_clips, too_small; the run's metrics and available slices |
| `GET /models/:provider` | per language: provider meta, every aggregate of that provider, canary history, drift events, reliability across runs |
| `GET /history/:lang?provider=` | the run's `history` section |
| `GET /drift/:lang` | the run's `drift` events |
| `GET /reliability/:lang` | the run's `reliability` section (`run`, `all_runs`) |
| `GET /badge/:provider/:lang.svg?metric=` | SVG "clipbench, 6.2% CER ja" from the verified whole-set aggregate (primary metric by default); "not published ja" (200) for an unpublished language; "no data ja" (404) when the provider or metric has no number |

Caching: JSON is `Cache-Control: public, max-age=300`, badges `public, max-age=3600`; every response has a
content ETag and a matching `If-None-Match` gets an empty 304. CORS is open (GET only). Rate limits are the
existing ones (30 per minute per IP anonymous, 600 per API key), except badges. Decision: badges skip the
per-IP limit, because READMEs fetch them through shared image proxies (GitHub's camo), where one IP stands
for many viewers; they read two cached assets and are themselves cached for an hour. ACCOUNTS off is
unchanged: these routes live under `/api/v1`, which never builds Better Auth.

## Export contract (for clipbench/report/export.py)

Two hooks, both already in `V3_HOOKS`, both `export_section(run_id, settings) -> dict | None`:

- `clipbench.canary`: `history` and `drift` for the run's language (language read from
  `results/runs/<run_id>/run_meta.json`), shapes in A3 above. None when there is no canary history.
- `clipbench.scoring.reliability`: `reliability: {run, all_runs}`, shapes in B19 above. Refreshes the
  reliability parquet files as a side effect. None when there are no runs.
