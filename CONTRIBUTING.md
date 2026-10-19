# Contributing to clipbench

clipbench is an independent benchmark of speech-to-text for captions on short-form video. Contributions
are welcome as code (fixes, new metrics, new provider adapters) and as submissions (a new provider's
results on the public canary set). Everything below runs with `uv` and Python 3.12:

```sh
uv sync
uv run pytest -q
uv run ruff check .
```

## Honesty rules

These are not negotiable, for maintainers as much as for contributors.

- **No fabricated numbers.** Every number comes from a run the harness made. Never edit `rows.csv`,
  `submission.json` or any parquet file by hand; CI recomputes the summary from the rows and refuses a
  bundle whose numbers disagree.
- **No caption text, no audio.** Nothing public holds creator captions, transcripts, hypothesis text or
  audio. Clip ids are fine (they are how anyone rebuilds the set). A bundle carries a sha256 of each
  hypothesis, never the text.
- **No claims before publication.** A language gets a ranking only when it has 100 verified clips and 6
  providers with results (docs/V2.md). Until then, results are progress, not rankings.
- **Disclose ties.** If you work for, or are paid by, a provider you submit, say so in the PR.
- **Same rules for everyone.** Your adapter runs with the same settings on the same clips as every
  other provider, and the maintainers' rerun is what gets published.

## Adding a provider to clipbench itself

1. Write an adapter in `clipbench/providers/<name>.py`: a subclass of `clipbench.providers.base.Provider`
   (or `clipbench.providers.http.HttpProvider` for an HTTP API, which brings retries with backoff on 429,
   5xx and network errors, and latency measured without our own backoff sleeps). Set `id`, `env_keys`,
   `paid`, `uploads_audio`, `concurrency`, `timeout_s`, and implement
   `async transcribe(audio_path, language, *, clip=None) -> Result`.
2. Fill `Result` honestly: `text`, `words` (word timings in ms, relative to the clip, in order) when the API
   returns them, `latency_ms`, `audio_seconds` (what was billed), `cost_usd`, `model_id`,
   `provider_version` if the API reports one, `retries`, `http_status`, `detected_language`.
3. Add its price, model id, `checked_on` date and source URL to `clipbench/providers/pricing.toml`, plus the
   data policy (retention, training opt-out). A paid adapter with no price is never run.
4. Register it in `clipbench/providers/registry.py` and add a test with a recorded (synthetic) response
   under `tests/`. Tests must not need keys or the network.
5. Open a PR. A maintainer runs it on the benchmark with their keys.

## Submitting results for a new provider (open harness)

If you have an adapter for a provider clipbench does not cover, you can run it yourself on the public
canary ids and open a PR with the numbers.

### 1. Write the adapter so it can be checked offline

Your adapter is one Python file with a `Provider` subclass. On top of the rules above, the contract
needs to check your `Result` without the network, so split parsing from the request:

```python
class MyApi(Provider):
    id = "my-api"                      # new, lowercase, [a-z0-9._-]
    env_keys = ("MY_API_KEY",)
    paid = True
    uploads_audio = True

    # A recorded response for a synthetic tone (not real speech), used by the offline shape check.
    fixture_response = {"duration": 2.0, "words": [{"w": "tone", "s": 0.1, "e": 0.6}]}

    def price_per_minute(self) -> float:
        return 0.004                   # list price, so the budget guard can stop you

    def parse_response(self, raw, audio_seconds) -> Result:
        ...                            # raise on a malformed response, never return an empty Result

    async def transcribe(self, audio_path, language, *, clip=None) -> Result:
        ...                            # call the API, then return self.parse_response(body, seconds)
```

A local model that works offline can skip `fixture_response` and `parse_response`; the contract then
calls `transcribe` on fake audio directly. `tests/fixtures/submission_adapter.py` has a full example.

### 2. Validate it (no network, no keys)

```sh
uv run clipbench submit --adapter path/to/my_api.py:MyApi --validate-only
```

The contract imports your file and runs every check with outgoing connections blocked:

| check | what it wants |
|---|---|
| class, id | a `Provider` subclass with a new, valid id |
| attributes | `paid`, `uploads_audio` bools, `concurrency` 1 to 64, `timeout_s` > 0, `env_keys` a tuple |
| availability_without_keys | `availability()` returns `(False, message)` with your keys unset, without raising |
| settings | `settings()` is JSON and its hash is stable (it is part of the cache key) |
| pricing | a paid adapter has a price above 0 |
| result_shape, word_timings | a valid `Result` for 2 s of fake audio; word timings in order, inside the audio |
| error_handling_malformed | `parse_response` raises on a malformed response |
| error_handling_network | with dummy keys and no network, `transcribe` raises within 15 s (no hang, no silent empty result, no `ProviderSkip` for a failure) |
| no_network | nothing outside `transcribe` tries to connect, including importing your file |

### 3. Price it, then run it with your own keys

The canary set is `datasets/canary/<lang>.csv` (30 public clip ids per language, frozen). Fetch the audio
for those ids into `data/audio/` (see README, Dataset), then:

```sh
uv run clipbench submit --adapter path/to/my_api.py:MyApi --lang ja             # dry run: prints the cost
MY_API_KEY=... uv run clipbench submit --adapter path/to/my_api.py:MyApi --lang ja --run --max-usd 1
```

Without `--run` nothing is sent. With `--run` the harness refuses when the estimate is over `--max-usd`
and stops paying mid-run once the next request would cross it. You pay your provider; clipbench never
sees your key.

### 4. Open a PR with the bundle

`--run` writes `submissions/<name>/`:

- `submission.json`: provider and model id, settings, price, the contract report, the canary set's
  sha256, versions, and a summary (clips, ok, errors, timeouts, cost, latency).
- `rows.csv`: one row per canary clip with status, exception type, latency, cost, retries, HTTP status,
  detected language, hypothesis length, word count and a sha256 of the hypothesis.
- `adapter.py`: the exact file that ran (its sha256 is in `submission.json`).

Commit that directory as is. There are no error rates in it: the verified references are private, so
nobody outside can compute them, and nobody should be trusted to type them in.

## Review rules for submissions

1. CI (`.github/workflows/submissions.yml`, no keys) runs `clipbench submission-check --all`: only the
   three files, the adapter hash matches, the rows are numbers and exception names only, every clip is in
   the current canary set, the summary matches the rows, and the adapter passes the contract offline.
2. A maintainer reads the adapter code before running it anywhere with keys. Adapters that send audio
   anywhere other than the provider's documented API, read files other than the audio, or phone home
   are rejected.
3. A maintainer reruns the canary set with the adapter (their keys, within the monthly canary budget)
   and scores it on the verified references. The published numbers are the rerun's, never the bundle's;
   the bundle's latency and failure numbers are shown only as "reported by the submitter" if at all.
4. Big gaps between the submitter's run and the rerun (failure rate, hypothesis hashes that never match
   on a deterministic API) are discussed in the PR before merging.
5. The provider joins the next full benchmark run; until its language is published it shows progress only.

## Code changes

- One feature per PR, with tests. CI runs `ruff check .` and `pytest -q` with no API keys, so anything
  needing keys must be mocked or skipped.
- No em or en dashes in code, docs or copy.
- Paid calls never run in tests or CI. A real run needs the maintainer's go and goes through the budget
  guard (`budget_usd` in clipbench.toml, and the monthly canary cap for canaries).
