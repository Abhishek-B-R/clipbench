# Scoring methodology

How clipbench turns a hypothesis text and a reference text into numbers. Code: `clipbench/scoring/`.
Every rule below is one function in `clipbench/scoring/normalize.py` with its own test in
`tests/test_normalize.py`.

## Normalisation

Both the reference and the hypothesis go through exactly the same steps before alignment.

### Rules shared by all languages

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

### Spanish (`es`)

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

### Japanese (`ja`)

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

### Korean (`ko`)

Order: NFKC, lowercase, bracket tags, punctuation (to spaces), whitespace collapsed.

- **`cer_nospace`.** All whitespace is then removed and tokens are characters (Hangul syllable
  blocks). Korean spacing is inconsistent in captions and transcripts, so the primary metric ignores
  it.
- **`wer_syllable`.** Spacing is kept and tokens are the space separated words (eojeol).
- **Digits.** Left as digits. Korean has two number systems (Sino-Korean and native) and choosing the
  right one depends on the counter, so no conversion is attempted.

## Metrics

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

## Hallucination

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

## Confidence intervals

Percentile bootstrap over clips: draw `n` clips with replacement, compute the corpus rate, repeat 1000
times, take the 2.5th and 97.5th percentiles. Resampling uses a numpy `Generator` seeded from
`sample_seed` in `clipbench.toml`, so the same inputs give the same intervals.

## Paired comparisons

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

## Which rows are scored

- Repeat 0 rows with status `ok`. Skipped and errored rows are not scored; a provider is only
  compared on the clips it returned. An `ok` row with no stored text is scored as an empty
  hypothesis (all deletions).
- Reference tiers (`verified`, `creator`) are never mixed: every aggregate and comparison is within
  one tier. Results on the `creator` tier are labelled "unverified reference".

## Slices

Each aggregate and paired comparison is computed for:

- `all`: every scored clip.
- `tag`: one slice per tag in `clipbench.models.TAGS` that at least one clip has (tags come from the
  reference snapshot).
- `duration`: `<15s`, `15-30s`, `30-60s`, `60-90s` by clip duration (`duration_s`, or
  `audio_seconds` when that is missing). Clips longer than 90 s go in `60-90s`.

**Too small.** A slice with fewer than 15 clips is marked `too_small` and should not be used for a
ranking. It is still reported.

## Cost and speed

- `cost_usd_total` is the sum of `cost_usd` over the slice. `cost_usd` is the provider's list cost for
  the clip, recorded even when the result came from the cache, so cached reruns do not make a provider
  look free. (Actual spend, which excludes cached results, is tracked separately in `data/spend.json`.)
- `audio_hours = sum(audio_seconds) / 3600`; `cost_per_hour = cost_usd_total / audio_hours`.
- `latency_p50`, `latency_p95`: percentiles of `latency_ms`.
- `rtf`: mean over clips of `(latency_ms / 1000) / audio_seconds`.

## Stability

Repeats 1 to 3 of a clip (status `ok`) are compared pairwise with the primary metric tokenizer.
Pairwise disagreement is `edit_distance(x, y) / max(len(x), len(y), 1)`, which is symmetric because
neither repeat is the reference. `text_disagreement` is the mean over all pairs. `latency_mean` and
`latency_std` (sample standard deviation) are over the same repeats.
