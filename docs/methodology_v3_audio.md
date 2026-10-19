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
