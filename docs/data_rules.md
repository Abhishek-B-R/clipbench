# Data rules (dataset builder)

How clipbench goes from public clip ids to `data/manifest.parquet`, `data/sample.parquet`, references and
audio. Code: `clipbench/data/`. Spec: docs/V2.md "Dataset spec".

## Dataset spec

- `datasets/<lang>/clips.csv` (committed, public) is the source of truth for which clips exist:
  `platform, video_id, start_ms, end_ms, channel_id, category, license_note, source`. Ids and windows only,
  never caption text. `start_ms`/`end_ms` both blank for a whole video.
- `clip_id` = `<video_id>` for a whole video, `<video_id>@<start_ms>-<end_ms>` for a window. It is the
  `video_id` of every manifest/sample row, Reference, Clip, audio file (`data/audio/<clip_id>.wav`) and
  verified file. The platform id is `source_id`.
- Platforms: `youtube` fully supported (tracks, audio). `tiktok`, `instagram` are accepted: audio through
  yt-dlp, no caption tracks, so they are reviewed from scratch.
- `datasets/<lang>/channels.csv`: `platform, channel_id, handle, category, note`, the input of discover.
  Rows noted `unverified` are curated from memory and not yet checked against YouTube.
- Importers add rows (never remove): `data import-vako` (source `vako`), `data import-ids <csv>`
  (source `ids` or `--source`), `data discover` (source `discover`). A repeated clip keeps its row; blank
  `channel_id`/`category` are filled from the newer row.

## Caption tracks and classification

Tracks come from whichever importer found them: Vako's store (`.vr/src/<lang>/<id>.<key>.json3`) or
`data/tracks/<id>.<key>.json3` (discover, fetch-tracks); Vako's file wins on the same key. info.json is
Vako's first, then `data/info/`. A key counts as listed only when its format list is non empty (as Vako
`parseInfo`). The kind comes from info.json only, never from the file name:

1. no info.json: `unknown`;
2. key in `subtitles`: `creator` (named tracks such as `ja-AN2ECZLziws` are listed there too);
3. key in neither `subtitles` nor `automatic_captions`: `unknown`;
4. key in `automatic_captions`: base language not the clip's: `auto_translation`; ends with `-orig`: `auto`;
   plain key and info `language` is the clip language: `auto`; plain key, no info language and no `-orig`
   of another language: `auto`; otherwise `auto_translation`.

The manifest's creator track prefers the exact language key; the auto track prefers `-orig`.
`data fetch-tracks` fetches, for clips.csv YouTube rows, a missing info.json and any creator track in the
language and `<lang>-orig` that info.json lists and that is not on disk.

## Windows

- `import-vako` turns a Vako video with a creator track into a whole-video row when it lasts 5-90 s, or into
  one window when it is longer. The window is built from the creator track's lines: edges on line starts
  and ends rounded outwards to whole seconds; no other line overlaps the rounded window (the reference is
  exactly the lines inside it); start at or after 10 s; end at least 1 s before the info.json duration (the
  audio stream can end about 0.4 s early); 20-60 s; highest
  caption characters per second wins, ties to the earliest; under 3 chars/s rejected.
- A window row's tracks are sliced and rebased into `data/tracks/windows/<clip_id>.<key>.json3`.
- Audio: `yt-dlp -f bestaudio/best --download-sections "*START-END" --force-keyframes-at-cuts`, then
  ffmpeg `-t (END-START)`. A stream copied section starts at the packet before START, so the cut is
  re-encoded; checked once against the full download of iLy7zSAX7hc (24-61 s): the section starts 8 ms
  early. A window WAV more than 0.15 s off its expected length is deleted and recorded as a failure.
  `load_clips` takes `duration_s` from the WAV itself.
- `clip_source`: `window`, else `short` (duration 90 s or less, or unknown), else `long` (never sampled).

## Category

`category` in clips.csv is authoritative (exact strings: talking_head, vlog_music, gaming, comedy_skit,
cooking_tutorial, news_explainer, other). Without one, the manifest pre-fills a guess and sets
`category_guess = true`: first the curated category of the channel the Short was discovered from
(`data/discover/category_hints-<lang>.json`), else a keyword match on the info.json title and tags, else
the YouTube category (Gaming, Comedy, News & Politics, Education and Science & Technology to
news_explainer, Howto & Style to cooking_tutorial, Music and Travel & Events to vlog_music, People & Blogs
and any other category to talking_head). The review UI confirms or changes it.

## Sample

Per language, n=150 by default, seed 7. Eligible: Shorts of 5-90 s and windows, with a creator track (or
on a platform without tracks). Cap per channel floor(0.05 n) (7 at n=150), Shorts and windows together.
Categories balanced: one clip per category in a seeded rotation (clips with no category form their own
bucket), channels round robin inside a category. Shorts first; windows fill the rest (`--shorts-only`
skips them). If the caps cannot reach n, the command prints `SHORT`.

## Diversity check

`clipbench data check --lang L` (sample by default, `--pool` for the eligible manifest pool) fails with
exit 1 unless: at least 30 channels, no channel above 5% of the set, at least 6 categories present (not
counting `other`). Guessed categories count unless `--strict`.

## Discover

Channels from `datasets/<lang>/channels.csv` (`channel_id` or `handle`). Per channel: one
`--flat-playlist` listing of the Shorts tab (first 100), then one info.json request per Short not already
known. Kept when info.json has a `subtitles` key in the language (not `-orig`), the clip is spoken in the
language (info `language`, or a `<lang>-orig` track), 5-90 s, and the channel is not blocked; then one more
request downloads the creator track(s) and `<lang>-orig`. A channel is dropped after 6 misses in a row with
none kept, finished at 8 kept (5% of 150 plus one) or 18 misses in a row. One yt-dlp process at a time,
5 s + 0-2 s jitter, stop on bot check or 429, `--max-requests` (default 1500). Resumable.

## Pacing (all network commands)

One yt-dlp process at a time. fetch-info, discover, fetch-tracks: at least 5 s + jitter between requests.
fetch-audio: at least 10 s between downloads. A bot check or HTTP 429 stops the run. Failures are recorded
(`data/info/failed.json`, `data/tracks/failed.json`, `data/audio/failed.json`) and not retried unless asked.

## History

- 2026-10-19, v1: ja from Vako's store had 11 creator-captioned Shorts (3 channels). Fixed by "Shorts first,
  windows to fill": discover found 23 more ja Shorts (7 channels, about 1450 requests over 229 channels);
  fetch-info for 437 long videos whose Vako track the store's probe saw as creator gave 298 clean windows.
- 2026-10-20: window end margin of 1 s before the info.json duration. `import-vako --replace` rebuilt the
  ja vako rows: 9 windows changed (7 moved, 2 dropped), ja pool 34 Shorts and 296 windows; the ja sample is
  150 clips (23 Shorts, 127 windows, 41 channels) and all 150 have audio.
