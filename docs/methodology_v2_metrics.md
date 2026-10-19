# v2 metrics methodology

Additions to the scoring methodology (docs/methodology_scoring.md) for clipbench v2. Code:
`clipbench/scoring/{timing,readiness,codeswitch,normalize}.py`, `clipbench/align.py`. Every number below is
computed per (provider, clip) on repeat 0 and written to `results/<run_id>/scores.parquet`; aggregates are in
`aggregates.parquet`.

## New languages

### English (`en`)

Metric: `wer` (primary). Order: NFKC, lowercase, bracket tags removed, numbers to words, punctuation removed,
whitespace collapsed. Tokens are whitespace separated words.

- **Numbers.** Digits are spelled out by a built in converter, without "and" or hyphens: `125` is
  `one hundred twenty five`, `21000` is `twenty one thousand`, up to 999,999,999,999 (larger numbers digit by
  digit). `1,000` is thousands, `3.14` is `three point one four`, `1st` / `22nd` are `first` / `twenty
  second`, `25%` is `twenty five percent`. Hyphens are punctuation, so `twenty-five` written as words also
  becomes `twenty five`. Known limitations: years are read as plain numbers (`1990` is `one thousand nine
  hundred ninety`, not `nineteen ninety`), `one hundred and five` keeps its `and`, and currency symbols are
  removed rather than spoken (`$5` is `five`).
- **Contractions are kept as written.** `don't` stays `don't`; it is not expanded to `do not`. An apostrophe
  between two letters is kept (curly apostrophes become `'` first); every other apostrophe or quote is
  punctuation. So `dont` vs `don't` is an error, the same way a misspelling is.

### Hindi and Hinglish (`hi`)

Metrics: `wer` (primary), `wer_translit`. Order for `wer`: NFKC, lowercase (Latin letters), bracket tags
removed, Devanagari digits `०`..`९` mapped to `0`..`9`, punctuation removed (this includes the danda `।` and
double danda `॥`, which are Unicode punctuation), whitespace collapsed. Tokens are whitespace separated
words in whatever script they were written in. Digits are not spelled out.

**Known limitation of `wer`:** Hinglish is written in both scripts. The same word in Devanagari in the
reference and in Latin in the hypothesis (`है` vs `hai`) counts as a substitution. This is why
`wer_translit` exists.

`wer_translit` takes the `wer` text and

1. transliterates Devanagari to Latin with a small built in table in the casual Hinglish style: consonants
   with their usual letters (`क` k, `छ` chh, `श` sh), vowel signs as single letters (`ा` a, `ी` i, `ू` u,
   `ै` ai), nukta forms (`ज़` z, `फ़` f), anusvara and chandrabindu as `n`. The inherent vowel is written as
   `a` after a consonant unless a vowel sign or virama follows, and is dropped at the end of a word
   (`दोस्त` is `dost`, `मेरा` is `mera`, `क्या` is `kya`, `मैं` is `main`);
2. loosens Latin spelling on both sides: `ee` to `i`, `oo` to `u`, `w` to `v`, then any doubled letter once
   (`theek` and `ठीक` both become `thik`).

This is a heuristic, not a standard romanisation: medial schwa deletion is not modelled (`कमरा` becomes
`kamara`), and loosening can make two different words equal. Treat `wer_translit` as a lenient bound on
how much of `wer` is script choice, not as a second ranking.

## Word timings for references

Timing metrics need reference word timings. They come from, in order:

1. the creator caption track, when it carries word level timings (`timing_source = "creator"`);
2. `clipbench align`, forced alignment of the verified text to the clip WAV
   (`timing_source = "aligner:<name>"`), written to `data/align/<clip_id>.json`.

### Aligner

`clipbench align` uses the MMS 300m forced aligner (`MahmoudAshraf/mms-300m-1130-forced-aligner` on Hugging
Face, pinned to commit `49402e9577`), a wav2vec2 CTC model over romanised characters trained for 1130
languages, run with PyTorch and transformers (optional extra: `uv sync --extra align`).

1. The text is split into words: whitespace words for en, es, ko, hi; fugashi (unidic-lite) tokens for ja.
   Bracket tags and punctuation-only words are dropped.
2. Each word is romanised with uroman. Japanese words are first replaced by their kana reading (the same
   reading used by `cer_reading`). English and Spanish digits are spelled out first; Hindi Devanagari digits
   become ASCII. Only the model's alphabet (a to z and apostrophe) is kept. Words left with no characters
   (for example Korean digits) get no timing and are left out of the file.
3. The audio is cut into 30 s chunks; each chunk gives per frame log probabilities (20 ms frames).
4. A CTC Viterbi pass finds the single best monotonic path of the characters through the frames. A word
   starts at the first frame of its first character and ends after the last frame of its last character.

The model weights are CC-BY-NC 4.0 (non commercial). clipbench does not redistribute them; they are
downloaded on first use. Alignment is an estimate: on music, overlapping speech or text that does not match
the audio, word times can be wrong. A reference word that was aligned badly adds the same error to every
provider, so it shifts all providers but does not reorder them by itself.

## Caption timing error

Per clip, when both the reference and the provider have word timings (null otherwise):

1. Each word on both sides is tokenised on its own with the language's primary metric tokenizer (characters
   for ja, words otherwise). A word that yields k tokens spreads them evenly over its duration: token j starts
   at `start + j / k * (end - start)`. This maps character alignment back to words for ja: a provider that
   returns `東京に` as one 600 ms word places `に` at 400 ms into it.
