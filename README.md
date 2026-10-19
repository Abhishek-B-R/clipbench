# clipbench

**Which speech-to-text should your video app use?** clipbench is the independent benchmark for captions
on real short-form video: accuracy, word timing, hallucination over music, and cost, in five languages.

Caption, clipping and dubbing apps pick a speech-to-text vendor today from numbers measured on phone
calls, meetings and read speech, mostly in English. Short-form video is different: music under the
voice, sound effects, jump cuts, fast stylised delivery, slang, several speakers, and people switching
languages mid sentence. clipbench scores each system on what a caption product actually ships, on
clips a person checked by ear, with every comparison backed by a paired statistical test and code
anyone can rerun.

![Headline chart: accuracy against cost per audio hour](docs/headline.png)

*Headline chart placeholder: written by `clipbench report` once the first language is published.*

## What it measures

| metric | why a caption app cares |
|---|---|
| CER or WER, per content category and with or without music | the words on screen |
| Caption timing error (median, p90, share of words within 200 ms) | whether a word lights up when it is said |
| Hallucination over non-speech (words per minute of music or silence) | invented lines over the beat |
| Caption readiness (punctuation F1, casing accuracy) | captions you can ship without an edit pass |
| Code-switch accuracy (English inside non-English clips) | real creator speech |
| Cost per audio hour, latency, real-time factor | the bill and the wait |

Languages: English, Japanese, Korean, Spanish, Hindi and Hinglish. A language is published only with
100 or more verified clips and 6 or more providers. Until then the site shows progress, not a ranking.
How every number is made: [METHODOLOGY.md](METHODOLOGY.md). Every judgement call: [DECISIONS.md](DECISIONS.md).

## Quickstart

```bash
make setup                                                   # uv sync with local Whisper, aligner and AWS extras
uv run clipbench data discover --lang en --channels datasets/en/channels.csv   # find clips with creator captions
uv run clipbench data build && uv run clipbench data sample --lang en && uv run clipbench data fetch-audio
uv run clipbench review                                      # verify references by ear at http://127.0.0.1:8765
make dry-run run report                                      # cost estimate, run, score, report, site data
```

Paid providers need keys in `.env` (see `.env.example`); a provider without a key is skipped with a
message. The budget cap is `budget_usd` in `clipbench.toml`, and a run that would cross it does not start.

## Commands

| command | does |
|---|---|
| `clipbench data import-ids <csv> --lang L` | add any list of public clip ids (Shorts, Reels, TikToks, windows) |
| `clipbench data discover --lang L --channels <csv>` | find Shorts with creator-uploaded captions from a channel list |
| `clipbench data fetch-info` / `build` / `sample` / `fetch-audio` | metadata, manifest, stratified sample, audio (one at a time, 10 s apart) |
| `clipbench data check --lang L` | diversity rules: 30+ channels, no channel above 5%, 6+ categories |
| `clipbench review` | review UI: correct the text, tag category, music spans, code-switch spans |
| `clipbench align` | forced-align verified text to the audio for word timing references |
| `clipbench run --providers all --tier verified [--dry-run]` | transcribe with every system, cached and budget guarded |
| `clipbench score` / `errors` / `report` / `export` `<run_id\|latest>` | scores, error analysis, report, web data |
| `clipbench publish --check` | fails if anything publishable holds audio or caption text |

## Systems tested

Whisper large-v3 and turbo (local), Groq Whisper, Deepgram, AssemblyAI, ElevenLabs Scribe, Gemini Flash,
Amazon Transcribe, YouTube automatic captions, and Wayin where its output exists. Models, prices and
data policies as checked: [docs/providers.md](docs/providers.md).

## Add a provider

1. Check the provider's current docs: model, endpoint, language parameter, word timestamps, batch price
   per minute, minimum billing, data use and opt-out flags.
2. Add a section to `clipbench/providers/pricing.toml` with `model_id`, `usd_per_minute`,
   `min_billed_seconds`, `checked_url`, `checked_on`, `data_policy`, `opt_out`.
3. Write `clipbench/providers/<name>.py`: subclass `HttpProvider` (or `Provider` for SDKs and local
   models), set `id` and `env_keys`, implement `transcribe()` returning word timings when the API has
   them, and set every opt-out flag the API offers.
4. Register it in `clipbench/providers/registry.py` and add its key to `.env.example`.
5. Test it with `httpx.MockTransport`: the request, the parsed result, a 429 then success, and no key.

## Reproduce from clip ids

The clip lists are public: `datasets/<lang>/clips.csv`. Audio and captions are not, because they belong
to the creators. To rebuild a language:

```bash
uv run clipbench data import-ids datasets/en/clips.csv --lang en
uv run clipbench data fetch-info && uv run clipbench data build && uv run clipbench data fetch-audio
```

Clips that were deleted or made private since will be missing; every run records how many clips it scored.

## Web

`web/` is the site: landing page, per-language progress and findings, leaderboard, compare, provider
picker for builders, clip browser and methodology, built only from the JSON that `clipbench export`
writes. It deploys to Cloudflare (static assets plus a small Worker for the public data API). See
[docs/WEB.md](docs/WEB.md) and [docs/V2.md](docs/V2.md).

## Data and ethics

Audio, caption text and references live in `data/`, which is gitignored (a test checks it). Only code,
clip ids, scores, aggregates, charts and error snippets of a few words are published. Audio is fetched
slowly, one file at a time. Providers that would train on API data are only used with their opt-out set.

## Development

```bash
make test        # pytest, no keys needed
make lint        # ruff
cd web && bun run test && bun run build
```

## Acknowledgements

The Japanese set was seeded from caption tracks collected by Vako.

License: MIT.
