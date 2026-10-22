"""Workers AI judge client: pinned prompt, temperature 0, JSON output validated with retries, cached, budget guarded.

Request: POST https://api.cloudflare.com/client/v4/accounts/{ACCOUNT_ID}/ai/run/{model} with the chat
`messages`. Responses come in the Cloudflare envelope; text models answer either `result.response`
(string or object) or OpenAI style `result.choices[0].message.content`. Billing is in neurons; the
per token USD prices below are from the Workers AI pricing page.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import httpx

from clipbench.budget import BudgetExceeded, BudgetGuard
from clipbench.models import Result
from clipbench.providers.cache import ResultCache

PROMPTS_DIR = Path(__file__).parent / "prompts"
DEFAULT_PROMPT = "semantic_v1"
DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"
PRICING_URL = "https://developers.cloudflare.com/workers-ai/platform/pricing/"
PRICING_CHECKED_ON = "2026-10-19"
USD_PER_1K_NEURONS = 0.011

# USD per million tokens (input, output), from PRICING_URL on PRICING_CHECKED_ON.
# json_mode: the model is listed on the Workers AI JSON mode page, so `response_format` is sent.
JUDGE_MODELS: dict[str, dict[str, Any]] = {
    "@cf/google/gemma-4-26b-a4b-it": {"usd_in": 0.100, "usd_out": 0.300, "json_mode": False},
    "@cf/meta/llama-4-scout-17b-16e-instruct": {"usd_in": 0.270, "usd_out": 0.850, "json_mode": False},
    "@cf/meta/llama-3.3-70b-instruct-fp8-fast": {"usd_in": 0.293, "usd_out": 2.253, "json_mode": True},
    "@cf/openai/gpt-oss-120b": {"usd_in": 0.350, "usd_out": 0.750, "json_mode": False},
}
MAX_OUTPUT_TOKENS = 200
PROMPT_OVERHEAD_TOKENS = 450  # the pinned instructions, counted generously
ATTEMPTS = 3
JUDGE_PROVIDER_ID = "judge-semantic"
LANGUAGE_NAMES = {"en": "English", "ja": "Japanese", "ko": "Korean", "es": "Spanish", "hi": "Hindi (Devanagari or romanised)", "fr": "French"}

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {"score": {"type": "number"}, "reason": {"type": "string"}},
    "required": ["score", "reason"],
}


class JudgeError(Exception):
    """The judge did not return a valid answer after every attempt."""


@dataclass(frozen=True)
class Prompt:
    version: str  # file stem, e.g. semantic_v1
    system: str
    user: str

    @property
    def hash(self) -> str:
        return hashlib.sha256((self.system + "\n" + self.user).encode()).hexdigest()[:12]

    @property
    def cache_tag(self) -> str:
        return f"{self.version}-{self.hash}"

    def render(self, language: str, reference: str, hypothesis: str) -> list[dict[str, str]]:
        name = LANGUAGE_NAMES.get(language.split("-")[0], language)
        user = self.user.replace("{language}", name).replace("{reference}", reference or "(empty)")
        user = user.replace("{hypothesis}", hypothesis or "(empty)")
        return [{"role": "system", "content": self.system}, {"role": "user", "content": user}]


@lru_cache(maxsize=8)
def load_prompt(version: str = DEFAULT_PROMPT) -> Prompt:
    text = (PROMPTS_DIR / f"{version}.txt").read_text()
    system, _, user = text.partition("---USER---")
    return Prompt(version, system.strip(), user.strip())


def text_hash(text: str) -> str:
    return hashlib.sha256((text or "").encode()).hexdigest()[:16]


def pair_id(ref: str, hyp: str) -> str:
    return f"{text_hash(ref)}{text_hash(hyp)}"


def estimate_tokens(text: str) -> int:
    """Upper bound: one token per 3 UTF-8 bytes (a CJK character is 3 bytes and about one token)."""
    return -(-len((text or "").encode()) // 3)


def model_price(model: str) -> dict[str, Any]:
    if model not in JUDGE_MODELS:
        raise ValueError(f"unknown judge model {model!r}; add its price to JUDGE_MODELS ({PRICING_URL})")
    return JUDGE_MODELS[model]


def cost_usd(model: str, tokens_in: float, tokens_out: float) -> float:
    p = model_price(model)
    return (tokens_in * p["usd_in"] + tokens_out * p["usd_out"]) / 1e6


def estimate_pair_usd(model: str, ref: str, hyp: str) -> float:
    tokens_in = PROMPT_OVERHEAD_TOKENS + estimate_tokens(ref) + estimate_tokens(hyp)
    return cost_usd(model, tokens_in, MAX_OUTPUT_TOKENS)


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _content(result: Any) -> Any:
    if not isinstance(result, dict):
        return result
    if "response" in result:
        return result["response"]
    choices = result.get("choices")
    if isinstance(choices, list) and choices:
        msg = choices[0].get("message") or {}
        return msg.get("content") if isinstance(msg, dict) else None
    return None


def parse_answer(result: Any) -> tuple[float, str]:
    """(score, reason) from a model result, or ValueError when it is not a valid answer."""
    content = _content(result)
    if isinstance(content, str):
        text = _THINK_RE.sub("", content).strip()
        text = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
        m = _OBJ_RE.search(text)
        if not m:
            raise ValueError("no JSON object in the answer")
        obj = json.loads(m.group(0))
    elif isinstance(content, dict):
        obj = content
    else:
        raise ValueError("empty answer")
    score = obj.get("score")
    if isinstance(score, bool) or not isinstance(score, int | float):
        raise ValueError(f"score is not a number: {score!r}")
    if not 0 <= float(score) <= 1:
        raise ValueError(f"score out of range: {score!r}")
    reason = obj.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("missing reason")
    reason = " ".join(reason.split())[:200]
    return float(score), reason


def _usage(body: dict[str, Any]) -> tuple[float, float] | None:
    result = body.get("result") if isinstance(body.get("result"), dict) else {}
    for usage in (result.get("usage"), body.get("usage")):
        if isinstance(usage, dict) and isinstance(usage.get("prompt_tokens"), int | float):
            return float(usage["prompt_tokens"]), float(usage.get("completion_tokens") or 0)
    return None


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


@dataclass
class JudgeScore:
    score: float
    reason: str
    cost_usd: float
    cached: bool
    attempts: int


def credentials_present() -> bool:
    return bool(os.environ.get("CLOUDFLARE_ACCOUNT_ID") and os.environ.get("CLOUDFLARE_API_TOKEN"))


class SemanticJudge:
    """One judge model with one pinned prompt. `transport` lets tests use httpx.MockTransport."""

    base_url = "https://api.cloudflare.com/client/v4"

    def __init__(
        self,
        cache: ResultCache,
        guard: BudgetGuard | None,
        *,
        model: str = DEFAULT_MODEL,
        prompt: str = DEFAULT_PROMPT,
        transport: httpx.AsyncBaseTransport | None = None,
        concurrency: int = 4,
        retry_sleep_s: float = 1.0,
    ) -> None:
        model_price(model)
        self.model = model
        self.prompt = load_prompt(prompt)
        self.cache = cache
        self.guard = guard
        self.transport = transport
        self.sem = asyncio.Semaphore(concurrency)
        self.retry_sleep_s = retry_sleep_s
        self._client: httpx.AsyncClient | None = None

    @property
    def settings_hash(self) -> str:
        return self.prompt.cache_tag

    def cached(self, ref: str, hyp: str) -> JudgeScore | None:
        hit = self.cache.get(JUDGE_PROVIDER_ID, self.model, pair_id(ref, hyp), self.settings_hash)
        if hit is None:
            return None
        try:
            obj = json.loads(hit.text)
            return JudgeScore(float(obj["score"]), str(obj["reason"]), 0.0, True, 0)
        except (ValueError, KeyError, TypeError):
            return None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={"authorization": f"Bearer {token}"},
                timeout=60.0,
                transport=self.transport,
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def request_body(self, language: str, ref: str, hyp: str) -> dict[str, Any]:
        body: dict[str, Any] = {
            "messages": self.prompt.render(language, ref, hyp),
            "temperature": 0,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "seed": 7,
        }
        if JUDGE_MODELS[self.model]["json_mode"]:
            body["response_format"] = {"type": "json_schema", "json_schema": RESPONSE_SCHEMA}
        return body

    async def score(self, language: str, ref: str, hyp: str, *, label: str = "") -> JudgeScore:
        hit = self.cached(ref, hyp)
        if hit is not None:
            return hit
        account = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
        path = f"/accounts/{account}/ai/run/{self.model}"
        body = self.request_body(language, ref, hyp)
        est = estimate_pair_usd(self.model, ref, hyp)
        spent = 0.0
        last_error = "no attempt"
        async with self.sem:
            for attempt in range(1, ATTEMPTS + 1):
                if self.guard is not None and not await self.guard.reserve(est):
                    raise BudgetExceeded(f"judge: budget cap reached before pair {label or pair_id(ref, hyp)}")
                started = time.perf_counter()
                usd = est
                try:
                    resp = await self._http().post(path, json=body)
                    status = resp.status_code
                    data = resp.json() if resp.content else {}
                    usage = _usage(data) if isinstance(data, dict) else None
                    if usage is not None:
                        usd = cost_usd(self.model, *usage)
                    if status == 429 or status >= 500:
                        raise ValueError(f"HTTP {status}")
                    if status >= 400:
                        usd = 0.0  # rejected requests are not billed
                        raise JudgeError(f"HTTP {status}: {str(data)[:200]}")
                    if not data.get("success", True) or "result" not in data:
                        raise ValueError(f"request failed: {str(data.get('errors'))[:200]}")
                    score, reason = parse_answer(data["result"])
                except (httpx.HTTPError, ValueError) as e:
                    last_error = str(e)
                    if self.guard is not None:
                        await self.guard.record(JUDGE_PROVIDER_ID, label, usd, est)
                    spent += usd
                    if attempt < ATTEMPTS:
                        await asyncio.sleep(self.retry_sleep_s * attempt)
                    continue
                except JudgeError:
                    if self.guard is not None:
                        await self.guard.release(est)
                    raise
                if self.guard is not None:
                    await self.guard.record(JUDGE_PROVIDER_ID, label, usd, est)
                spent += usd
                latency = (time.perf_counter() - started) * 1000
                self.cache.put(
                    JUDGE_PROVIDER_ID,
                    self.model,
                    pair_id(ref, hyp),
                    self.settings_hash,
                    Result(
                        text=json.dumps({"score": score, "reason": reason}, ensure_ascii=False),
                        latency_ms=latency,
                        audio_seconds=0.0,
                        cost_usd=spent,
                        model_id=self.model,
                        provider_version=self.prompt.cache_tag,
                    ),
                )
                return JudgeScore(score, reason, spent, False, attempt)
        raise JudgeError(f"judge gave no valid answer after {ATTEMPTS} attempts: {last_error}")