2. The two token lists are aligned (the same Levenshtein alignment as CER/WER).
3. A reference word is **matched** when its first token is a hit (an exact match, not a substitution).
   Timing is only measured on words the provider got right, so timing is not punished for text errors.
4. For each matched word: offset = |predicted start of the hit token - reference word start| in ms.

Per clip columns: `timing_n_words` (matched words), `timing_median_ms`, `timing_p90_ms` (90th percentile),
`timing_within_200ms` (share of matched words with offset <= 200 ms). With timings but no matched word,
`timing_n_words` is 0 and the rest are null.

Aggregates: `timing_median_ms` and `timing_p90_ms` are the **median across clips** of the per clip values, so
one long clip does not dominate; `timing_within_200ms` is word weighted (matched words within 200 ms over all
matched words); `timing_n_clips`, `timing_n_words` are counts.

## Hallucination over non-speech

**Non-speech** in a clip is:

- the reviewer's tagged `nonspeech_spans` (kinds `music`, `sfx`, `silence`), merged, when the clip has any;
- otherwise, from reference word timings: every gap longer than 1.0 s between consecutive reference words,
  the lead in from 0 to the first word when it is longer than 1.0 s, and the tail from the last word to the
  clip end when it is longer than 1.0 s;
- unknown (null) when the clip has neither.

A provider word is **hallucinated over non-speech** when its midpoint `(start + end) / 2` lies inside
non-speech. This needs provider word timings; without them the count is null.

Per clip: `nonspeech_minutes`, `halluc_nonspeech_words`, `halluc_per_nonspeech_min` = words / minutes (null
when there is no non-speech). Aggregates are corpus style: total words over total non-speech minutes, over the
clips where both are known (`nonspeech_n_clips`).

Without tags, a gap in the reference means the caption has no text there, which can still hold speech the
caption left out; on verified clips the reviewer has corrected the text, so gaps are more trustworthy.

The v1 measures (`hallucinated_tokens`, `long_output`) are unchanged.

## Caption readiness

Scored only on clips whose verified text the reviewer marked `punctuated` (null otherwise), on the
**un-normalised** text, and separately from CER/WER so that a provider is not punished twice.

**Units.** After NFKC and removing bracket tags, the text is split into units:

- ja: every character that is not whitespace or punctuation;
- en, es, ko, hi: every whitespace separated word, with leading and trailing punctuation removed.

Each unit carries the class of the punctuation right after it:

- **final**: `.` `?` `!` `…` `。` `।` `॥` (full width `！` `？` become ASCII under NFKC);
- **comma**: `,` `;` `:` `、` (full width `，` `；` `：` become ASCII under NFKC);
- none otherwise. Other marks (quotes, brackets, dashes) are ignored. If a unit is followed by both classes,
  final wins. Leading marks such as Spanish `¿` `¡` and punctuation standing alone attach to the previous unit.

**Punctuation F1.** Units are aligned on their lowercased text. At every aligned pair (hit or substitution):

- same class on both sides: one true positive;
- otherwise a class only in the hypothesis, or a different class, is a false positive, and a class only in
  the reference, or a different class, is a false negative (final vs comma is one of each).

Deleted and inserted units are ignored; WER already counts them. `punct_f1 = 2TP / (2TP + FP + FN)`, null when
there is nothing to score.

**Casing accuracy.** Over hits whose reference unit contains Latin letters: correct when the hypothesis unit
is written with exactly the same casing. For ja this is per Latin character. `casing_acc` = correct / checked,
null when nothing was checked.

Aggregates are corpus style: F1 from summed TP, FP, FN; casing from summed counts. The counts are in
`scores.parquet` as `punct_tp`, `punct_fp`, `punct_fn`, `casing_n`, `casing_correct`.

## Code-switch accuracy

The reviewer tags `codeswitch_spans`: character offsets into the verified text where the speaker switches
language (for example English inside Japanese).

1. For word metrics, each span is widened to whole words so a tag that cuts a word does not split it.
   Overlapping spans are merged.
2. The reference is tokenised piece by piece (outside, inside, outside, ...) with the metric's tokenizer and
   tokens from inside a span are flagged.
3. The flagged reference is aligned with the hypothesis. Errors on the span are: substitutions and deletions
   of flagged tokens, plus insertions whose neighbours on both sides are flagged (text edges count as
   flagged). Insertions at a span edge are not counted.

Per clip and metric: `codeswitch_ref_len` (flagged tokens), `codeswitch_errors`, `codeswitch_error_rate` =
errors / flagged tokens. Null when the clip has no spans. Aggregates are corpus style over clips with flagged
tokens (`codeswitch_n_clips`). The whole clip's CER/WER is unchanged and still includes these tokens.

## Category and audio condition

Per clip: `category` (from the dataset spec: `talking_head`, `vlog_music`, `gaming`, `comedy_skit`,
`cooking_tutorial`, `news_explainer`, `other`) and `audio_condition`: `music` if the clip is tagged
`background_music` or has any non-speech span of kind `music`, else `no_music`.

Aggregates and paired comparisons gain the slices `slice_kind = "category"` (one per category present) and
`slice_kind = "audio_condition"` (`music`, `no_music`), on top of `all`, `tag` and `duration`. The too-small
rule (fewer than 15 clips) applies to every slice.
