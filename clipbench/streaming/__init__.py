"""Streaming track (A1, docs/V3.md): realtime speech-to-text over WebSocket, scored on its own.

Pieces:

- `base`: the `StreamingProvider` interface, the connection protocol and the clock.
- `pacer`: reads a 16 kHz WAV as PCM and sends it in real-time-sized chunks.
- `session`: runs one clip through one provider and records every partial and final with its time.
- `metrics`: time to first partial, time to final segment, partial stability, final error rate, timing.
- `runner`, `score`, `export`, `cli`: `clipbench run-stream`, `clipbench score-stream`, the export hook.
- one adapter module per provider; `registry` maps ids to classes.

Streaming numbers never share a file, a table or a leaderboard with batch numbers: a streaming run is its own
run kind (`run_meta.kind = "stream"`) and writes `stream_rows.parquet` instead of `rows.parquet`.
"""
