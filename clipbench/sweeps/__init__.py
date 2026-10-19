"""Controlled perturbation sweeps (A5): clean speech mixed with music and noise beds at fixed SNRs.

- `beds`: the bed manifest (datasets/beds/beds.json), license checks, `fetch-beds`, loading beds.
- `mix`: active speech level, mixing at an exact SNR, synthetic reverb.
- `build`: pick clean verified clips, mix them, write WAVs (data/sweeps/) and recipes (results/sweeps/).
- `run`: run batch providers over a sweep through clipbench.runner.
- `score`: error rate and hallucination per provider per SNR, and the SNR where the error rate doubles.
- `export`: the `sweeps` key of the site export.

See METHODOLOGY.md.
"""
