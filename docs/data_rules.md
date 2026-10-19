# Data rules (dataset builder)

How clipbench turns Vako's caption store into `data/manifest.parquet` and `data/sample.parquet`.
Code: `clipbench/data/vako.py`, `manifest.py`, `sample.py`.

## Source layout

`<vako_root>/.vr/src/<lang>/<videoId>.<trackkey>.json3`, sometimes `<videoId>.info.json`.
Video ids are 11 characters from `[A-Za-z0-9_-]` and can start with `-`. The file name is split
as `id = name[:11]`, `key = name[12:-6]`. Vako keeps exactly one json3 per video (the track its
own `scan.py` picked: creator first, else `<lang>-orig`, else plain `<lang>` when info says the
clip is spoken in it), so every video has one track.

## Which info.json

Vako's `src/<lang>/<id>.info.json` first, then `data/info/<id>.info.json` (fetched by
`clipbench data fetch-info`). An unreadable file counts as none. A track key counts as listed only
when its format list is non empty (as Vako `parseInfo`).

## Track classification (from info.json only, never from the file name)

1. No info.json: `unknown`.
2. Key in `subtitles`: `creator`.
3. Key in neither `subtitles` nor `automatic_captions`: `unknown`.
4. Key in `automatic_captions`:
   - base language (part before the first `-`) is not the clip language: `auto_translation`;
   - key ends with `-orig`: `auto` (YouTube ASR of the spoken language);
   - plain key and info `language` has the clip language as base: `auto`;
   - plain key, info has no `language`, and no `-orig` key of another language: `auto`
     (Vako's benefit of the doubt for old clips);
   - otherwise: `auto_translation`.

`ja-<suffix>` keys (e.g. `ja-AN2ECZLziws`): checked in the fetched info.json files. They sit under
`subtitles` with a name like `Japanese - <track title>`. They are named creator tracks, and rule 2
classifies them as `creator`. In ja, all 3 such keys that have info.json are in `subtitles`.

Manifest `creator_track_*` is the creator track (exact language key preferred over regional or
named ones); `auto_track_*` is the auto track (`-orig` preferred).

## Sample

Eligible: `has_creator_track` and `5 <= duration_s <= 90` (duration from info.json). Cap per channel
`floor(0.10 * n)` (at least 1), channel = `channel_id` (else `channel`). Seeded `random.Random(seed)`:
shuffle each channel's clips (sorted by id first), shuffle the channel order, then take clips round
robin one per channel per round until `n` or the pool under the cap is exhausted. `sample_rank` is
the pick order. If short, the command prints `SHORT` with the count.

## fetch-info pre-filter

Only ids whose json3 estimate (end of the last event, a lower bound on duration) is <= 95 s, unless
`--all`. Checked against Vako's `probed-ja.json` durations: of 1242 ja videos with a probed
duration, none at <= 90 s had an estimate above 95 s, so the filter loses no eligible clip.

## Counts (2026-10-19)

ja after `fetch-info` (460 fetched, 0 failed, about 77 min at 5 s + jitter):

| | count |
|---|---|
| videos with json3 | 1344 |
| with info.json | 483 (vako 23, clipbench 460) |
| tracks `creator` | 13 (10 `ja`, 3 `ja-<suffix>`) |
| tracks `auto` | 470 (464 `ja-orig`, 6 plain `ja` with info language `ja`) |
| tracks `auto_translation` | 0 |
| tracks `unknown` (no info.json, all long by estimate) | 861 (475 `ja`, 362 `ja-orig`, 24 `ja-<suffix>`) |
| creator track and 5-90 s (eligible) | 11 clips, 3 channels |

Duration of the 483 with info: median 48 s; 5-30 s 91, 30-60 s 299, 60-90 s 89, 90-180 s 1, >10 min 3.

ko and es from Vako's existing info.json only (nothing fetched): ko 3920 videos, 47 with info, 0 creator,
47 auto (eligible 0); es 1371 videos, 0 with info (eligible 0). Short fetch candidates if fetched: ko 946, es 471.
Vako's own probe records (`.vr/probed-<lang>.json`, written from info.json at probe time, `manualSubs`)
predict 54 eligible creator clips in ko (13 channels, one with 26) and 25 in es (7 channels); for ja they
predicted 11, which matches the fetched result exactly.
