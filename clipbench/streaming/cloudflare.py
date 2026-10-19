"""Cloudflare Workers AI hosted Deepgram Nova-3 over WebSocket.

Checked 2026-10-19 (https://developers.cloudflare.com/workers-ai/models/nova-3/): the model page lists
"Real-time: Yes", websocket-only options (interim_results, vad_events, utterance_end_ms) and a separate
WebSocket price (836.36 neurons, 0.0092 USD per audio minute, against 0.0052 over HTTP).

UNVERIFIED: Cloudflare's docs show WebSockets through a Worker binding (`env.AI.run(model, {..., websocket:
true})`) and do not document the REST WebSocket URL. This adapter uses
`wss://api.cloudflare.com/client/v4/accounts/<id>/ai/run/@cf/deepgram/nova-3` with a Bearer token and
Deepgram query parameters, and assumes Deepgram shaped messages. Check it on the first live run.

`@cf/deepgram/flux` is not adapted: it is English only (Deepgram lists flux-general-en for Workers AI),
so it cannot cover the five benchmark languages.
"""

from __future__ import annotations

import os
from typing import ClassVar
from urllib.parse import urlencode

from clipbench.streaming.deepgram import DeepgramShaped


class CfNova3Stream(DeepgramShaped):
    id: ClassVar[str] = "cf-nova-3-stream"
    env_keys: ClassVar[tuple[str, ...]] = ("CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_API_TOKEN")
    concurrency: ClassVar[int] = 4
    base_url: ClassVar[str] = "wss://api.cloudflare.com/client/v4/accounts"

    def query(self, language: str) -> dict[str, str]:
        q = super().query(language)
        q.pop("model")  # the model is in the path
        return q

    def url(self, language: str) -> str:
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        return f"{self.base_url}/{account}/ai/run/{self.model_id}?{urlencode(self.query(language))}"

    def headers(self) -> dict[str, str]:
        return {"authorization": f"Bearer {os.environ.get('CLOUDFLARE_API_TOKEN', '')}"}
