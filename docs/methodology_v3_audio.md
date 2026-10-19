# Methodology v3: audio features (sweeps, long-form, compute)

To be merged into METHODOLOGY.md. Covers A5 (controlled perturbation sweeps, with B14's per-SNR
hallucination), A8 (long-form track) and A9 (speed and compute).

## Controlled perturbation sweeps (A5)

How fast each model falls apart as the music or noise under the speech gets louder. Code:
`clipbench/sweeps/`. Commands: `clipbench sweep beds | fetch-beds | build | run | score`.

### Clean clips

Verified clips of one language that have no `background_music` or `noisy` tag, no `music` or
`outdoor_noise` condition and no non-speech span of kind `music`. Noise is excluded too (not only music)
so the clean baseline is actually clean.

### Beds

`datasets/beds/beds.json` lists every bed with its source page, download URL, sha1, license, license URL
and attribution. The audio itself is never committed or published: `clipbench sweep fetch-beds` downloads
it into `data/beds/` (one file at a time, 2 s apart), checks the sha1 the source publishes, and converts it
to 16 kHz mono WAV.

- Allowed licenses: CC0, public domain, CC BY, CC BY-SA (any version). All allow commercial use. Anything
  NC, ND, or unclear is refused by code (`clipbench.sweeps.beds.license_ok`), not only by convention.
- Sources: Wikimedia Commons files whose license is shown in the file's metadata (checked through the
  Commons API on 2026-10-19), and two synthetic noises (pink and brown, generated from a seed, CC0 by
  construction).
- Music beds are instrumental by the source's description, noise beds are ambient (rain, traffic, forest,
  street). `speech_free: "claimed"` and `listened: false` say nobody has listened to them for clipbench
  yet; a bed with sung or spoken words would add words the reference does not have, so listen before a
  published sweep and set `listened: true`.

### Mixing at an exact SNR

1. The clip is padded with 2.0 s before and after the speech (`PAD_S`). The pads are known non-speech.
2. **Active speech.** The padded clip is cut into 20 ms frames. A frame is active when its RMS level is at
   most 30 dB below the 95th percentile frame level and above -70 dBFS. Frames in the pads or in a tagged
   non-speech span are never active. Speech RMS is the RMS over the active samples, so pauses do not lower
   the speech level.
3. **Bed level** is the RMS over the whole bed segment mixed under the clip.
4. `gain = rms_speech / (rms_bed * 10^(SNR / 20))`; `mix = speech + gain * bed`. If the peak would exceed
   0.99 the whole mix is scaled down, which leaves the SNR unchanged (the scale is in the recipe).
5. One bed and one offset per clip and bed kind, the same at every SNR, so the only thing that changes
   along a sweep is the level. Beds are assigned to clips round robin from a seeded permutation; offsets
   are seeded per clip. A bed shorter than the clip loops.
6. **Reverb** (`--reverb`): a synthetic room impulse response (unit direct path plus exponentially
   decaying Gaussian noise, 60 dB decay at RT60, default 0.5 s, direct to reverberant energy 0 dB, seeded
   per clip) is convolved with the padded speech, which is then rescaled to the dry RMS. The bed is added
   dry after the reverb, as in a room where the music comes from the video edit, not the room. The active
   frames are the dry ones.
7. WAVs are 16 kHz mono PCM 16, written to `data/sweeps/<sweep_id>/audio/` (never published).

`results/sweeps/<sweep_id>/recipes.parquet` has one row per variant: clip id, condition, bed kind and id,
bed license, bed offset, SNR, reverb, RT60 and IR seed, seed, pad, duration, gain, scale, speech and bed
levels, active speech seconds, the measured SNR, the source WAV's sha256 and the output's sha256. No audio,
no text. With the clip's source WAV and the bed this rebuilds the variant exactly.

### Running

One sweep is one runner run (run id = sweep id) holding every condition, with clip ids
`<clip_id>#clean`, `<clip_id>#<kind>_snr<SNR>` and `_rev` for the reverb series (for example
`abc123#music_snr-5_rev`). One run keeps resume, the budget guard, the cache and `run_meta.json`
(`kind = "sweep"`) in one place and lets the score pair the conditions of a clip. The run lives in
`results/sweeps/<sweep_id>/` and `data/sweeps/<sweep_id>/`, never under `results/runs/`, so it can never
show up as a leaderboard run. Stability reruns are off. Paid providers go through the usual estimate
(`--dry-run`) and budget guard.

### Scoring

Per variant, the same scorer as a normal run (same normalisers, alignment, hallucination rules). The
variant reference is the verified reference with word timings shifted by the pad and the pads tagged as
non-speech, so hallucination over non-speech is measured on every clip (it needs provider word timings).

Per provider, metric and condition (`results/sweeps/<sweep_id>/sweeps.parquet`, `slice_kind =
"sweep_condition"`):

