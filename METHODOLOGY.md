# Methodology

How clipbench builds its test set, runs each system, and turns outputs into the numbers in the report.
Every rule here is code in this repo with a test; the file named in each section is the source of truth.

## What is measured

Speech-to-text for video captions: real short-form clips (Shorts, Reels, TikToks) with music beds,
sound effects, jump cuts, fast stylised delivery, slang, several speakers and code-switching. Each
system is scored on what a captioning product ships: text accuracy (CER or WER), word timing,
hallucination over music and silence, caption readiness (punctuation and casing), accuracy on
code-switched spans, cost per audio hour and latency. Languages: English, Japanese, Korean, Spanish,
and Hindi with Hinglish.

## Ground truth tiers

Kept separate in every table, chart and test.

| tier | what | used for |
|---|---|---|
| `verified` | a creator-uploaded caption, or a draft, that a person checked against the audio in the review UI (`clipbench review`) and corrected | everything published |
| `creator` | a creator-uploaded caption nobody has checked | pipeline checks, labelled "unverified reference" |

YouTube's automatic captions and Wayin output are never references. They are systems under test.
In the review UI the reviewer also sets the clip's content category, marks music, sound-effect and
silence stretches on the waveform, marks spans in a second language, and confirms whether punctuation
and casing were checked. Every save records the reviewer, time and audio hash.

## When a language is published

A language gets a ranking only when it has at least 100 verified clips and at least 6 providers
scored on them (local Whisper, YouTube auto captions and four paid APIs). Below that the site shows
the method, the dataset and progress, never a ranking. Comparisons between two models are stated only
when the paired bootstrap test supports them.

## Where the clips come from

The dataset is a list of public clip ids per language, `datasets/<lang>/clips.csv` (platform, video
id, optional window, channel, content category, license note, which importer added it). Anyone can
rebuild the audio from it. Importers add rows: `clipbench data import-ids` takes any CSV of ids,
`clipbench data discover` finds Shorts with creator-uploaded captions from a channel list, and other
importers read existing caption collections.

