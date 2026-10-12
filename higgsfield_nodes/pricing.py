"""Price badge data for nodes in the editor (before anything runs).

1. Live: with an API key, rebuild the exact request the node would send and ask Higgsfield's free
   /estimate endpoint — this includes your account's discounts. Cached for 10 minutes per request.
2. Published: otherwise (no key, or the price depends on media that isn't uploaded yet) show the range
   from open.higgsfield.ai/pricing (synced into the catalog), scaled by duration when that's exact.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from . import credentials
from .catalog import active_catalog
from .client import HiggsfieldClient, HiggsfieldError

log = logging.getLogger("higgsfield")
CACHE_SECONDS = 600
_cache: dict[str, tuple[float, dict]] = {}
PLACEHOLDER = {"image": "https://cdn.higgsfield.ai/placeholder.png",
               "video": "https://cdn.higgsfield.ai/placeholder.mp4",
               "audio": "https://cdn.higgsfield.ai/placeholder.wav"}


class _Linked:
    """Stands in for a connected socket whose media isn't available in the editor."""


def _catalog() -> dict:
    """Prices from the active catalog (re-read when it changes, so catalog updates apply without restart)."""
    try:
        return active_catalog()[1]
    except RuntimeError:
        return {}


def _money(value: float) -> str:
    if value >= 10:
        return f"${value:,.0f}"
    if value >= 1:
        return f"${value:.2f}"
    return f"${value:.3f}".rstrip("0").rstrip(".") if value >= 0.01 else f"${value:.4f}".rstrip("0").rstrip(".")


def published(endpoint: str, duration: float | None = None, batch: int | None = None) -> dict | None:
    catalog = _catalog()
    price = (catalog.get("endpoints", {}).get(endpoint) or {}).get("price")
    if not price:
        family = next((f for f in catalog.get("families", []) if endpoint in (f.get("modes") or [])), None)
        primary = ((family or {}).get("pricing") or {}).get("primary") or {}
        if not primary.get("amount"):
            return None
        amount = float(primary["amount"])
        price = {"unit": primary.get("unit"), "min": amount, "max": amount, "exact": False}
    unit = "s" if price.get("unit") == "second" else "image" if price.get("unit") == "image" else price.get("unit") or ""
    low, high = float(price["min"]), float(price["max"])
    multiplier = (duration if unit == "s" and duration else 1) * (batch or 1)
    if price.get("exact") and multiplier != 1:
        return {"kind": "published", "text": f"≈{_money(low * multiplier)}",
                "detail": f"Published price {_money(low)}/{unit} × {multiplier:g}. Exact price: run 'estimate only'."}
    rate = _money(low) if low == high else f"{_money(low)}–{_money(high)}"
    prefix = "" if price.get("exact") else ("from " if low == high else "")
    return {"kind": "published", "text": f"{prefix}{rate}/{unit}",
            "detail": "Published price range for this mode (depends on settings). Set an API key for the exact price."}


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def price_for(node_id: str, values: dict, linked: list[str]) -> dict:
    from .nodes import NODE_SPECS, _build_arguments

    spec = NODE_SPECS.get(node_id)
    if spec is None:
        return {"kind": "none", "text": ""}
    # The frontend sends one flat dict: node-level inputs plus the selected mode's inputs (prefix stripped).
    nested = values
    key = values.get("mode") if spec.multi else spec.family.modes[0].key
    mode = spec.modes_by_key.get(key)
    if mode is None:
        return {"kind": "none", "text": ""}
    params = spec.params_by_mode[mode.key]
    linked_ids = {name.split(".")[0] for name in linked}
    media_values = {}
    for param in params:
        if param.kind in ("image", "video", "audio") and param.input_id in linked_ids:
            media_values[param.input_id] = _Linked()
        elif param.kind == "media_list" and param.input_id in linked_ids:
            count = sum(1 for name in linked if name.split(".")[0] == param.input_id)
            media_values[param.input_id] = {f"{param.media}_{i}": _Linked() for i in range(1, max(count, 1) + 1)}
    duration = _number(nested.get("duration"))
    batch = _number(nested.get("batch_size") or nested.get("num_images"))
    fallback = published(mode.endpoint, duration, int(batch) if batch else None) or {"kind": "none", "text": ""}

    creds = credentials.resolve() if _safe_resolve() else None
    if creds is None:
        return fallback
    # Prices don't depend on prompt text: use a stand-in while the prompt box is still empty.
    top = dict(values)
    if "prompt" in (mode.schema.get("properties") or {}) and not str(top.get("prompt") or "").strip():
        top["prompt"] = "price check"
    try:
        arguments, uploads = _build_arguments(spec.family, mode, params, {**top, **media_values},
                                              top, spec.hoist_prompt, spec.seed_param)
    except HiggsfieldError as error:
        return fallback | {"detail": f"{fallback.get('detail', '')} Not ready: {error}".strip()}
    if any(p.kind in ("video", "audio") or p.media in ("video", "audio") for p, _ in uploads):
        return fallback | {"detail": "The price depends on the length of the connected video/audio; "
                                     "use run = 'estimate only' for the exact amount."}
    for param, items in uploads:
        url = PLACEHOLDER.get(param.media or param.kind, PLACEHOLDER["image"])
        arguments[param.name] = [url] * len(items) if param.kind == "media_list" else url

    cache_key = json.dumps([mode.endpoint, arguments], sort_keys=True, default=str)
    hit = _cache.get(cache_key)
    if hit and time.time() - hit[0] < CACHE_SECONDS:
        return hit[1]
    try:
        async with HiggsfieldClient(creds) as client:
            estimate = await client.estimate(mode.endpoint, arguments)
    except HiggsfieldError as error:
        log.debug("Higgsfield live price unavailable for %s: %s", mode.endpoint, error)
        return fallback
    if estimate.usd is None:
        return fallback
    result = {"kind": "live", "text": _money(float(estimate.usd)),
              "detail": f"Higgsfield estimate for these settings: {estimate.describe()} (your account's price)."}
    _cache[cache_key] = (time.time(), result)
    if len(_cache) > 500:
        for old in sorted(_cache, key=lambda k: _cache[k][0])[:250]:
            _cache.pop(old, None)
    return result


def _safe_resolve() -> bool:
    try:
        return credentials.resolve() is not None
    except credentials.CredentialError:
        return False
