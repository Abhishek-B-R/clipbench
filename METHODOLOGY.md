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

## Caption quality beyond error rate

These metrics sit next to the strict CER/WER. None of them changes the strict number; each is reported
side by side, with its own counts, so the reader can see how much of a provider's error is spelling choice,
formatting style or meaning.

### Multiple valid references (lattice error rate)

Some differences between a hypothesis and the verified text are not mistakes: 今日 or きょう, "ok" or
"okay", Devanagari or Hinglish romanisation. Following OI-WER, the reference becomes a lattice of valid
paths and the hypothesis is scored against the closest one.

**Where alternatives come from.**

1. Reviewer tagged `alternatives` spans: character offsets into the verified text plus the other valid
   spellings. The text as written is always valid too. For word metrics a span is widened to whole words
   (the widened part is kept on every option). Overlapping spans keep the first one.
2. Built-in equivalences per language, applied to the text outside the tagged spans:
   - `en`: ok / okay / o.k., alright / all right, percent / per cent, US and UK spellings (gray / grey,
     color / colour, favorite / favourite, center / centre, theater / theatre, realize / realise,
     organize / organise, mom / mum), cannot / can not, email / e-mail, online / on-line. Numerals:
     the normaliser already reads digits as cardinal words, and every year from 1100 to 2099 may also be
     read as a year ("twenty twenty four" or "two thousand twenty four"; "nineteen oh five").
   - `es`: ok / okay / okey / oki, the accent pairs the RAE made optional (solo / sólo, este / éste,
     esta / ésta, ese / ése, esa / ésa, aquel / aquél, guion / guión, truhan / truhán), periodo / período,
     septiembre / setiembre, video / vídeo, wifi / wi-fi, email / e-mail.
   - `ko`: loanword spellings, standard form and common variant: 케이크 / 케익, 파이팅 / 화이팅,
     카페 / 까페, 주스 / 쥬스, 메시지 / 메세지, 콘텐츠 / 컨텐츠, 액세서리 / 악세사리, 초콜릿 / 초콜렛,
     리더십 / 리더쉽, 로봇 / 로보트, and 오케이 / 오키 / ok / okay.
   - `ja` (`cer`): each morpheme (fugashi with unidic-lite on the normalised text) may be written as its
     surface, in hiragana or in katakana (from the dictionary reading), so kanji versus kana choices are
     not errors. Digit runs may also be written as their reading or as a kanji numeral (3 / さん / 三,
     2024 / 二千二十四). ok / okay / オッケー / オーケー. `cer_reading` already compares readings.
   - `hi` (`wer`): a reference word and a hypothesis word match when their loose romanisations are equal
     (the `wer_translit` transliteration plus ee to i, oo to u, w to v and doubled letters once), so
     है and hai match. ok / okay.

**Scoring.** The lattice is a chain of segments, each holding one or more token paths. One dynamic
program aligns the hypothesis to all paths at once (a Levenshtein alignment over a DAG: for each lattice
node and hypothesis position, the cheapest of hit or substitution from a predecessor, deletion of the node,
or insertion after it), so the cost grows with the number of nodes, not the number of paths. The closest
path is the one with the fewest edits. The strict path (the reference tokenised whole, exactly as for
CER/WER) is always valid, so the lattice error count is never above the strict count.

Per clip and metric `scores.parquet` adds `error_rate_lattice` = lattice errors / length of the closest
path, with the counts `lattice_ref_len`, `lattice_errors`, `lattice_hits`, `lattice_subs`, `lattice_dels`,
`lattice_ins`. Aggregates add the corpus `error_rate_lattice` (summed errors over summed closest path
lengths) with its 95% bootstrap interval `lattice_ci_low`, `lattice_ci_high`. The strict `error_rate`
columns are unchanged and stay the headline number.

### Punctuation, casing and formatting

Scored on the **un-normalised** text, separately from CER/WER. Results are in
`results/<run_id>/formatting.parquet` (per provider and clip) and `formatting_aggregates.parquet`.

