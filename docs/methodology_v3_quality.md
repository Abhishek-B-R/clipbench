## Quality metrics (v3)

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
substitution) to the reference units overlapping the span, plus insertions between them or right after,
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
