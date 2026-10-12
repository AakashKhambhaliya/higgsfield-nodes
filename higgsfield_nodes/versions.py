"""Is a newer release of this pack published on the Comfy Registry? (shown in Settings → Higgsfield)

Updates themselves are installed by ComfyUI Manager / comfy-cli; this only tells the user one exists.
"""

from __future__ import annotations

import logging
import re
import time

import httpx

from . import REGISTRY_ID, __version__

log = logging.getLogger("higgsfield")
REGISTRY_API = "https://api.comfy.org/nodes/"
REGISTRY_PAGE = "https://registry.comfy.org/nodes/"
CACHE_SECONDS = 6 * 60 * 60
_cache: tuple[float, dict] | None = None


def parse_version(text: str) -> tuple[int, ...]:
    """'1.2.3' -> (1, 2, 3); pre-release/build suffixes are ignored."""
    numbers = re.findall(r"\d+", (text or "").split("-")[0].split("+")[0])
    return tuple(int(n) for n in numbers[:3]) + (0,) * (3 - len(numbers[:3]))


def compare(installed: str, latest: str | None) -> bool:
    return bool(latest) and parse_version(latest) > parse_version(installed)


async def fetch_latest(transport: httpx.AsyncBaseTransport | None = None) -> dict:
    async with httpx.AsyncClient(timeout=10.0, transport=transport, follow_redirects=True,
                                 headers={"User-Agent": f"higgsfield-nodes/{__version__}"}) as client:
        response = await client.get(REGISTRY_API + REGISTRY_ID)
    if response.status_code == 404:
        return {"published": False, "latest": None}
    response.raise_for_status()
    payload = response.json()
    latest = (payload.get("latest_version") or {}).get("version") if isinstance(payload, dict) else None
    return {"published": True, "latest": latest}


async def version_status(transport: httpx.AsyncBaseTransport | None = None, now: float | None = None) -> dict:
    global _cache
    now = time.time() if now is None else now
    base = {"installed": __version__, "node_id": REGISTRY_ID, "page": REGISTRY_PAGE + REGISTRY_ID}
    if _cache is None or now - _cache[0] > CACHE_SECONDS:
        try:
            _cache = (now, await fetch_latest(transport))
        except Exception as error:  # offline, registry down, … — never an error for the user
            log.debug("Higgsfield: registry version check failed: %s", error)
            return base | {"latest": None, "published": None, "update_available": False,
                           "error": "Couldn't reach the Comfy Registry to check for updates."}
    info = _cache[1]
    return base | info | {"update_available": compare(__version__, info.get("latest"))}
