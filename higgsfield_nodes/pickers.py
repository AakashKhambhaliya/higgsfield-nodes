"""ID pickers: fill style_id / preset_id / custom_reference_id from Higgsfield's list endpoints.

Docs: soul-2 + soul-standard ("List SOUL styles"), marketing-studio-image ("List Marketing Studio presets"),
genjutsu/restyle ("Available styles"), soul-id (custom references).
"""

from __future__ import annotations

from typing import Any

from .catalog import load_families
from .client import HiggsfieldClient

PICKERS: dict[str, dict[str, Any]] = {
    "soul-styles-v2": {"path": "/v1/text2image/soul-styles/v2", "label": "Soul 2 style"},
    "soul-styles": {"path": "/v1/text2image/soul-styles", "label": "Soul style"},
    "marketing-presets": {"path": "/marketing-studio/image/presets", "label": "Marketing Studio preset", "paged": True},
    "restyle-presets": {"path": "/models/higgsfield/genjutsu/restyle/v1.0/presets", "label": "Restyle style"},
    "soul-ids": {"path": None, "label": "Soul ID"},
}
MAX_ITEMS = 1000


def picker_for(endpoint: str, field: str) -> str | None:
    if field == "style_id" and endpoint.startswith("higgsfield-ai/soul/v2"):
        return "soul-styles-v2"
    if field == "style_id" and endpoint.startswith("higgsfield-ai/soul"):
        return "soul-styles"
    if field == "preset_id" and endpoint.startswith("marketing-studio/"):
        return "marketing-presets"
    if field == "preset_id" and "genjutsu/restyle" in endpoint:
        return "restyle-presets"
    if field == "custom_reference_id":
        return "soul-ids"
    return None


def picker_map() -> dict:
    """{node_id: {"multi": bool, "modes": {mode_key: {widget_name: {"kind", "label"}}}}} for the frontend."""
    from .nodes import MODE_INPUT, NODE_INPUT_IDS, RENAMED_INPUTS

    result = {}
    for family in load_families():
        multi = len(family.modes) > 1
        modes = {}
        for mode in family.modes:
            fields = {}
            for name in (mode.schema.get("properties") or {}):
                kind = picker_for(mode.endpoint, name)
                if not kind:
                    continue
                reserved = {MODE_INPUT} if multi else NODE_INPUT_IDS
                input_id = RENAMED_INPUTS.get(name, f"api_{name}") if name in reserved else name
                widget = f"{MODE_INPUT}.{input_id}" if multi else input_id
                fields[widget] = {"kind": kind, "label": PICKERS[kind]["label"]}
            if fields:
                modes[mode.key] = fields
        if modes:
            result[family.node_id] = {"multi": multi, "modes": modes}
    return result


def _normalize(item: dict) -> dict | None:
    if not isinstance(item, dict) or not item.get("id"):
        return None
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    return {
        "id": str(item["id"]),
        "name": str(item.get("name") or item["id"]),
        "group": metadata.get("group_name") or item.get("model_version") or item.get("type") or "",
        "description": item.get("description") or (item.get("status") if item.get("status") else ""),
        "preview": item.get("preview_url") or item.get("cover_image") or item.get("thumbnail_url") or "",
    }


def _items(payload: Any) -> list:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("items", "results", "data", "styles", "presets"):
            if isinstance(payload.get(key), list):
                return payload[key]
    return []


async def fetch_options(client: HiggsfieldClient, kind: str, search: str = "") -> list[dict]:
    spec = PICKERS.get(kind)
    if spec is None:
        raise ValueError(f"Unknown picker '{kind}'.")
    if kind == "soul-ids":
        raw = [r for r in await client.list_custom_references() if r.get("status") == "completed"]
    elif spec.get("paged"):
        raw, cursor = [], 0
        while cursor is not None and len(raw) < MAX_ITEMS:
            query = f"?size=100&cursor={int(cursor)}" + (f"&search={_quote(search)}" if search.strip() else "")
            payload = await client.get_json(spec["path"] + query, PICKERS[kind]["label"] + " list")
            page = _items(payload)
            raw.extend(page)
            next_cursor = payload.get("cursor") if isinstance(payload, dict) else None
            # Stop on an empty page or a cursor that does not advance (avoids an endless loop).
            cursor = next_cursor if page and isinstance(next_cursor, int) and next_cursor > cursor else None
    else:
        raw = _items(await client.get_json(spec["path"], PICKERS[kind]["label"] + " list"))
    items = [n for n in (_normalize(i) for i in raw[:MAX_ITEMS]) if n]
    if search.strip() and not spec.get("paged"):
        needle = search.strip().lower()
        items = [i for i in items if needle in i["name"].lower() or needle in i["group"].lower()]
    return items


def _quote(text: str) -> str:
    from urllib.parse import quote

    return quote(text.strip()[:100])