**Units.** As for caption readiness: characters for ja, whitespace separated words with leading and trailing
punctuation removed elsewhere, after removing bracket annotations. Each unit carries the class of the mark
right after it: **period** (`.` `。` `…` `।` `॥`), **question** (`?`), **exclamation** (`!`) or **comma**
(`,` `、` `;` `:`); full width marks count after NFKC. When several follow one unit the strongest wins, in the
order question, exclamation, period, comma. Leading marks (Spanish `¿` `¡`) and other symbols are ignored.

**Punctuation error rate** (Picovoice style). Units are aligned on their lowercased text. At every aligned pair
(hit or substitution): a reference mark the hypothesis lacks is a deletion, a mark only in the hypothesis is
an insertion, two different classes are a substitution. `punct_error_rate` = (S + D + I) / reference marks at
aligned positions. Units present on only one side are left to WER, so a dropped word is not punished twice.
Scored only on clips whose verified text is marked `punctuated`; the rate can exceed 1 when a hypothesis adds
many marks.

**Casing accuracy** is the caption readiness measure (exact casing of hit units with Latin letters), repeated
here so the formatting table is complete.

**Inverse text normalisation (ITN).** The reviewer tags `itn_spans` (kinds number, date, time, currency,
percent, unit). For each span the hypothesis text aligned to it is found: the hypothesis units aligned (hit or
substitution) to the reference units overlapping the span, plus insertions right before, between or right after them,
widened to whole whitespace pieces so attached symbols ($, %, 円) come along. Each span gets:

- `found`: some hypothesis text aligns to it (false when every unit was deleted);
- `form_match` (the ITN accuracy): written exactly as the reference after NFKC, casefolding, collapsing spaces
  and stripping sentence punctuation from the ends;
- `style_match`: the same rendering shape, where digit runs become 9 and letter runs a ("$1,000" is `$9,9`,
  "March 3, 2024" is `a9,9`, "three" is `a`), so digits versus words, date order and separators, and symbol
  position are compared without the exact value;
- `value_match`: the same content: equal after the language's metric normaliser, or the same numeric values
  ("$5" and "$5.00", "3/14" and "3-14").

`itn_form_acc` = form matches / spans. Aggregates: corpus rates (summed counts) with 95% clip bootstrap
intervals for `punct_error_rate`, `casing_acc` and `itn_form_acc`, plus `itn_style_acc`, `itn_value_acc` and
`itn_found_rate`; slices `all`, `condition`, `category`, and `slice_kind = itn_kind` (one row per kind present)
for the per kind breakdown. A written form is a style choice as much as an error: the table says which
providers write numbers the way the reference does, not which are wrong.

### Proper nouns and brands

The reviewer tags `entity_spans` (types person, brand, place, product, other) in the verified text. Each
span is located in the hypothesis the same way as an ITN span (aligned units, widened to whole words) and
scored, per provider and entity, in `results/<run_id>/entities.parquet`:

- `exact`: the hypothesis text equals the entity after the language's metric normaliser with all spaces
  removed (so "iPhone", "i phone" and "IPHONE" match), or equals the reference widened to the same whole
  words (so a possessive or particle attached to the name does not break the match);
- `lenient`: 1 minus the character edit distance over the entity's characters, floored at 0, the partial
  credit for a near miss ("Appel" for "Apple" scores 0.6);
- `anywhere`: the normalised entity appears anywhere in the normalised hypothesis, a check that does not
  depend on the alignment;
- for hi, a Devanagari entity also matches its loose romanisation (दिल्ली and dilli).

**Keyterms.** Each row carries the run options (`options` json). A row ran with keyterms when `options`
has a non-empty `keyterms` entry (the same term list for every provider that supports custom vocabulary).
Aggregates in `entities_aggregates.parquet` are per provider, tier, language and `keyterms` (true or false),
with slices `all`, `condition`, `category` and `slice_kind = entity_type`: `exact_acc` and `lenient_acc` are
corpus rates over entities with 95% clip bootstrap intervals, plus `anywhere_rate` and `found_rate`.

