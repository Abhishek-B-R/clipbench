# Canary sets

`<lang>.csv`: the fixed 30-clip canary set of a language, rerun on a cadence to catch provider drift
(docs/methodology_v3_ops.md). Ids only, never caption text or audio.

Columns: `clip_id, language, platform, source_id, start_ms, channel, category, rank_key`.

A set is chosen once, deterministically, from the language's verified clips with
`clipbench canary select --lang <lang>` (seeded hash order, at most 3 clips per channel) and then frozen.
Until a language has 30 verified clips the command says so and no file exists. `--reselect` replaces a
frozen set; history before and after a reselect is not comparable.
