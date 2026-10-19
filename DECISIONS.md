# Decisions

One line each: what was decided and why. Newest at the bottom of each section.

## Setup

- Language for the first verified set: `ja`. The build prompt left it unfilled; Japanese is what the specified stack (fugashi, unidic-lite, pykakasi) is built for, and it has the most short clips with info.json in Vako.
- Budget cap: 10 USD (the example value in the prompt, left unfilled). Change `budget_usd` in clipbench.toml.
- Settings live in `clipbench.toml`, overridable by `CLIPBENCH_*` env vars, so tests can point all paths at a tmp dir.
- Raw per-run text (hypotheses, reference snapshot) lives in `data/runs/`, not `results/runs/`: hypotheses of a good model are nearly the creator's captions, and results/ is published. `results/runs/<run_id>/` keeps only numbers.
- CLI is one typer app; each feature module registers its own commands so modules can be built independently.