**Keyterms on versus off.** When the same clips were run with and without keyterms (in this run or another
run of the same language under `results/`), entities are paired by (provider, clip, entity) and a paired clip
bootstrap compares the miss rates: `diff` = miss rate with keyterms minus without, with its interval;
`verdict` `a_better` means keyterms helped. A provider that does not support keyterms records that in its raw
response and shows no difference.

### Fillers, stutters and profanity (style, not errors)

Caption apps choose differently here: some keep "um" and stutters for a verbatim feel, most drop them for a
clean read; some write profanity, others mask it. These are reported as **style properties** of each
provider, never as errors, in `results/<run_id>/style.parquet` (per provider and clip) and
`style_aggregates.parquet`.

**Tokens.** Un-normalised text after NFKC, with bracket annotations removed. Bleep markers (`[ __ ]` as
YouTube writes masked words, `[bleep]`, `[beep]`, `[censored]`, `[expletive]`, `[pitido]`, `[삐]`, `[ピー]`)
become a masked token first. en, es, ko, hi: whitespace separated words, lowercased, with edge punctuation
removed; a word containing `*`, `＊`, `●`, `○`, `■`, `□`, `×` or `#` is masked ("f***", "sh*t"). ja:
fugashi morphemes, with the filler forms below matched first so えーと stays one token, and runs of the mask
characters becoming a masked token.

**Fillers.** Always fillers:

- en: um, umm, uh, uhh, uhm, er, erm, hmm, hm, mm, mmm
- es: eh, ehh, em, emm, ehm, mm, mmm, hmm
- ko: 음, 으음, 어, 어어, 에, 흠, 엄
- hi: um, umm, uh, hmm, अं, उम्म, हम्म, अ
- ja: えーっと, えーと, ええと, えっと, えと, えー, えぇ, あのー, あのう, そのー, うーん, んー, まー

Fillers only when followed by a pause (a comma, ellipsis, sentence mark or another filler), because they are
also ordinary words: en like; es este, pues, bueno; ko 그, 저, 뭐, 막, 아, 저기; hi matlab, मतलब, woh, wo,
वो, yaani, यानी, basically; ja あの, その, まあ, なんか (also at the end of the text). This undercounts
unpunctuated hypotheses, which is why keep rates are computed against the reference (below).

**Repetitions and stutters.** A token equal to the previous one ("I I think"), or a hyphen stutter ("b-but",
counted as the word after the hyphen); for ja a one character token, a pause and a word starting with that
character (わ、わたし). Fillers and masked tokens are not repetitions.

**Profanity.** Prefix match on the lowercased word (substring for ja and ko) against short per language
lists: en fuck, motherfuck, shit, bullshit, bitch, bastard, asshole, dick, cunt, damn, goddamn, piss, crap,
wtf; es mierda, puta, puto, joder, jodid-, coño, carajo, pendej-, cabrón, chingar, chingad-, verga,
gilipollas, hostia, culero; ko 씨발, 시발, 씨바, 존나, 졸라, 개새끼, 새끼, 병신, 지랄, 젠장, ㅅㅂ, ㅈㄴ;
hi chutiya, chutiye, bhenchod, behenchod, madarchod, gaandu, gandu, harami, kamina, kamine and their
Devanagari spellings; ja くそ, クソ, 糞, ちくしょう, チクショウ, 畜生, くたばれ, ふざけんな, ぶっ殺.

**Per clip.** Reference and hypothesis tokens are aligned (profane and masked tokens share one alignment key,
so a masked word lines up with the word it hides). Then:

- `filler_keep_rate` = reference fillers whose aligned hypothesis token is a filler / reference fillers, and
  `hyp_fillers_per_min` = hypothesis fillers per minute of audio (works without reference fillers);
- `repetition_keep_rate` = reference repetitions kept (both words hit, and the hypothesis repeats too; a
  hyphen stutter must be written as a stutter) / reference repetitions;
- for each profane reference word: `written` (aligned to a profane word), `masked` (aligned to a masked
  token), `dropped` (deleted) or `replaced` (aligned to some other word); for each bleep in the reference:
  masked, dropped or other; plus hypothesis masked tokens and profane words.

