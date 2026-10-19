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
