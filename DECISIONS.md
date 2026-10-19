# Decisions

One line each: what was decided and why. Newest at the bottom of each section.

## Setup

- Language for the first verified set: `ja`. The build prompt left it unfilled; Japanese is what the specified stack (fugashi, unidic-lite, pykakasi) is built for, and it has the most short clips with info.json in Vako.
- Budget cap: 10 USD (the example value in the prompt, left unfilled). Change `budget_usd` in clipbench.toml.
- Settings live in `clipbench.toml`, overridable by `CLIPBENCH_*` env vars, so tests can point all paths at a tmp dir.
- Raw per-run text (hypotheses, reference snapshot) lives in `data/runs/`, not `results/runs/`: hypotheses of a good model are nearly the creator's captions, and results/ is published. `results/runs/<run_id>/` keeps only numbers.
- CLI is one typer app; each feature module registers its own commands so modules can be built independently.

## Review UI

- Binds 127.0.0.1 only; API docs routes are off because it is a local tool.
- A flagged (re-review) clip counts as done only if its review is newer than data/rereview.json, so an old review cannot hide a new flag.
- Skipped clips are saved with empty text and the original kept, so a creator caption can never become a verified reference by accident.
- Space types a space inside the textarea; alt+space plays/pauses anywhere and Esc leaves the textarea. Typing is the main job, so the textarea wins.
- "Save and next" jumps to the next clip that is not done, which is faster when flagged clips sit at the top.
- Unsaved edits are kept in memory per clip while navigating; only an explicit save writes a verified file.
- Tags are stored in TAGS order with duplicates removed, for stable diffs.
- Reviewer name: --reviewer, then CLIPBENCH_REVIEWER, then the OS user name.

## Scoring

- Bracketed content ([..] and (..), after NFKC) is stripped in all languages: in captions it is almost always sound tags, annotations, speaker labels or furigana.
- Punctuation is every Unicode P* and S* character. ー and 々 are letters (category Lm) and are kept.
- Punctuation becomes a space in es and ko and is deleted in ja, so "hola,amigo" does not merge into one word.
- Latin letters are lowercased in every language, since ja and ko captions contain English words.
- es `wer_no_accents` strips every combining mark, including ñ to n, so the variant isolates every diacritic-caused error.
- es "1.500" and "1,500" are read as thousands; "1" is "uno" because gender cannot be known from digits.
- ja `cer` keeps digits; `cer_reading` converts digits to readings with an in-house converter (pykakasi leaves digits alone).
- ja reading uses the unidic `kana` field (spelling based, 東京 is トウキョウ), not `pron`, because it is closer to how people write kana.
- Korean digits are not converted: Sino-Korean vs native depends on the counter.
- Headline rates are corpus rates, sum(errors) / sum(ref_len), the usual WER/CER convention; per-clip rates divide by max(ref_len, 1).
- Hallucination, timing method: a hypothesis word whose midpoint is in a reference gap over 1.0 s, or over 1.0 s outside the reference speech. Length fallback when timings are missing: max(0, hyp_len - 1.5 * ref_len); long_output means ratio > 1.5.
- Bootstrap seed is `sample_seed` from clipbench.toml, one seed for the project. Paired p = 2 x share of resamples with the opposite sign, capped at 1.
- Paired rows are ordered alphabetically (provider_a < provider_b), one stable row per pair.
- Cost per hour uses the list cost of every row, cached or not; actual money spent is tracked separately in data/spend.json.
- Clips over 90 s go in the 60-90s bucket.
- Stability disagreement is symmetric edit distance / max length between repeats, since neither repeat is the reference.