**Aggregates.** Corpus keep rates with 95% clip bootstrap intervals, fillers and masked tokens per minute, the
share of reference profanity written, masked, dropped and replaced, and plain labels: `filler_style` and
`repetition_style` are `keeps` (keep rate at least 0.8), `drops` (at most 0.2) or `mixed`, and
`profanity_style` is the most common outcome; each label is null below 5 reference occurrences. Slices `all`,
`condition` and `category`.

### Semantic WER (LLM judge)

CER and WER count every token alike; a viewer cares whether the caption says what was said. An LLM judge
scores, for each verified (reference, hypothesis) pair, how well the hypothesis keeps the meaning of the
reference, from 0 to 1, with a one line reason.

**Judge.** Cloudflare Workers AI, default model `@cf/google/gemma-4-26b-a4b-it` (Gemma 4, multilingual,
USD 0.10 per million input tokens and 0.30 per million output tokens, billed in neurons at USD 0.011 per 1,000,
checked 2026-10-19 on the Workers AI pricing page). Alternatives with prices in `clipbench/judge/client.py`:
`@cf/meta/llama-4-scout-17b-16e-instruct`, `@cf/meta/llama-3.3-70b-instruct-fp8-fast` (sent with JSON mode),
`@cf/openai/gpt-oss-120b`. Temperature 0, fixed seed, at most 200 output tokens.

**Prompt.** Pinned in `clipbench/judge/prompts/semantic_v1.txt`; its version is the file name plus a hash of its
text, so any edit is a new version. It tells the judge to ignore punctuation, casing, spacing, script (kanji
or kana, Devanagari or romanised Hindi), digits versus words, fillers, stutters and harmless spelling variants,
and to count missing or added content words, wrong names or numbers, negation flips, hallucinated text and
wrong language, on a five point anchor scale (1, 0.75, 0.5, 0.25, 0). The answer must be JSON
`{"score", "reason"}`: a number from 0 to 1 and a non-empty reason (collapsed to one line, at most 200
characters). An invalid answer, an HTTP 429 or 5xx, or a network error is retried, three attempts in all; a
pair that never gets a valid answer is recorded as failed, not scored.

**Cost and cache.** Every request goes through the budget guard (`data/spend.json`, the same cap as provider
runs), reserved at an upper bound before it is sent (one token per three UTF-8 bytes of text plus the prompt,
and the full output allowance) and recorded at the billed token usage when the response reports it. Answers
are cached by (judge model, prompt version, reference hash, hypothesis hash), so a rerun costs nothing.
`clipbench judge run <run_id> --dry-run` prints the pair count, cached pairs and estimate and sends nothing.

**Outputs.** `results/<run_id>/semantic.parquet` per provider and clip: `semantic_score`, `semantic_error`
(1 minus the score), judge model, prompt version, cost, cached, failed. `semantic_aggregates.parquet`: the mean
score with a 95% clip bootstrap interval, `semantic_error`, and the share of clips with the full meaning
kept, sliced by `all`, `condition` and `category`. The reasons can quote the captions, so they stay in
`data/runs/<run_id>/semantic_reasons.parquet` and are never published.

**Calibration against people.** `clipbench judge label --lang L --labeller NAME` shows real pairs sampled
(seeded) from the runs on this machine, and the person types a score on the same anchor scale and an optional
note. Labels go to `data/calibration/semantic_pairs.csv` (gitignored, since they hold caption text), in the
format of `datasets/calibration/semantic_pairs.csv` (`lang, ref, hyp, human_score, labeller, note`, plus pair
and run ids). No label is ever generated. `clipbench judge calibrate` judges every labelled pair (a pair
labelled by several people uses their mean) and computes, per language, the Spearman rank correlation and the
mean absolute error between judge and human scores. Only these statistics are published
(`judge_calibration.json`).

