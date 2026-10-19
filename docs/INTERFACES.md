# Internal interfaces

How the pieces fit. Every module codes against this file. If you change a schema, update it here.

## Settings and paths

`clipbench.config.get_settings()` returns `Settings` with `language`, `vako_root`, `budget_usd`,
`sample_seed` and the paths below. Tests point everything at a tmp dir with the env vars
`CLIPBENCH_DATA_DIR`, `CLIPBENCH_RESULTS_DIR`, `CLIPBENCH_SITE_DIR` (call `get_settings.cache_clear()` after setting them).

| path | what | published? |
|---|---|---|
| `datasets/<lang>/clips.csv`, `channels.csv` | dataset spec: ids, windows, categories (no text) | yes |
| `data/manifest.parquet` | one row per clips.csv row, tracks classified | no |
| `data/tracks/<id>.<key>.json3`, `data/tracks/windows/` | caption tracks fetched by clipbench, window slices | no |
| `data/discover/` | discover state, listings, category hints | no |
| `data/align/<clip_id>.json` | word timings from `clipbench align` | no |
| `data/info/<videoId>.info.json` | info.json fetched by clipbench when Vako has none | no |
| `data/sample.parquet` | the stratified sample, every language | no |
| `data/audio/<videoId>.<ext>` + `data/audio/<videoId>.wav` | original + 16 kHz mono WAV | no |
| `data/audio/hashes.json` | `{videoId: {"original": sha256, "wav": sha256}}` | no |
| `data/verified/<videoId>.json` | review UI output | no |
| `data/cache/...` | provider result cache | no |
| `data/spend.json` | money spent so far | no |
| `data/runs/<run_id>/texts.parquet` | hypothesis text per row | no |
| `data/runs/<run_id>/refs.parquet` | reference snapshot used by the run | no |
| `results/runs/<run_id>/rows.parquet` | numeric rows, no text | yes |
| `results/runs/<run_id>/run_meta.json` | commit, versions, model ids, seed | yes |
| `results/<run_id>/*.parquet`, `report.md` | scores, aggregates | yes |
| `site/index.html` | static report | yes |

## Dataset spec and manifest

`datasets/<lang>/clips.csv` (committed, public, ids and windows only, no text) is the source of truth for
which clips exist: `platform, video_id, start_ms, end_ms, channel_id, category, license_note, source`
(see docs/V2.md). `datasets/<lang>/channels.csv`: `platform, channel_id, handle, category, note`
(input of `clipbench data discover`). Importers: `data import-vako`, `data import-ids <csv>`,
`data discover`. Caption tracks found by importers live in Vako's store or `data/tracks/<id>.<key>.json3`
and are classified from info.json (docs/data_rules.md).

`data/manifest.parquet`: one row per clips.csv row: `video_id` (the clip_id: `<id>` or
`<id>@<start_ms>-<end_ms>`), `language, duration_s (float|null), channel, channel_id,
has_creator_track (bool), has_auto_track (bool), creator_track_key, creator_track_path,
auto_track_key, auto_track_path, info_path, info_source ("vako"|"clipbench"|null), clip_id, source_id
(platform id), youtube_id (source_id on YouTube, else null), platform ("youtube"|"tiktok"|"instagram"),
clip_source ("short"|"window"|"long"), start_ms, end_ms (Int64|null), window_start_s, window_end_s
(float|null), category (clips.csv value, else a guess, else null), category_guess (bool: true when the
category is a guess), license_note, source`. A window row's track paths point at sliced, rebased copies
in `data/tracks/windows/`.

`data/sample.parquet`: manifest columns plus `sample_rank` (pick order inside a language); holds every
language, `data sample --lang L` replaces only L's rows. `clipbench.data.sample.clip_meta()` gives per
clip `platform, source_id, youtube_id, kind, start_s, end_s, category, category_guess, url`.

## References

`clipbench.data.references`:

- `load_reference(video_id, tier, settings=None) -> Reference | None`
  - tier `verified`: reads `data/verified/<videoId>.json` (skips if tagged `unusable`).
  - tier `creator`: parses the creator json3 from the manifest path.