- `error_rate`, `ci_low`, `ci_high`: corpus rate with a 95% clip bootstrap CI.
- `clean_error_rate` (dry clean) and `rel_to_clean` = rate / clean rate.
- `halluc_per_nonspeech_min` with a bootstrap CI (words per non-speech minute; resampling clips, ratio of
  sums), `halluc_nonspeech_words`, `nonspeech_minutes`, `halluc_n_clips`; and the v1
  `hallucination_rate` and `long_output_rate`.
- **Doubling SNR** (`doubling_snr_db`), per bed kind and reverb series: the SNR at which the error rate
  reaches 2 x the dry clean rate, with the clean rate floored at 1% (so the threshold is at least 2%),
  linearly interpolated between the two SNR levels around the first crossing (walking from high to low
  SNR). Higher means the model falls apart earlier. `doubling_censored = "above"`: already doubled at the
  highest SNR tested (the value is that SNR, a lower bound); `"below"`: never doubled down to the lowest SNR
  tested (value null). `doubling_ci_low/high`: percentiles over 1000 clip resamples applied to every
  condition at once (paired), with "never doubled" counted as minus infinity.

`sweep_scores.parquet` keeps the per variant numbers (no text).

### Export

`clipbench.sweeps.export.export_section(run_id, settings)` returns `{"sweeps": [...]}`: every scored sweep
in the run's language, newest first, each with its settings, the beds used (id, kind, source page,
license, attribution) and the primary metric rows of `sweeps.parquet`. Null when there is none.

### Limitations

- Mixed beds are a controlled stand-in for real background music: real edits duck the music under speech
  and choose tracks that fit; a fixed-SNR bed does not.
- The reverb is synthetic and the same shape for every clip apart from the noise seed.
- Hallucination over non-speech needs provider word timings; providers without them have null there.

## Long-form track (A8)

Does a provider still work on a whole video: accuracy, word timing drift over time, and chunking errors.
A **separate track**: its runs live in `results/longform/<run_id>/` and `data/longform/`, never under
`results/runs/`, `run_meta.kind = "longform"`, and the export key is `longform` with `track =
"longform"`. Long-form numbers are never mixed into the short-form leaderboard. Code: `clipbench/longform.py`.
Commands: `clipbench longform list | fetch | run | score`.

### Videos and reference

- From the existing manifest, no new discovery: the source videos behind window clips (and any `long`
  rows), 3 to 20 minutes (180 to 1200 s by their info.json), with a creator caption track for the whole
  video. `fetch --limit N` picks N of them spread across channels (seeded shuffle, one per channel per
  round), keeps the choice in `data/longform/selection-<lang>.json`, and downloads the audio with the same
  gentle rules as `clipbench data fetch-audio`: one video at a time, at least 10 s apart (plus 0 to 3 s
  jitter), stop on a bot check or HTTP 429.
- The reference is the creator track (tier `creator`, **unverified**: creator captions are often not
  verbatim, so absolute error rates are inflated; comparisons between providers on the same videos still
  hold). Its caption lines with their start and end times are the timing anchors.
- Each provider call gets a timeout of the longest video's length plus 10 minutes (per instance), since a
  20 minute file takes far longer than a Short. Stability reruns are off.

### Metrics (primary metric of the language)

- **Accuracy**: corpus error rate over the whole video, with a clip (video) bootstrap CI.
- **Error rate per minute**: each reference token is placed in time by spreading a caption line's tokens
  evenly over the line. Substitutions and deletions count at their reference token, insertions at the
  reference position they are inserted before. Minute buckets 0 to 19 (19 holds everything after 19:00);
  per bucket a corpus rate with a bootstrap CI over videos (`slice_kind = "minute"`).
- **Timing drift**: for every caption line whose first token the provider got right (an exact hit, as in
  caption timing error), offset = provider start of that token minus the line start. Per minute: median
  absolute and median signed offset. Per video: least squares slope of the signed offset against time in
  minutes (`drift_slope_ms_per_min`, systematic drift), of the absolute offset (`abs_slope_ms_per_min`,
  degradation; both need at least 10 matched lines over at least 2 minutes), and the median absolute
  offset before and after minute 5. Aggregates take the median across videos.
- **Gaps**: a stretch of the video with no hypothesis word at all that contains reference caption lines
  spanning more than 10 s (first such line start to last such line end). Needs provider word timings
  (null otherwise). Reported as `n_gaps`, `gap_seconds`, `longest_gap_s`; aggregates `gaps_per_hour` and
  `gap_share` (gap seconds over audio seconds).
- **Duplicated text**: an insertion run (consecutive inserted tokens in the alignment) of at least 4 words
  (8 characters for ja) that repeats hypothesis text right next to it (within 3 x the run length + 20
  tokens before or after), the signature of a chunk boundary transcribed twice. `dup_events`,
  `dup_tokens`; aggregates `dup_events_per_hour` and `dup_token_rate` (over reference tokens).