**Publishing gate.** Semantic scores for a language are published only when its calibration, for the same
judge model and prompt version, has at least 50 labelled pairs, Spearman at least 0.7 and mean absolute error
at most 0.2. Spearman checks that the judge orders pairs as people do; the error bound checks that its scale
matches theirs. Otherwise the export carries `published: false` and the reason (for example "12 labelled
pairs, needs 50") and no scores.

### Commands and published data

`clipbench score <run_id>` adds the lattice columns; `clipbench quality <run_id>` writes the formatting,
entity and style files (free, no requests); `clipbench judge run <run_id> [--dry-run]`, `clipbench judge label`
and `clipbench judge calibrate [--dry-run]` handle semantic WER. The run export carries `lattice`,
`formatting`, `entities`, `style` and `semantic`, each null when the run has no such data, with numbers only:
no reference, hypothesis or judge reason text is ever published.

## Speakers, language ID, features and caption files

Covers B15, B16, B17 (provider side), B20 and B21. Code: `clipbench/scoring/{diarization,lid,captions}.py`,
`clipbench/providers/features.py`, the adapters in `clipbench/providers/`. Provider options and their
doc links are in docs/providers.md. Every metric reports "not enough data" (no export section) until
the inputs exist; nothing here is estimated or filled in.

### Run options

`--diarize`, `--detect-language` and `--keyterms <file>` are sent to every provider and are part of the
cache key, so runs with and without an option never share results. A provider that does not offer an
option ignores it and records the option name in `raw_response["clipbench_unsupported"]`; it is then
left out of that metric rather than scored as a failure. With `--detect-language` no language hint is
sent. With `--keyterms` the same term list goes to every provider, through its own feature (keyterm
prompting, word boost, custom vocabulary or a prompt); limits that differ per provider (term counts,
words per term, prompt length) are applied by the adapter and listed in docs/providers.md.

### Speaker diarization

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

### Language identification

On a run made with `--detect-language`:

- LID accuracy: share of clips where the detected language, normalised to ISO-639-1, equals the clip
  language. Providers that report no language on any clip do not offer detection and are left out; a
  missing answer from one that does report languages counts as wrong. 95% bootstrap interval.
- Cost of no hint: the primary error rate on this run against a hinted run (the most recent other run
  in the same language without detection that has scores for that provider), paired over the clips both
  runs scored, with a paired bootstrap of the difference. Also reported: the error rate on the clips
  where detection was wrong.

Files: `lid.parquet`, `lid_aggregates.parquet`. Language codes and numbers only.

### Feature matrix

One `[<id>.features]` table per batch provider in `pricing.toml`, each cell with its doc URL and the
date it was checked. Values come from the provider's current documentation only; where a doc does not
state something, the value is left out (unknown) and the cell's note says so. Conflicting docs are
noted, and the more conservative figure is kept. `languages` lists which of the five benchmark
languages the provider serves.

### Caption export check

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

### Decisions

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

## Robustness to music and noise, long-form, compute

To be merged into METHODOLOGY.md. Covers A5 (controlled perturbation sweeps, with B14's per-SNR
hallucination), A8 (long-form track) and A9 (speed and compute).

### Controlled perturbation sweeps

How fast each model falls apart as the music or noise under the speech gets louder. Code:
`clipbench/sweeps/`. Commands: `clipbench sweep beds | fetch-beds | build | run | score`.

#### Clean clips

Verified clips of one language that have no `background_music` or `noisy` tag, no `music` or
`outdoor_noise` condition and no non-speech span of kind `music`. Noise is excluded too (not only music)
so the clean baseline is actually clean.

#### Beds

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

#### Mixing at an exact SNR

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

#### Running

One sweep is one runner run (run id = sweep id) holding every condition, with clip ids
`<clip_id>#clean`, `<clip_id>#<kind>_snr<SNR>` and `_rev` for the reverb series (for example
`abc123#music_snr-5_rev`). One run keeps resume, the budget guard, the cache and `run_meta.json`
(`kind = "sweep"`) in one place and lets the score pair the conditions of a clip. The run lives in
`results/sweeps/<sweep_id>/` and `data/sweeps/<sweep_id>/`, never under `results/runs/`, so it can never
show up as a leaderboard run. Stability reruns are off. Paid providers go through the usual estimate
(`--dry-run`) and budget guard.

#### Scoring

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

#### Limitations

- Mixed beds are a controlled stand-in for real background music: real edits duck the music under speech
  and choose tracks that fit; a fixed-SNR bed does not.
- The reverb is synthetic and the same shape for every clip apart from the noise seed.
- Hallucination over non-speech needs provider word timings; providers without them have null there.

### Long-form track

Does a provider still work on a whole video: accuracy, word timing drift over time, and chunking errors.
A **separate track**: its runs live in `results/longform/<run_id>/` and `data/longform/`, never under
`results/runs/`, `run_meta.kind = "longform"`, and the export key is `longform` with `track =
"longform"`. Long-form numbers are never mixed into the short-form leaderboard. Code: `clipbench/longform.py`.
Commands: `clipbench longform list | fetch | run | score`.

#### Videos and reference

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

#### Metrics (primary metric of the language)

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

### Speed and compute

Code: `clipbench/compute.py`. Commands: `clipbench compute machine | bench | table`.

#### Local open models (`whisper-local`, `whisper-local-turbo`)

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

#### Every provider in a run

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

### Decisions

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

## Drift, reliability, submissions and the public API

Ops features. Covers A3, B19, A11 and A12 of docs/FEATURES-PROMPT.md. Decisions are logged at the
end of each section (this file stands in for DECISIONS.md entries; merge them there).

### Continuous reruns and drift

**Canary set.** One fixed set of 30 clips per language, `datasets/canary/<lang>.csv`, chosen once from
the language's verified clips (with audio on disk) by `clipbench canary select --lang L`: clips are
ordered by `sha256("clipbench-canary:<sample_seed>:<clip_id>")` and taken greedily with at most 3 clips per
channel, then filled from the rest if the channel cap leaves the set short. Same verified clips and seed,
same set. The set is frozen: selecting again needs `--reselect`, and history across a reselect is not
comparable. Until 30 verified clips exist the command says so and writes nothing.

**Run.** `clipbench canary run --lang L [--providers all] [--dry-run]` runs the providers whose keys are
set on the canary clips with no cache (a canary must ask the provider again) and no stability reruns.
Rows and scores go to `results/canary/runs/<run_id>/` and `results/canary/<run_id>/` (run ids
`canary-<lang>-<YYYYMMDD-HHMM>`), apart from `results/runs/`, so a canary never becomes the `latest`
benchmark run and never enters a leaderboard.

**Money.** A monthly canary cap, `canary_budget_usd_per_month` in clipbench.toml (default 2, env
`CLIPBENCH_CANARY_BUDGET_USD`). The canary spend of a month is the sum of `data/spend.json` events whose run
id starts with `canary-` in that month; the file also keeps `canary_by_month` totals. A run is refused
when estimate plus this month's canary spend is over the cap, and the runner's guard stops mid-run at the
lower of the global `budget_usd` cap and what is left this month.

**History.** `results/history/<lang>.parquet`, append-only, one row per canary run x provider x metric:
`date, provider, model_id, provider_version, metric, error_rate, ci_low, ci_high, n, run_id` (verified tier,
whole set, corpus rate with the 95% clip bootstrap CI from scoring). A run id already in the file is never
added again and existing rows are never rewritten.

**Drift.** `results/history/drift.parquet`, rebuilt from the history on every run (`clipbench canary
drift` rebuilds it by hand). Each canary point is compared with the same provider's previous canary point
for the same metric. Events (`kind`):

- `score`: the new error rate is outside the previous run's bootstrap interval `[ci_low, ci_high]`.
- `model_id`: the model id changed.
- `provider_version`: the provider-reported version changed.

Columns: `language, provider, metric, date, run_id, kind, before, after, ci_low, ci_high,
model_id_before, model_id_after, provider_version_before, provider_version_after`. A score event is a
flag to look, not a verdict: two runs of 30 clips can differ by chance, which is why the rule uses the
interval and the page shows before, after and the interval with the date.

**Scheduler.** `.github/workflows/canary.yml`. Decision: GitHub Actions cron for the schedule, running
on the owner's Mac as a self-hosted runner (label `clipbench-canary`). Why:

- Secrets, logs, manual dispatch and artifacts already live in GitHub; a Cloudflare Cron Trigger cannot
  run Python, ffmpeg or a local Whisper.
- A canary needs the audio and the verified references. References are caption text and never leave the
  owner's machine; a GitHub hosted runner would need them shipped in (secret or encrypted file), which
  breaks the "no caption text in anything public" rule in spirit. Fetching the audio on a hosted runner
  with yt-dlp would also hit YouTube's refusals of cloud IPs and redownload the same clips every week.
- So the schedule is GitHub's, the work is the Mac's, where `data/` already holds audio, references and
  the spend ledger (repository variables `CLIPBENCH_DATA_DIR` and `CLIPBENCH_RESULTS_DIR` point at them).
  If the Mac is off, the queued job expires and the next day's check catches up (the cadence is measured
  from the last canary in the history, not from the cron).