- `load_clips(tier, settings=None, limit=None, language=None) -> list[Clip]`: sample rows that have
  WAV audio on disk and a reference in that tier (all languages unless `language`). References carry
  platform, source_id, clip_source, start_ms, category; verified ones also the verified file's
  category, nonspeech_spans, codeswitch_spans, punctuated, and words from `data/align/<clip_id>.json`
  (timing_source `aligner:<name>`) or the creator track (`creator`).
- `clipbench.data.json3.parse_json3_text(text, language) -> str` and
  `parse_json3_words(text) -> list[Word]` (ports of Vako `parseJson3` / `parseJson3Words`).

## Verified file (`data/verified/<videoId>.json`)

```json
{"video_id": "...", "language": "ja", "text": "corrected text", "original_text": "creator caption",
 "tags": ["background_music"], "reviewer": "abhishek", "reviewed_at": "2026-10-19T12:00:00Z",
 "audio_sha256": "...", "skipped": false}
```

## Providers

`clipbench.providers.base.Provider` (see the file). `transcribe(audio_path, language, *, clip=None) -> Result`.
Raise `ProviderSkip` when a provider has nothing for a clip (youtube-auto with no auto track,
wayin with no output). `availability()` returns `(ok, message)`; the runner skips unavailable
providers with that message. `estimate_cost(seconds)` uses `providers/pricing.toml`:

```toml
[deepgram]
model_id = "nova-3"
usd_per_minute = 0.0043
min_billed_seconds = 0
checked_url = "https://..."
checked_on = "2026-10-19"
notes = "..."
[deepgram.models]   # optional per model prices
"nova-3" = 0.0043
```

Cache: `clipbench.providers.cache.ResultCache(settings.cache_dir)`.

## Run rows (`results/runs/<run_id>/rows.parquet`)

One row per (clip, provider, repeat). `repeat` 0 is the main pass; 1..3 are the stability reruns. A run holds
one language (`clipbench run --lang L`; the runner and `score` refuse a mixed run, since en, es and hi all
score `wer`).
Columns: `run_id, provider, model_id, provider_version, video_id, language, tier, repeat,
status ("ok"|"skipped"|"error"), error, latency_ms, audio_seconds, cost_usd, cached (bool),
duration_s, channel, tags (json list string), hyp_chars`.

`data/runs/<run_id>/texts.parquet`: `provider, video_id, repeat, hyp_text, words_json`.
`data/runs/<run_id>/refs.parquet`: `video_id, tier, ref_text, words_json, tags (json list string)`.

## Scores (`results/<run_id>/`)

`clipbench.scoring` writes:

- `scores.parquet` per (provider, video_id, metric), repeat 0 only: `provider, model_id, video_id, tier,
  language, metric, is_primary (bool), ref_len, hits, subs, dels, ins, errors, error_rate,
  hallucinated_tokens, hallucination_rate, length_ratio, long_output (bool), latency_ms, cost_usd,
  audio_seconds, duration_s, duration_bucket, tags (json list string)`.
- `aggregates.parquet`: `provider, model_id, tier, metric, slice_kind ("all"|"tag"|"duration"), slice,
  n_clips, error_rate, ci_low, ci_high, too_small (bool), hallucination_rate, long_output_rate,
  cost_usd_total, audio_hours, cost_per_hour, latency_p50, latency_p95, rtf`.
- `paired.parquet`: `tier, metric, slice_kind, slice, provider_a, provider_b, n_clips, diff
  (a minus b, error rate), ci_low, ci_high, p_value, verdict ("a_better"|"b_better"|"no_difference")`.
- `stability.parquet`: `provider, video_id, n_repeats, text_disagreement (mean pairwise error rate
  between repeats), latency_mean, latency_std`.

Metrics per language (primary first): `ja`: `cer`, `cer_reading`; `es`: `wer`, `wer_no_accents`;
`ko`: `cer_nospace`, `wer_syllable`.

## Error analysis (`results/<run_id>/errors.json`)

`{"providers": {pid: {"top_substitutions": [[ref, hyp, count]], "worst_clips": [{video_id, error_rate, others_median}]}},
"all_fail": [video_id], "examples": [{provider, video_id, ref_snippet, hyp_snippet}]}`. Snippets are at most 4 words
(or 8 characters for ja). `all_fail` clips are also written to `data/rereview.json` so the review UI can surface them (the run's own clips
are replaced there, clips of other runs are kept).
