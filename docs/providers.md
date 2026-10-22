# Providers

Every system sits behind `clipbench.providers.base.Provider`. Prices and data terms live in
`clipbench/providers/pricing.toml`; this page mirrors it. All prices are pay-as-you-go, batch
(pre-recorded) audio, USD, checked on 2026-10-19. Re-check before publishing cost results.

## Paid APIs

| provider id | model | USD / min | USD / hour | min billing | data policy and opt-out | checked |
|---|---|---|---|---|---|---|
| `groq-whisper` | `whisper-large-v3` | 0.00185 | 0.111 | 10 s per request | No retention by default; may log up to 30 days for abuse/debugging. Opt-out: Zero Data Retention in console Data Controls (no request flag). | [docs](https://console.groq.com/docs/speech-to-text), 2026-10-19 |
| `deepgram` | `nova-3` (monolingual) | 0.0043 | 0.258 | none, per second | Model Improvement Program. Opt-out: `mip_opt_out=true`, sent on every request (API reference notes pricing impacts). | [pricing](https://deepgram.com/pricing), 2026-10-19 |
| `assemblyai` | `universal-3-5-pro`, fallback `universal-2` (ko) | 0.0035 (U-2: 0.0025) | 0.21 (U-2: 0.15) | none stated | Does not train on API data today, terms allow it. Opt-out: account Data Controls toggle, paid plans only (no request flag). Adapter deletes each transcript after reading it. | [pricing](https://www.assemblyai.com/pricing), 2026-10-19 |
| `elevenlabs-scribe` | `scribe_v2` | 0.003667 | 0.22 | none stated (file at least 100 ms) | Logs and transcripts stored by default. Opt-out: `enable_logging=false` (zero retention) is Enterprise only; enable with `CLIPBENCH_ELEVENLABS_ZERO_RETENTION=1`. | [pricing](https://elevenlabs.io/pricing/api), 2026-10-19 |
| `gemini-flash` | `gemini-3.8-flash` | about 0.0034 (estimate) | about 0.20 | none (per token) | Paid tier: not used to improve products, logged for a limited time for abuse monitoring. Free tier: used to improve products. Opt-out: use a billing-enabled key (no request flag). | [pricing](https://ai.google.dev/gemini-api/docs/pricing), 2026-10-19 |
| `aws-transcribe` | standard batch | 0.024 (unverified) | 1.44 | none, per second | AWS AI services may use content for service improvement. Opt-out: AWS Organizations AI services opt-out policy (no request flag). Adapter deletes S3 objects and the job. | [pricing](https://aws.amazon.com/transcribe/pricing/), 2026-10-19 |
| `cf-whisper-large-v3-turbo` | `@cf/openai/whisper-large-v3-turbo` | 0.000513 | 0.0308 | none stated | Cloudflare does not train on or retain Workers AI inputs. Opt-out: not needed. | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), 2026-10-19 |
| `cf-whisper` | `@cf/openai/whisper` (size undocumented, auto language) | 0.000453 | 0.0272 | none stated | As above. | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), 2026-10-19 |
| `cf-whisper-tiny-en` | `@cf/openai/whisper-tiny-en` (beta, en only) | 0.000453 (unverified) | 0.0272 | none stated | As above. | [model](https://developers.cloudflare.com/workers-ai/models/whisper-tiny-en/), 2026-10-19 |
| `cf-deepgram-nova-3` | `@cf/deepgram/nova-3` (HTTP) | 0.0052 | 0.312 | none stated | As above, plus `mip_opt_out=true` on every request. | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), 2026-10-19 |

Notes:

- **Gemini** is billed per token. Audio is 32 tokens per second. The estimate is 1920 audio tokens
  plus about 100 prompt tokens at 0.75 USD per million input tokens, plus about 500 output tokens
  (transcript and low-level thinking) at 3.75 USD per million. The actual cost of each call is computed
  from `usageMetadata` in the response. 3.8 Flash prices double on 2027-01-01. Thinking cannot be
  turned off on 3.8 Flash, so the adapter asks for `thinkingLevel: low`.
- **AWS**: the official pricing page's tier table did not render when checked. Third-party sources give
  0.024 USD per minute for tier 1 in US East; the official page's batch example uses 0.006. We keep 0.024
  so the budget guard is conservative. Verify before publishing.
- **Groq**: the 10 s minimum matters for Shorts. A 5 s clip is billed as 10 s.
- **Deepgram**: Nova-3 multilingual (`language=multi`) is 0.0052 per minute; we always pass the language.
- **Cloudflare Workers AI** (`cf-*`): one account id and one API token run every model. Billed in
  neurons at 0.011 USD per 1,000, with 10,000 neurons per day free (about 214 minutes of turbo or
  21 minutes of nova-3 per day); the table shows list price without the free allocation. Cost uses
  neurons from the response if one is ever reported, else `neurons_per_minute` in `pricing.toml`.
  `cf-whisper` and `cf-whisper-tiny-en` take no language parameter (auto detect); tiny-en skips every
  language but `en`. Whisper models split audio longer than `chunk_seconds` (240) into WAV chunks;
  no request size limit is documented, so any body over 25 MB fails with a clear error.
  `@cf/deepgram/flux` is not added: WebSocket only, English only, no word timings.
  Rate limit: 720 requests per minute for speech recognition; concurrency is 8.

Language codes sent: `ja`, `es`, `ko`, `fr` everywhere except AWS (`ja-JP`, `es-US`, `ko-KR`, `fr-FR`; set
`spanish_variant="es-ES"` for Castilian Spanish) and Gemini Live streaming (`fr-FR`).

## Verification status

Nothing below has been called live yet: every adapter was built from the providers' docs and is tested only
against mocked transports (`tests/test_providers_contract.py`, `tests/test_streaming_adapters_contract.py`).
Each section of `pricing.toml` records this in four fields (checked 2026-10-21):

- `live_verified`: `false` everywhere. Set it to `true` only after a real call confirmed the request and
  response shapes, and settle the matching `unverified` items in the same edit.
- `price_status`: `documented` when the price was read from an official page (`checked_url`, `checked_on`),
  `unverified` otherwise.
- `policy_status`: `documented` when data use and training are established from an official page that
  covers this product, `unverified` when the terms are stated but the cited source does not cover this
  product, `unknown` when they are not stated.
- `unverified`: the open questions, word for word, to settle on the first live run.

Free and local systems (`whisper-local`, `whisper-local-turbo`, `youtube-auto`, `wayin`, `fake`) are
`not_applicable` and exempt.

**Fail closed.** A real run (`clipbench run`, `clipbench run-stream`) refuses a paid provider whose price is
missing, whose `price_status` is not `documented`, or whose `policy_status` is not `documented`. The provider
is skipped with `<id>: refused, <reason>; pass --allow-unverified <id> to run it anyway`, and a run where
every provider is refused does not start. `--allow-unverified groq-whisper,aws-transcribe` lets named
providers run; `run_meta.json` records the overridden reasons under `allow_unverified`, the refusals under
`verification_refused`, and each provider's `price_status`, `policy_status` and `live_verified`. A dry run
still prices every provider (including the ones only missing keys) and marks the refused ones
`would be refused: <reason>`. The gate is `clipbench/providers/verification.py`. Canary, long-form and sweep
runs go through the same runner and have no override flag, so they skip refused providers.

| provider id | price | policy | live verified | real run without override | open questions (`unverified` in pricing.toml) |
|---|---|---|---|---|---|
| `groq-whisper` | documented | **unknown** | no | refused | `verbose_json.language` for detect_language is not in the docs; training use of inputs not stated (retention only); Zero Data Retention is a console setting the run cannot confirm; keyterm prompt capped at 400 characters as a stand in for 224 tokens |
| `deepgram` | documented | documented | no | allowed | `mip_opt_out=true` "pricing impacts" not established; keyterm quality outside English untested |
| `assemblyai` | documented | documented | no | allowed | terms allow training although the FAQ says it does not happen today, opt-out is a paid plan toggle; Korean runs on the universal-2 fallback (check `speech_model_used`) |
| `elevenlabs-scribe` | documented | documented | no | allowed | stored by default and used for training unless the account toggle is off, zero retention is Enterprise only; keyterm 20% surcharge and 20 s minimum not confirmed on an invoice |
| `gemini-flash` | documented | documented | no | allowed | 0.0034 per minute is a token estimate (real cost from `usageMetadata`); 3.8 Flash prices double on 2027-01-01; thinking tokens at `low` are estimated; only a paid tier key keeps data out of training |
| `aws-transcribe` | **unverified** | documented | no | refused | 0.024 per minute is from third parties (official example uses 0.006); content used for service improvement unless the Organizations opt-out policy is attached |
| `cf-whisper-large-v3-turbo` | documented | documented | no | allowed | no neurons field in responses (cost estimated); no documented size limit (25 MB guard, 240 s chunks are ours) |
| `cf-whisper` | documented | documented | no | allowed | model size undocumented; no language parameter; neurons and size limit as above |
| `cf-whisper-tiny-en` | **unverified** | documented | no | refused | not on the pricing page, whisper rate assumed; neurons and size limit as above |
| `cf-deepgram-nova-3` | documented | documented | no | allowed | `words[].speaker`, `detected_language` and `model_info` are not in the output schema; how Deepgram's terms combine with Cloudflare's for the partner model; neurons as above |
| `deepgram-stream` | documented | documented | no | allowed | list price 0.0077 used, 0.0048 is a promotion; `mip_opt_out` pricing impact |
| `assemblyai-stream` | documented | **unverified** | no | refused | `language_codes` list format undocumented (one code sent); unknown query parameters are ignored, check the `Begin` configuration echo; no training opt-out documented for streaming; session billing estimate adds 3 s |
| `elevenlabs-stream` | documented | **unverified** | no | refused | billing basis (audio or session) not stated, audio assumed; no policy page recorded for the realtime API |
| `gemini-stream` | documented | documented | no | allowed | `x-goog-api-key` header on the WebSocket is unverified (docs show `?key=`); 0.009 per minute is a blended estimate, `usageMetadata` not priced; paid tier key needed |
| `cf-nova-3-stream` | documented | documented | no | allowed | REST WebSocket URL undocumented (docs show a Worker binding); Deepgram shaped messages assumed; neurons not read from the stream |

Known behaviour the mocks pin down, worth confirming live: a malformed 2xx body (not JSON, not an object,
or without the documented fields) is a `ProviderError` and is not retried; a valid empty transcript is an
empty result and is still billed; streaming handshake failures (a 429 or 5xx on the upgrade) are not
retried, the row is an error and the estimate is counted as spent.

### First live test

Before adding any key, with no network needed:

```sh
.venv/bin/python -m pytest -q && .venv/bin/ruff check .
.venv/bin/clipbench run --providers all --lang ja --limit 2 --no-stability --dry-run
.venv/bin/clipbench run-stream --providers all --lang ja --limit 2 --dry-run
```

The dry runs list every provider that is only missing keys, with its price and any `would be refused`
note. After adding one provider's keys to `.env`, run its two clip dry run, then the real run (`--no-cache`
so the request really goes out; `--no-stability` keeps it to two requests). Read the raw responses in the
cache and settle that provider's `unverified` items before setting `live_verified = true`.

| provider id | keys | two clip dry run, then the same command without `--dry-run` |
|---|---|---|
| `deepgram` | `DEEPGRAM_API_KEY` | `.venv/bin/clipbench run --providers deepgram --lang ja --limit 2 --no-stability --no-cache --dry-run` |
| `assemblyai` | `ASSEMBLYAI_API_KEY` | `.venv/bin/clipbench run --providers assemblyai --lang ko --limit 2 --no-stability --no-cache --dry-run` |
| `elevenlabs-scribe` | `ELEVENLABS_API_KEY` | `.venv/bin/clipbench run --providers elevenlabs-scribe --lang ja --limit 2 --no-stability --no-cache --dry-run` |
| `gemini-flash` | `GEMINI_API_KEY` (billing enabled) | `.venv/bin/clipbench run --providers gemini-flash --lang ja --limit 2 --no-stability --no-cache --dry-run` |
| `groq-whisper` | `GROQ_API_KEY` | `.venv/bin/clipbench run --providers groq-whisper --lang ja --limit 2 --no-stability --no-cache --detect-language --allow-unverified groq-whisper --dry-run` |
| `aws-transcribe` | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`, `CLIPBENCH_AWS_BUCKET`, the `aws` extra | `.venv/bin/clipbench run --providers aws-transcribe --lang ja --limit 2 --no-stability --no-cache --allow-unverified aws-transcribe --dry-run` |
| `cf-whisper-large-v3-turbo`, `cf-whisper`, `cf-deepgram-nova-3` | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | `.venv/bin/clipbench run --providers cf-whisper-large-v3-turbo,cf-whisper,cf-deepgram-nova-3 --lang ja --limit 2 --no-stability --no-cache --diarize --dry-run` |
| `cf-whisper-tiny-en` | as above | `.venv/bin/clipbench run --providers cf-whisper-tiny-en --lang en --limit 2 --no-stability --no-cache --allow-unverified cf-whisper-tiny-en --dry-run` |
| `deepgram-stream` | `DEEPGRAM_API_KEY` | `.venv/bin/clipbench run-stream --providers deepgram-stream --lang ja --limit 2 --no-cache --dry-run` |
| `assemblyai-stream` | `ASSEMBLYAI_API_KEY` | `.venv/bin/clipbench run-stream --providers assemblyai-stream --lang ko --limit 2 --no-cache --allow-unverified assemblyai-stream --dry-run` |
| `elevenlabs-stream` | `ELEVENLABS_API_KEY` | `.venv/bin/clipbench run-stream --providers elevenlabs-stream --lang ko --limit 2 --no-cache --allow-unverified elevenlabs-stream --dry-run` |
| `gemini-stream` | `GEMINI_API_KEY` | `.venv/bin/clipbench run-stream --providers gemini-stream --lang es --limit 2 --no-cache --dry-run` |
| `cf-nova-3-stream` | `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | `.venv/bin/clipbench run-stream --providers cf-nova-3-stream --lang ja --limit 2 --no-cache --dry-run` |

Pass `--allow-unverified` only after reading that provider's open questions; it is recorded in the run.
The `--detect-language` and `--diarize` flags in two rows exercise the open output-field questions
(Groq's `verbose_json.language`, Workers AI nova-3 `speaker`); drop them for a plain first call.

## Free and local systems

| provider id | model | USD / min | data |
|---|---|---|---|
| `whisper-local` | `mlx-community/whisper-large-v3-mlx` | 0 | local |
| `whisper-local-turbo` | `mlx-community/whisper-large-v3-turbo` | 0 | local |
| `youtube-auto` | `youtube-asr` | 0 | auto captions already on disk |
| `wayin` | `wayin` | 0 | existing Wayin output on disk |

## Shared behavior of the paid adapters

- Keys come from the environment (`.env` is loaded by `clipbench.config`). A missing key makes
  `availability()` return `(False, "<id>: skipped, missing KEY in .env")`.
- `clipbench/providers/http.py` gives each adapter one httpx client and a semaphore sized from
  `Provider.concurrency` (groq 2, gemini 4, elevenlabs 4, deepgram 8, assemblyai 8, aws 8, cf-* 8).
- Retries: up to 5 attempts, exponential backoff with full jitter (1 s base, 30 s cap) on 429, 5xx,
  timeouts and network errors. `Retry-After` is honoured (capped at 120 s). Other 4xx fail at once.
  After the last attempt a `ProviderError` is raised. A 2xx body that is not JSON, not an object, or lacks the
  documented fields is a `ProviderError` at once (the same request would get the same answer).
- `latency_ms` is wall clock from the first byte sent to the result, including upload and polling for
  async APIs (AssemblyAI, AWS), excluding our own retry backoff and time queued for a concurrency slot.
- `cost_usd` uses usage reported by the API when there is one (Gemini tokens, AssemblyAI model used,
  Deepgram billed duration, Workers AI neurons if reported), otherwise `pricing.toml` and the WAV
  duration, with any minimum applied.
- `raw_response` is the parsed JSON; `provider_version` is filled when the API reports one
  (Deepgram model version, Gemini `modelVersion`, AssemblyAI `speech_model_used`, Workers AI nova-3
  model info).

## v3 run options: diarization, language detection, keyterms

`clipbench run ... --diarize --detect-language --keyterms datasets/<lang>/keyterms.txt` sets the
options `diarize`, `detect_language` and `keyterms` on every provider (they are part of the cache key).
An adapter lists what it implements in `supported_options`; any other requested option is ignored and
recorded in `raw_response["clipbench_unsupported"]`. With `detect_language` no language hint is sent
and the provider's answer is normalised to ISO-639-1 in `Result.detected_language` ("ja-JP", "jpn" and
"japanese" all become "ja"). Per word speaker labels go to `Result.speakers`, parallel to `words`.
Checked 2026-10-19.

| provider id | diarize | detect_language | keyterms | docs |
|---|---|---|---|---|
| `deepgram` | `diarize_model=v2`, `words[].speaker` | `detect_language=true`, `channels[0].detected_language` | repeated `keyterm` (100 terms, 500 tokens) | [diarization](https://developers.deepgram.com/docs/diarization), [language detection](https://developers.deepgram.com/docs/language-detection), [keyterm](https://developers.deepgram.com/docs/keyterm) |
| `assemblyai` | `speaker_labels`, `words[].speaker` | `language_detection=true`, `language_code` | `keyterms_prompt` (capped at 200 for the Universal-2 fallback, 6 words each) | [submit](https://www.assemblyai.com/docs/api-reference/transcripts/submit) |
| `elevenlabs-scribe` | `diarize`, `words[].speaker_id` | omit `language_code`, read `language_code` | `keyterms` (1000, under 50 chars, 5 words; adds 20% to price, priced in) | [convert](https://elevenlabs.io/docs/api-reference/speech-to-text/convert) |
| `groq-whisper` | not offered | omit `language`, read `verbose_json.language` (needs a live check) | Whisper `prompt` (224 tokens; we cap at 400 chars) | [speech-to-text](https://console.groq.com/docs/speech-to-text), [API](https://console.groq.com/docs/api-reference) |
| `gemini-flash` | not supported (no word timings) | prompt names no language, JSON reply `{language, transcript}` | listed in the prompt | [audio](https://ai.google.dev/gemini-api/docs/audio) |
| `aws-transcribe` | `ShowSpeakerLabels`, `MaxSpeakerLabels=10`, `speaker_label` | `IdentifyLanguage` with `LanguageOptions` (our six languages) | not supported: needs a CreateVocabulary account resource | [diarization](https://docs.aws.amazon.com/transcribe/latest/dg/diarization.html), [LID](https://docs.aws.amazon.com/transcribe/latest/dg/lang-id-batch.html), [vocabulary](https://docs.aws.amazon.com/transcribe/latest/dg/custom-vocabulary.html) |
| `cf-whisper-large-v3-turbo` | not offered | omit `language`, read `transcription_info.language` | `initial_prompt` | [input](https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/schema-input.json), [output](https://developers.cloudflare.com/workers-ai/models/whisper-large-v3-turbo/schema-output.json) |
| `cf-whisper`, `cf-whisper-tiny-en` | not offered | not supported (always auto detects, reports nothing) | not offered | [whisper](https://developers.cloudflare.com/workers-ai/models/whisper/) |
| `cf-deepgram-nova-3` | `diarize=true` | `detect_language=true` | repeated `keyterm` | [schema](https://developers.cloudflare.com/workers-ai/models/nova-3/schema-input.json); output fields not in the schema, needs a live check |
| `whisper-local`, `whisper-local-turbo` | not offered | `language=None` | `initial_prompt` | [whisper](https://github.com/openai/whisper) |
| `youtube-auto`, `wayin` | not offered | not offered | not offered | read from disk |

Every result also carries `retries` (retries made across all requests of the transcription) and
`http_status` (last HTTP status seen). A `ProviderError` carries the same two attributes, and any other
exception raised inside `transcribe()` gets them attached, so the runner records them on error rows.

## Feature matrix

`[<id>.features]` tables in `pricing.toml` hold the B20 capability matrix: `languages` (which of en,
ja, ko, es, hi the provider serves), `batch`, `streaming`, `word_timestamps`, `diarization`,
`language_id`, `custom_vocabulary`, `max_file_minutes`, `retention`, `training_opt_out`. Every key has
`<key>_url` and `<key>_checked_on`, and optionally `<key>_note`; a missing value means unknown or not
stated. `clipbench providers features` prints it (`--json` with links, `--urls` for each cell's
source), and the export carries it under `features`. `youtube-auto` and `wayin` have no table: they read
existing captions and are not services a builder can buy.

Open questions to settle with a live call before publishing are listed per provider under Verification
status (and in `unverified` in `pricing.toml`). Separately, the newer `gemini-3.5-transcribe` model
(Interactions API, real diarization) has no adapter yet.

## How to add a provider

1. Look up the provider's current docs: best model for batch ja/es/ko, endpoint, language parameter,
   word timestamps, batch price per minute, minimum billing, and data use / opt-out flags.
2. Add a section to `clipbench/providers/pricing.toml` with `model_id`, `usd_per_minute`,
   `min_billed_seconds`, `checked_url`, `checked_on`, `data_policy`, `opt_out`, the exported
   policy fields `retention_days`, `trains_by_default`, `opt_out_status`, `policy_url` (and
   `[id.models]` for per-model prices), and the verification fields `live_verified = false`,
   `price_status`, `policy_status` and `unverified` (see Verification status; a real run refuses the
   provider until both statuses are `documented`). For a host that serves several models behind one API (Workers
   AI), give each model its own provider id and section, sharing one base class.
3. Create `clipbench/providers/<name>.py` with a subclass of `HttpProvider` (from `http.py`), or of
   `Provider` for SDK-based or local systems. Set `id`, `env_keys`, `concurrency`, `timeout_s`,
   `base_url`, implement `headers()` and `transcribe()`. Inside `transcribe`, wrap the work in
   `async with self.slot(), stopwatch() as watch:` and send requests with `self.http.request(...)`.
   Set every available opt-out flag. Put anything that changes the output into `self.options` (it goes
   into the cache key); keep test hooks and pure privacy flags out of it.
4. Register it in `clipbench/providers/registry.py` as `"id": "module:Class"`.
5. Add the key to `.env.example`.
6. Add tests with `httpx.MockTransport` and `no_sleep_policy()`: the request (model, language, auth,
   opt-out flag), the parsed `Result`, a 429 then success, failure after retries, and availability
   without the key; add it to `SPECS` in `tests/test_providers_contract.py` for the shared contract
   (malformed bodies, Retry-After, timeouts). Never call a real paid API from tests.
7. Add a row to the table above.
8. Implement the run options the API offers (set `supported_options`), add a `[<id>.features]` table
   with a doc URL and date for every cell, and add a row to the run options table.