Cadence: the cron fires daily at 03:17 UTC and `clipbench canary due` runs a language only when its last
canary is at least `canary_cadence_days` old (clipbench.toml, default 7, env
`CLIPBENCH_CANARY_CADENCE_DAYS`), so changing the cadence never needs a workflow edit. Scheduled runs are
dry runs until the repository variable `CANARY_LIVE` is `true` (owner's approval for recurring spend);
`workflow_dispatch` has `lang`, `force` and `live` inputs. The workflow never runs on pull requests, and
the repository setting "Require approval for all outside collaborators" must stay on because the runner is
self-hosted. The history parquet files (numbers only) are uploaded as an artifact.

**Export.** `clipbench.canary.export_section(run_id, settings)` returns, for the run's language:

- `history`: `{language, canary_clips, canary_sha256, cadence_days, series: {provider: [{date, model_id,
  provider_version, metric, error_rate, ci_low, ci_high, n, run_id}]}}`, or null with no history.
- `drift`: `[{language, provider, metric, date, run_id, kind, before, after, model_id_before,
  model_id_after, provider_version_before, provider_version_after, ci: [low, high]}]`, or null.

### API reliability

From the numeric rows of every run (`rows.parquet`: `status, timed_out, retries, http_status,
latency_ms, cached`), per provider. Only requests that went out count: cached rows (served from the
result cache) and skipped rows (budget stop, nothing for the clip) are left out.

- `failure_rate` = errors / requests, with a 95% Wilson interval (`failure_ci_low`, `failure_ci_high`).
- `timeout_rate` = rows the runner gave up on after the job timeout / requests.
- `mean_retries`, `max_retries`: retries the adapter made (after 429, 5xx or network errors).
- `latency_p50`, `latency_p95`, `latency_p99`: milliseconds over successful requests.
- `rate_limited_share` = requests whose last HTTP status was 429 / requests. Only the last status is
  stored, so a request that hit 429 and then succeeded on retry shows up in `mean_retries`, not here.

Files: `results/<run_id>/reliability.parquet` (one row per provider for the run) and
`results/history/reliability.parquet` (per provider across every run in `results/runs/` and every canary
run in `results/canary/runs/`). `clipbench reliability [run_id|latest|all]` writes them; the export hook
and every canary run refresh them too. Columns: `provider, model_id, tier, language, slice_kind, slice,
n_runs, n_clips, n_requests, n_ok, n_errors, n_timeouts, n_rate_limited, failure_rate, failure_ci_low,
failure_ci_high, timeout_rate, mean_retries, max_retries, latency_p50, latency_p95, latency_p99,
rate_limited_share, first_run, last_run` (tier and language are null in the history file).

Decision: canary requests count towards reliability. They are real requests made the same way, and
weekly canaries are the only steady sample of a provider's uptime between full runs.

**Export.** `clipbench.scoring.reliability.export_section(run_id, settings)` returns
`reliability: {run: [rows for this run], all_runs: [rows across every run]}`, or null when there are no
runs. NaN values are null.

### Open harness and submissions

`clipbench submit --adapter path/to/module.py:Class --lang L` (clipbench/submit.py, documented for
contributors in CONTRIBUTING.md):

1. **Contract** (always first, network blocked by patching socket connect, create_connection and
   getaddrinfo; the adapter file is imported under the same block): Provider subclass with a new valid
   id; attribute types; `availability()` returns `(False, msg)` with its keys unset; `settings()` is JSON
   with a stable hash; a paid adapter has a price; a valid `Result` for 2 s of synthetic tone (shape,
   audio seconds, cost, retries, HTTP status, word timings in order and inside the audio, speakers
   parallel to words); `parse_response` raises on malformed responses; with dummy keys and no network
   `transcribe` raises within 15 s (no hang, no silent empty Result, no ProviderSkip for a failure).
   Decision: API adapters expose `fixture_response` + `parse_response(raw, audio_seconds)` so the shape
   is checked offline from the same parser `transcribe` uses; offline local models are checked through
   `transcribe` itself. Missing word timings is a warning, not a failure.
2. **Dry run** on the public canary ids of L (audio the submitter fetched into data/audio): clip count,
   missing audio, audio minutes and the estimate. No request without `--run`.
3. **Run** (`--run`, submitter's own keys): refused over `--max-usd` (default 1); mid-run the next
   request is skipped once it would cross the cap.
4. **Bundle** `submissions/<name>/`: `submission.json`, `rows.csv`, `adapter.py`. Numbers only: per clip
   status, exception type (never the message, which can echo response bodies), latency, cost, retries,
   HTTP status, detected language, hypothesis length, word count and sha256 of the hypothesis.
   Decision: no error rates in bundles. The verified references are private, so a submitter cannot
   compute them and must not type them; maintainers rerun the canary set and score it.

`clipbench submission-check <dir>|--all` is what `.github/workflows/submissions.yml` runs on pull
requests (GitHub hosted, no secrets, read-only token): exactly the three files, adapter sha256 matches,
`scored` is false, no long text fields, rows are numbers and exception names, clips are in the current
canary set and `canary_sha256` matches, the summary is recomputed from the rows, and the adapter passes
the contract offline. "Reruns the canary set before merging" (brief A11) is a maintainer step with keys
(`clipbench canary run` after adding the adapter, or the canary workflow's dispatch), never PR CI.

### Public results API and badges

Worker routes under `/api/v1` (web/worker/src/routes/results.ts), all read-only, computed on request
from the exported JSON under `/data` (index.json and runs/<run_id>.json). For each language the run is
the newest one with a verified tier, else the newest. A language that is not published (docs/V2.md) gets
`{language, run_id, published: false, status, message}` and no numbers from every route.

| route | returns |
|---|---|
| `GET /leaderboard/:lang?metric=&tier=verified&slice_kind=all&slice=all` | rows ranked by error rate with CI, n_clips, too_small; the run's metrics and available slices |
| `GET /models/:provider` | per language: provider meta, every aggregate of that provider, canary history, drift events, reliability across runs |
| `GET /history/:lang?provider=` | the run's `history` section |
| `GET /drift/:lang` | the run's `drift` events |
| `GET /reliability/:lang` | the run's `reliability` section (`run`, `all_runs`) |
| `GET /badge/:provider/:lang.svg?metric=` | SVG "clipbench, 6.2% CER ja" from the verified whole-set aggregate (primary metric by default); "not published ja" (200) for an unpublished language; "no data ja" (404) when the provider or metric has no number |

Caching: JSON is `Cache-Control: public, max-age=300`, badges `public, max-age=3600`; every response has a
content ETag and a matching `If-None-Match` gets an empty 304. CORS is open (GET only). Rate limits are the
existing ones (30 per minute per IP anonymous, 600 per API key), except badges. Decision: badges skip the
per-IP limit, because READMEs fetch them through shared image proxies (GitHub's camo), where one IP stands
for many viewers; they read two cached assets and are themselves cached for an hour. ACCOUNTS off is
unchanged: these routes live under `/api/v1`, which never builds Better Auth.

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