Caption track kind (creator, automatic, translation) is decided only from each video's metadata
(`subtitles` vs `automatic_captions` in yt-dlp's info.json), never from file names. Full rules:
[docs/data_rules.md](docs/data_rules.md).

Diversity rules, enforced by `clipbench data check`: per language at least 30 channels, no channel above
5% of the set, and at least 6 content categories (talking head, vlog with music, gaming, comedy or
skit, cooking or tutorial, news or explainer). When a long video has creator captions, a 20 to 60 second
window aligned to caption lines can stand in for a Short; each clip records whether it is a short or a
window, and results can be sliced by it.

Audio is fetched audio only, one download at a time, at least 10 seconds apart, and converted to
16 kHz mono WAV. The sha256 of every file is recorded; a verified reference is only used with the exact
audio its reviewer heard.

## Systems under test

One adapter per system behind one interface (`clipbench/providers/`). Model ids, endpoints and per
minute prices were checked against each provider's docs on the date recorded in
`clipbench/providers/pricing.toml`; see [docs/providers.md](docs/providers.md). Wherever a provider
offers a training or retention opt-out, it is set. Results are cached by (provider, model, audio hash,
settings hash), so reruns never bill twice. A budget guard refuses a run whose estimate plus past
spend exceeds the cap, and stops mid-run before crossing it.

Latency is wall-clock per request from this machine, including upload and polling, excluding our own
retry backoff. For stability, a seeded 20 clip subset is rerun 3 times per paid provider.

## Scoring

How clipbench turns a hypothesis text and a reference text into numbers. Code: `clipbench/scoring/`.
Every rule below is one function in `clipbench/scoring/normalize.py` with its own test in
`tests/test_normalize.py`.

### Normalisation

Both the reference and the hypothesis go through exactly the same steps before alignment.

#### Rules shared by all languages

1. **NFKC.** Unicode NFKC normalisation. Full width letters and digits become ASCII (`ＡＢＣ１２３` to
   `ABC123`), half width katakana becomes full width, full width brackets become ASCII brackets.
2. **Lowercase.** Applies to Latin letters (also inside Japanese and Korean text).
3. **Bracket tags removed.** Anything inside `[...]` or `(...)` is removed together with the brackets,
   nested brackets included. This covers sound tags (`[音楽]`, `[Música]`, `[음악]`) and annotations
   (`(笑)`, `(risas)`, speaker labels, furigana in parentheses). Because NFKC runs first, full width
   `［］` and `（）` are covered too. Decision: round brackets are treated like square ones because in
   captions they almost always hold non-speech, a speaker label or a reading that would otherwise be
   counted twice. Japanese quote brackets `「」『』【】` are ordinary punctuation (rule 4): the brackets
   go, their content stays.
4. **Punctuation and symbols removed.** Every character whose Unicode category is punctuation (`P*`) or
   symbol (`S*`) is removed. This includes `¿ ¡ , . ! ?`, `、 。 「 」 『 』 ・ 〜`, `♪` and `※`. The
   Japanese long vowel mark `ー` (category `Lm`) and the iteration mark `々` are letters, not
   punctuation, and are kept. For Spanish and Korean the removed character is replaced by a space
   (so `hola,amigo` is two words); for Japanese it is deleted.
5. **Whitespace.** Collapsed to single spaces and trimmed (Spanish, Korean word metrics) or removed
   entirely (Japanese, Korean character metric).

#### Spanish (`es`)

Order: NFKC, lowercase, bracket tags, numbers to words, punctuation, whitespace.

- **Numbers to words.** Digits are spelled out with a small built in converter so `3` and `tres`
  match. Integers 0 to 999,999,999,999 are supported (`25` to `veinticinco`, `100` to `cien`,
  `101` to `ciento uno`, `21000` to `veintiún mil`, `1000000` to `un millón`); larger numbers are read
  digit by digit. `uno` is shortened to `un` (and `veintiuno` to `veintiún`) before `mil`, `millón`
  and `millones`. A standalone `1` is `uno`; gender (`una`) cannot be known from digits and is not
  modelled.
- **Thousands and decimals.** `1.500` and `1,500` (one or more groups of exactly three digits) are
  read as thousands. Any other `d,d` is a decimal read as `coma` (`3,5` to `tres coma cinco`) and
  `d.d` as `punto`.
- **Percent.** `25%` becomes `veinticinco por ciento`.
- **Tokens.** Whitespace separated words.
- **No accents variant.** After the steps above, every combining mark is removed: `á` to `a`, `ü` to
  `u`, and also `ñ` to `n`. Decision: `ñ` is stripped too, so the variant measures errors that are not
  caused by any diacritic. Accents are kept in the primary metric because they carry meaning in
  Spanish (`si` vs `sí`).

#### Japanese (`ja`)

Order for `cer`: NFKC, lowercase, bracket tags, punctuation (deleted), all whitespace removed.

- **Tokens.** Characters.
- **Digits.** Left as digits after NFKC (`２０２４年` becomes `2024年`). The primary metric therefore
  counts `2024` vs `二千二十四` as a difference; the reading metric does not.

Order for `cer_reading`: NFKC, lowercase, bracket tags, punctuation (to spaces), digits to reading,
reading conversion, katakana to hiragana, punctuation and whitespace removed.

- **Digits to reading.** ASCII digit runs are replaced by their hiragana reading with a small built in
  converter (`2024` to `にせんにじゅうよん`), handling the common sound changes in hundreds and
  thousands (`さんびゃく`, `ろっぴゃく`, `はっぴゃく`, `さんぜん`, `はっせん`). Thousands commas
  (`1,000`) are dropped first. Counter specific changes (`1本` read `いっぽん`) are not modelled.
- **Reading conversion.** The text is tokenised with fugashi and the unidic-lite dictionary. Each
  token is replaced by the `kana` field of its unidic feature (the spelling based kana reading, so
  `東京` is `トウキョウ`, not the pronunciation `トーキョー`). If a token has no `kana` field (unknown
  words), pykakasi is used; if that also fails, the surface is kept.
- **Katakana to hiragana.** Every katakana letter `ァ` to `ヶ` is mapped to hiragana. `ー` is kept.
- The result is that a choice between kanji and kana (`漢字` vs `かんじ`) is not an error. Readings
  can still be wrong when the analyser picks the wrong reading for a homograph; that noise affects all
  providers equally.

#### Korean (`ko`)

Order: NFKC, lowercase, bracket tags, punctuation (to spaces), whitespace collapsed.

- **`cer_nospace`.** All whitespace is then removed and tokens are characters (Hangul syllable
  blocks). Korean spacing is inconsistent in captions and transcripts, so the primary metric ignores
  it.
- **`wer_syllable`.** Spacing is kept and tokens are the space separated words (eojeol).
- **Digits.** Left as digits. Korean has two number systems (Sino-Korean and native) and choosing the
  right one depends on the counter, so no conversion is attempted.

### Metrics

| language | primary | secondary |
|---|---|---|
| ja | `cer` | `cer_reading` |
| es | `wer` | `wer_no_accents` |
| ko | `cer_nospace` | `wer_syllable` |

For each clip the normalised reference and hypothesis token lists are aligned with a minimum edit
(Levenshtein) alignment (jiwer, which uses rapidfuzz). The alignment gives hits `H`, substitutions
`S`, deletions `D` and insertions `I`.

- `ref_len = H + S + D`, `errors = S + D + I`.
- Per clip `error_rate = errors / max(ref_len, 1)`. With an empty reference this is the number of
  inserted tokens.
- The headline rate of a provider over a set of clips is the **corpus rate**:
  `sum(errors) / sum(ref_len)`. Long clips weigh more than short ones. It is not the mean of per clip
  rates.

When several minimum edit alignments exist, the split between S, D and I can differ, but the total
`errors` is always the edit distance.

### Hallucination

Two measures per clip, in metric tokens (characters for ja and ko `cer_nospace`, words otherwise).

1. **Speech free insertions (when timings exist).** If the reference has word timings and the
   hypothesis has word timings, a hypothesis word is hallucinated when its midpoint lies
   - inside a gap between reference words longer than 1.0 s, or
   - more than 1.0 s before the first reference word starts, or
   - more than 1.0 s after the last reference word ends.

   Each such word counts as the number of metric tokens it contains. The reference can only
   tell us where the caption has text, so "gap" means a stretch with no caption text, which may still
   contain music or speech that the caption left out.
2. **Long output (always).** `length_ratio = hyp_len / max(ref_len, 1)` and `long_output` is true when
   `length_ratio > 1.5`. Repetition loops and invented text push the ratio well above 1.

`hallucination_rate = hallucinated_tokens / max(ref_len, 1)`.

When timings are missing on either side, `hallucinated_tokens = max(0, hyp_len - 1.5 * ref_len)`: the
token excess beyond the long output threshold. This is a lower bound: it only sees hallucination that
makes the output long, never hallucination that replaces real speech. Aggregates report the corpus
hallucination rate `sum(hallucinated_tokens) / sum(ref_len)` and `long_output_rate`, the share of
clips flagged long.

### Confidence intervals

Percentile bootstrap over clips: draw `n` clips with replacement, compute the corpus rate, repeat 1000
times, take the 2.5th and 97.5th percentiles. Resampling uses a numpy `Generator` seeded from
`sample_seed` in `clipbench.toml`, so the same inputs give the same intervals.

### Paired comparisons

For every pair of providers, per tier, metric and slice, over the clips both providers scored:

- `diff = rate_a - rate_b`, both corpus rates over the shared clips. Negative means A makes fewer errors.
- Paired bootstrap: each of 1000 resamples draws one set of clip indices and applies it to both
  providers, then computes the difference of the two corpus rates. The 95% CI is the 2.5th and 97.5th
  percentiles of those differences.
- `p_value`: twice the share of resampled differences whose sign is opposite to the observed `diff`
  (zero counts as opposite), capped at 1. When `diff` is exactly 0, p is 1.
- `verdict`: `a_better` if the CI is entirely below 0, `b_better` if entirely above 0, otherwise
  `no_difference`.

Providers are ordered alphabetically, so `provider_a < provider_b`.

### Which rows are scored

- Repeat 0 rows with status `ok`. Skipped and errored rows are not scored; a provider is only
  compared on the clips it returned. An `ok` row with no stored text is scored as an empty
  hypothesis (all deletions).
- Reference tiers (`verified`, `creator`) are never mixed: every aggregate and comparison is within
  one tier. Results on the `creator` tier are labelled "unverified reference".

### Slices

Each aggregate and paired comparison is computed for:

- `all`: every scored clip.
- `tag`: one slice per tag in `clipbench.models.TAGS` that at least one clip has (tags come from the
  reference snapshot).
- `duration`: `<15s`, `15-30s`, `30-60s`, `60-90s` by clip duration (`duration_s`, or
  `audio_seconds` when that is missing). Clips longer than 90 s go in `60-90s`.

**Too small.** A slice with fewer than 15 clips is marked `too_small` and should not be used for a
ranking. It is still reported.

### Cost and speed

- `cost_usd_total` is the sum of `cost_usd` over the slice. `cost_usd` is the provider's list cost for
  the clip, recorded even when the result came from the cache, so cached reruns do not make a provider
  look free. (Actual spend, which excludes cached results, is tracked separately in `data/spend.json`.)
- `audio_hours = sum(audio_seconds) / 3600`; `cost_per_hour = cost_usd_total / audio_hours`.
- `latency_p50`, `latency_p95`: percentiles of `latency_ms`.
- `rtf`: mean over clips of `(latency_ms / 1000) / audio_seconds`.

### Stability

Repeats 1 to 3 of a clip (status `ok`) are compared pairwise with the primary metric tokenizer.
Pairwise disagreement is `edit_distance(x, y) / max(len(x), len(y), 1)`, which is symmetric because
neither repeat is the reference. `text_disagreement` is the mean over all pairs. `latency_mean` and
`latency_std` (sample standard deviation) are over the same repeats.

## Caption metrics

Additions to the scoring methodology (docs/methodology_scoring.md) for clipbench v2. Code:
`clipbench/scoring/{timing,readiness,codeswitch,normalize}.py`, `clipbench/align.py`. Every number below is
computed per (provider, clip) on repeat 0 and written to `results/<run_id>/scores.parquet`; aggregates are in
`aggregates.parquet`.

### English and Hindi normalisation

#### English (`en`)

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

#### Hindi and Hinglish (`hi`)

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

### Word timings for references

Timing metrics need reference word timings. They come from, in order:

1. the creator caption track, when it carries word level timings (`timing_source = "creator"`);
2. `clipbench align`, forced alignment of the verified text to the clip WAV
   (`timing_source = "aligner:<name>"`), written to `data/align/<clip_id>.json`.

#### Aligner

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

### Caption timing error

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

### Hallucination over non-speech

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

### Caption readiness

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

### Code-switch accuracy

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

### Category and audio condition

Per clip: `category` (from the dataset spec: `talking_head`, `vlog_music`, `gaming`, `comedy_skit`,
`cooking_tutorial`, `news_explainer`, `other`) and `audio_condition`: `music` if the clip is tagged
`background_music` or has any non-speech span of kind `music`, else `no_music`.

Aggregates and paired comparisons gain the slices `slice_kind = "category"` (one per category present) and
`slice_kind = "audio_condition"` (`music`, `no_music`), on top of `all`, `tag` and `duration`. The too-small
rule (fewer than 15 clips) applies to every slice.

## What is published

Code, video ids, per-clip scores, aggregate tables, charts, and error examples of at most a few words.
Audio, caption text and references stay in `data/`, which is gitignored. `clipbench publish --check`
fails if anything under `site/`, `results/` or the web app's data contains audio or more than 8
consecutive words of any reference. Anyone can rebuild the set from the video ids.

## Limitations

- Sample size: at 100 to 150 clips per language confidence intervals are wide, and slices under 15 clips are marked too small to conclude.
- Creator captions are often not verbatim; only the verified tier is corrected, and only it is published.
- Reference word timings come from forced alignment (MMS 300m forced aligner), which has its own error; timing numbers are comparisons between providers against the same alignment, not absolute truth. The aligner weights are licensed CC-BY-NC 4.0.
- Providers change models without notice; model ids and the date each was checked are in every run's `run_meta.json`.
- Prices are list prices as of the checked date, not what any customer actually pays.
- Clips are what creators chose to caption, which skews toward channels that upload captions.
