"""Verification metadata from pricing.toml and the fail-closed gate for real (non dry) runs.

Every section of pricing.toml carries `live_verified`, `price_status`, `policy_status` and `unverified`
(the open questions to settle on the first live run). A real run refuses a paid provider unless its price is
in pricing.toml with `price_status = "documented"` and its data use / training terms are established
(`policy_status = "documented"`), or the operator names it in `--allow-unverified`, which run_meta records.
A dry run still prices refused providers and marks them "would be refused: <reason>".

Exempt: providers with `paid = False` (whisper-local, youtube-auto, wayin, fake, fake-stream), the fake test
doubles (`verification_exempt = True`, so their paid subclasses in the budget tests stay exempt), and Provider
instances whose id is neither registered nor in pricing.toml (test doubles, the synthetic fixture generator).
Those carry no pricing.toml metadata to check; a paid one still needs a price above zero.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from clipbench.providers.base import load_pricing

PRICE_STATUSES = frozenset({"documented", "unverified", "not_applicable"})
POLICY_STATUSES = frozenset({"documented", "unverified", "unknown", "not_applicable"})
OVERRIDE_FLAG = "--allow-unverified"


@dataclass(frozen=True)
class Verification:
    provider: str
    live_verified: bool
    price_status: str | None
    policy_status: str | None
    unverified: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "live_verified": self.live_verified,
            "price_status": self.price_status,
            "policy_status": self.policy_status,
            "unverified": list(self.unverified),
        }


def verification(provider_id: str) -> Verification:
    """The verification fields of `provider_id` in pricing.toml. Missing fields come back as None / empty."""
    entry = load_pricing().get(provider_id) or {}
    return Verification(
        provider=provider_id,
        live_verified=bool(entry.get("live_verified", False)),
        price_status=entry.get("price_status"),
        policy_status=entry.get("policy_status"),
        unverified=tuple(str(q) for q in entry.get("unverified") or []),
    )


def _registered(provider_id: str) -> bool:
    from clipbench.providers.registry import REGISTRY
    from clipbench.streaming.registry import STREAM_REGISTRY

    return provider_id in REGISTRY or provider_id in STREAM_REGISTRY or provider_id in load_pricing()


def is_gated(provider: Any) -> bool:
    """True for a paid provider whose price and terms come from pricing.toml (every registered paid adapter)."""
    if not bool(getattr(provider, "paid", True)) or getattr(provider, "verification_exempt", False):
        return False
    return _registered(str(provider.id))


def refusal_reason(provider: Any) -> str | None:
    """Why a real run must refuse this provider, or None when it may run."""
    if not is_gated(provider):
        return None
    pid = str(provider.id)
    entry = load_pricing().get(pid)
    v = verification(pid)
    reasons: list[str] = []
    try:
        price = float(provider.price_per_minute())
    except Exception:  # noqa: BLE001 - a broken price is a missing price
        price = 0.0
    if not entry or "usd_per_minute" not in entry or price <= 0:
        reasons.append(f"no price for {getattr(provider, 'model_id', '?')} in pricing.toml")
    if v.price_status != "documented":
        reasons.append(f"price_status is {v.price_status or 'missing'}")
    if v.policy_status != "documented":
        reasons.append(f"policy_status is {v.policy_status or 'missing'} (data use / training not established)")
    return "; ".join(reasons) or None


def parse_allow(spec: str | Iterable[str] | None) -> frozenset[str]:
    """'groq-whisper,aws-transcribe' (or a list of ids) -> frozenset of ids. Empty for None."""
    if spec is None:
        return frozenset()
    items = spec.split(",") if isinstance(spec, str) else list(spec)
    return frozenset(str(s).strip() for s in items if str(s).strip())


def check(providers: Iterable[Any], allow: Iterable[str] = ()) -> tuple[dict[str, str], dict[str, str]]:
    """(refused, overridden): {provider id: reason}. `overridden` holds the ones `allow` lets through."""
    allowed = set(allow)
    refused: dict[str, str] = {}
    overridden: dict[str, str] = {}
    for p in providers:
        reason = refusal_reason(p)
        if reason is None:
            continue
        (overridden if p.id in allowed else refused)[p.id] = reason
    return refused, overridden


def refused_message(provider_id: str, reason: str) -> str:
    return f"{provider_id}: refused, {reason}; pass {OVERRIDE_FLAG} {provider_id} to run it anyway"
