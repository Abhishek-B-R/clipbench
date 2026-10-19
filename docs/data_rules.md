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

## Filling the shortfall: Shorts first, windows to fill (decided 2026-10-19)

The ja pool had 11 creator-captioned Shorts. Two routes fill it, in this order.

### A. Discover more Shorts (`clipbench data discover`)

- Seed channels from Vako `.vr` (read only), best bets first: channels where `probed-ja.json` saw creator
  captions (by count), `vouched-channels.json`, `beginner-channels.json` (by `easy`), `channels-db.json`
  (by `live`, then `easy`). `blocked-channels.json` is skipped. 1803 ja seed channels.
- Per channel: one `yt-dlp --flat-playlist -J .../channel/<id>/shorts` (first 100 entries), then one
  info.json request per Short not already known (Vako src, `data/info`, `probed-ja.json`, failed list).
- Keep rule: a `subtitles` key with base `ja` (not `-orig`), spoken in ja (info `language` base `ja`, or a
  `ja-orig` ASR track), duration 5-90 s, channel not blocked. Every inspected info.json is saved to
  `data/info/` so it is never asked for twice.
- For kept Shorts, one more request downloads the creator track(s) and `ja-orig` as json3 into
  `data/tracks/<id>.<key>.json3` (`--load-info-json` on the full info, so the page is not fetched again).
- Channel stop rules: dropped after 6 inspected Shorts in a row without a keep while it has none kept;
  finished after 15 kept (the per channel cap at n=150) or 18 misses in a row.
- Pacing as fetch-info: one process at a time, 5 s + 0-2 s jitter, stop on bot check or 429,
  `--max-requests` (default 1500) per run. Resumable from `data/discover/state-<lang>.json`.
- `data build` merges `data/tracks/` with Vako's `.vr/src/<lang>/` (Vako's file wins on the same key) and
  classifies them by the same info.json rule.
- `clipbench data fetch-tracks` fetches `ja-orig` (only when info.json lists it) for sampled clips that have
  a creator track and no auto track, so youtube-auto has an output for them; it rebuilds the manifest and
  refreshes the sample's columns without changing membership.

### B. One window per long video (`clip_source` "window")

- Targets: Vako videos with no info.json whose Vako track Vako's probe saw as creator
  (`fetch-info --probed-creator --per-channel 18`; targeting only, kind still comes from the fetched info).
- For every manifest video with a creator track and info duration over 90 s, at most one window:
  - built from the creator track's lines, window edges on line starts and ends, rounded outwards to
    whole seconds;
  - clean edges: no other caption line overlaps the rounded window, so the reference is exactly the
    lines inside it;
  - start at or after 10 s, end at or before the duration, length 20-60 s;
  - highest caption characters per second wins (dense speech), ties to the earliest; under 3 chars/s
    rejected.
- `clip_id = <youtubeId>@<start>-<end>` (whole seconds). The window row's `video_id` is the clip_id (the
  clip key everywhere: references, `data/audio/<clip_id>.wav`, `data/verified/<clip_id>.json`, caches);
  `youtube_id` holds the YouTube id. Track paths point at sliced, rebased copies in `data/tracks/windows/`.
- Audio: `yt-dlp -f bestaudio --download-sections "*START-END"`, then ffmpeg `-t (END-START)` to make the
  WAV exactly the window.
- Other rows: `clip_source` "short" (duration 90 s or less, or unknown) or "long" (over 90 s, never sampled).

### Sample

Shorts first (round robin over channels, seeded), then windows to fill n; the cap floor(0.10 n) per channel
counts Shorts and windows together. `--shorts-only` skips windows.
