# Methodology: diarization, language identification, feature matrix, caption export (v3)

Covers B15, B16, B17 (provider side), B20 and B21. Code: `clipbench/scoring/{diarization,lid,captions}.py`,
`clipbench/providers/features.py`, the adapters in `clipbench/providers/`. Provider options and their
doc links are in docs/providers.md. Every metric reports "not enough data" (no export section) until
the inputs exist; nothing here is estimated or filled in.

## Run options (B15, B16, B17)

`--diarize`, `--detect-language` and `--keyterms <file>` are sent to every provider and are part of the
cache key, so runs with and without an option never share results. A provider that does not offer an
option ignores it and records the option name in `raw_response["clipbench_unsupported"]`; it is then
left out of that metric rather than scored as a failure. With `--detect-language` no language hint is
sent. With `--keyterms` the same term list goes to every provider, through its own feature (keyterm
prompting, word boost, custom vocabulary or a prompt); limits that differ per provider (term counts,
words per term, prompt length) are applied by the adapter and listed in docs/providers.md.

## Speaker diarization (B15)

Inputs: reference speaker turns tagged in the review UI, and the provider's words with per word speaker
labels. Only clips with speaker turns count.

- Hypothesis speech: each labelled word is an interval of its speaker. Consecutive words of the same
  speaker are joined when the gap between them is 500 ms or less, since word timings leave small gaps
  that are not pauses. Words without a label are dropped.
- Time is scored on a 10 ms grid. Frames within 250 ms of any reference turn boundary are not scored
  (the standard forgiveness collar, on both sides of each boundary), because a turn boundary cannot be
  placed more precisely than that by a human tagger.
- In each scored frame, with N_ref reference and N_hyp hypothesis speakers active: missed speech is
  max(0, N_ref minus N_hyp), false alarm is max(0, N_hyp minus N_ref), and speaker confusion is
  min(N_ref, N_hyp) minus the number of reference speakers whose mapped hypothesis speaker is active.
- The mapping between reference and hypothesis labels is one to one and maximises the total overlap
  (Hungarian assignment on the overlap matrix), so a provider is never penalised for its label names.
- DER = (missed + false alarm + confusion) / total reference speaker time, over scored frames.
  Overlapping speech counts once per speaker. Aggregates are corpus style (summed over clips) with a
  95% bootstrap interval over clips.
- Speaker count accuracy: share of clips where the number of distinct labelled hypothesis speakers
  equals the number of reference speakers, with a bootstrap interval.
- A provider is scored on a clip when it returned labels, or when it returned nothing at all while it
  does label speakers on other clips (that empty output is all missed speech).

Files: `diarization.parquet` (per clip: ref and hyp speaker counts, scored, missed, false alarm and
confusion ms, DER) and `diarization_aggregates.parquet` (per provider, slices all and condition).

## Language identification (B16)

On a run made with `--detect-language`:

- LID accuracy: share of clips where the detected language, normalised to ISO-639-1, equals the clip
  language. Providers that report no language on any clip do not offer detection and are left out; a
  missing answer from one that does report languages counts as wrong. 95% bootstrap interval.
- Cost of no hint: the primary error rate on this run against a hinted run (the most recent other run
  in the same language without detection that has scores for that provider), paired over the clips both
  runs scored, with a paired bootstrap of the difference. Also reported: the error rate on the clips
  where detection was wrong.

Files: `lid.parquet`, `lid_aggregates.parquet`. Language codes and numbers only.

## Feature matrix (B20)

One `[<id>.features]` table per batch provider in `pricing.toml`, each cell with its doc URL and the
date it was checked. Values come from the provider's current documentation only; where a doc does not
state something, the value is left out (unknown) and the cell's note says so. Conflicting docs are
noted, and the more conservative figure is kept. `languages` lists which of the five benchmark
languages the provider serves.

## Caption export check (B21)

Each provider's timed words become captions with one fixed rule, and the reference words (creator
timing or the forced aligner) go through the same rule:

- at most 2 lines per cue, at most 42 characters per line for en, es and hi and 16 for ja and ko;
- a new cue after a pause of 700 ms or more, after sentence final punctuation, before the cue would
  pass 7 seconds, or before its text would no longer fit in two lines;
- a two line cue splits at the word boundary closest to the middle, preferring one after a comma;
- cue start and end are the first word's start and the last word's end.

Scores per clip: for each reference cue, the provider cue with the most time overlap gives the start
and end error (median per clip, and median of clip medians in aggregates); the overlap ratio is the
time intersection over union of all provider cue time and all reference cue time (corpus style, with a
bootstrap interval). Readability: characters per second per cue against 17 for en, es and hi, 4 for ja
and 12 for ko (counted without spaces for ja and ko), the share of cues over that limit (with an
interval, and the same share for the reference cues as a baseline), the share of lines over the line
limit, and provider cues per reference cue. Providers without word timings are not scored.

Files: `captions.parquet`, `captions_aggregates.parquet`. SRT and VTT files contain caption text and
are written only under `data/captions/<run_id>/` (never published).

## Decisions

- Retries and status are captured in one place (a wrapper on every adapter's `transcribe()`), so new
  adapters get them for free and error rows carry them too.
- Deepgram uses `diarize_model=v2`, pinned rather than `latest`, so reruns stay comparable.
- AssemblyAI keyterms are capped at 200, the Universal-2 limit, because Korean falls back to it and the
  same list must reach every clip.
- ElevenLabs keyterm surcharge (20%, and a 20 s minimum with more than 100 terms) is in the cost
  estimate, so the budget guard sees it.
- AWS custom vocabulary is not used: it needs a persistent account resource, and the benchmark creates
  nothing that outlives a request. Hindi (hi-IN) was added to the AWS language codes.
- Gemini Flash diarization is not scored: it returns no word timings, so there is nothing to align
  speaker turns to. Language detection uses a JSON reply with a schema rather than free text.
- Workers AI `@cf/openai/whisper` auto detects but reports no language, so it is recorded as not
  supporting detection instead of scoring every clip as unknown.
- Detected language is only filled on detection runs, so a hinted run never looks like a LID result.
- Diarization joins same speaker words across gaps of up to 500 ms; without it, every gap between
  words would count as missed speech against continuous reference turns.
- The caption rule uses one set of numbers for every provider; the numbers are common caption limits,
  not tuned on any provider's output.