- **Failure rate**: share of videos whose call errored or timed out (status not ok), the bluntest way a
  provider fails to handle the length.

Files: `longform.parquet` (per provider and video), `longform_minutes.parquet` (per provider, video and
minute), `longform_aggregates.parquet` (`slice_kind` `all` and `minute`). Numbers only.

### Export

`clipbench.longform.export_section(run_id, settings)` returns `{"longform": {track, run_id, language,
created_at, reference, n_videos, min_minutes, max_minutes, aggregates, minutes}}` for the newest scored
long-form run in the run's language, else null.

## Speed and compute (A9)

Code: `clipbench/compute.py`. Commands: `clipbench compute machine | bench | table`.

### Local open models (`whisper-local`, `whisper-local-turbo`)

`clipbench compute bench --lang L --limit N` times each model on the machine it runs on and appends one
row per model to `results/compute/bench.parquet`:

- Each model runs in its own fresh Python subprocess, so memory numbers belong to that model only.
- The first clip is a **warm up**: it loads the weights, and its time is reported separately as
  `warmup_s`. The next N clips are transcribed one after another.
- `rtfx` = total audio seconds / total wall seconds over the N clips (Hugging Face Open ASR style; 10 means
  ten seconds of audio per second). `rtfx_median` is the median of the per clip ratios.
- `peak_rss_bytes`: the subprocess's peak resident memory from `os.wait4` (`ru_maxrss`; bytes on macOS,
  KiB converted to bytes on Linux). psutil is not a dependency, so this is the only process measure.
- `peak_gpu_bytes`: with the mlx backend, MLX's own peak allocation (`mlx.core.get_peak_memory`). On
  Apple silicon GPU memory is the same unified memory, but Metal buffers are not always counted in RSS, so
  both are reported.
- `model_size_bytes`: size on disk of the model's Hugging Face cache repo (its blobs, each file once).
- The declared machine: `chip` (sysctl `machdep.cpu.brand_string` on macOS, `/proc/cpuinfo` on Linux),
  `cores_total`, `cores_performance`, `cores_efficiency`, `ram_bytes`, `os`, `python`, `backend` (mlx on
  the Apple GPU or faster-whisper int8 on CPU), and `machine_id`, a hash of chip, cores, RAM and
  architecture so numbers from one machine group together.
- Metal is not available in a sandboxed shell; there mlx-whisper cannot import and the backend falls back
  to faster-whisper on CPU. The `backend` column says which one ran.

### Every provider in a run

`clipbench compute table <run_id>` writes `results/<run_id>/compute.parquet` (one row per provider):

- `speed_factor_median` (headline): median over non cached ok rows of audio seconds / latency seconds,
  robust to a cold start such as the first call downloading weights; `speed_factor`: total audio over total
  latency on the same rows. For APIs latency includes upload, queueing and polling, which is what a caption
  app waits for.
- APIs: `usd_per_1000_min` = list price per minute from `pricing.toml` x 1000 (Artificial Analysis style),
  and `effective_usd_per_1000_min` = sum of `cost_usd` over audio minutes x 1000, which includes minimum
  billed lengths on short clips.
- Local models: the newest bench row for the same provider and backend (`rtfx`, memory, size, machine).
  Their price is null (free to run; hardware cost is not priced).

### Export

`clipbench.compute.export_section(run_id, settings)` returns `{"compute": {"rows": [...], "machines":
[...]}}` from `compute.parquet` (or built from the run's rows when the file is missing), null when the run
has no rows.

## Decisions

- One runner run per sweep with clip ids `<clip_id>#<condition>`, not one run per SNR: one resume state,
  one budget estimate, and conditions of a clip pair directly for the doubling SNR CI.
- Sweep and long-form runs are written to their own folders through a Settings subclass that moves
  `runs_dir`, so they never appear as leaderboard runs or as `latest`.
- Clean clips exclude noise as well as music, so the clean baseline is clean.
- 2 s pads of bed-only audio around each clip: known non-speech on every clip, so hallucination over music
  and noise is measured per SNR even without reference word timings.
- Bed applied dry after reverb: background music in a Short comes from the edit, not the room.
- Doubling threshold uses the clean rate floored at 1%: a near perfect clean score would otherwise make
  any single error a "doubling".
- Beds from Wikimedia Commons (stable URLs, license and sha1 in the file metadata) plus two synthetic
  noises; MUSAN was not used because it only ships as one 11 GB archive with per file licenses.
- Long-form timing anchors are creator caption line starts (creator tracks have no word timings).
- Long-form calls get a per instance timeout of the longest video plus 10 minutes.
- Compute runs each local model in a subprocess so peak memory is per model.
