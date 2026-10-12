"""Load the model catalog into node specs.

Two copies can exist:
  * bundled  — higgsfield_nodes/catalog/models.json, shipped with each release (never modified at runtime,
               so git-based installs stay clean and ComfyUI Manager updates never conflict)
  * user     — ComfyUI/user/higgsfield/catalog/models.json, written by "Check for updates" / the daily check
The newer one (by synced_at) is used, so a pack update with a fresher bundled catalog wins automatically.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

BUNDLED_CATALOG = Path(__file__).with_name("catalog") / "models.json"
CATALOG_PATH = BUNDLED_CATALOG  # backwards-compatible name
_active_cache: tuple[tuple, Path, dict] | None = None


def user_catalog_path() -> Path:
    from .credentials import user_directory

    return user_directory() / "higgsfield" / "catalog" / "models.json"


def _load_valid(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, dict) and isinstance(data.get("families"), list) and isinstance(data.get("endpoints"), dict) \
            and data["endpoints"]:
        return data
    return None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def active_catalog() -> tuple[Path, dict]:
    """(path, catalog) of the newest valid copy: user download, its backup, or the bundled release."""
    global _active_cache
    user = user_catalog_path()
    candidates = [user, user.with_name(user.name + ".bak"), BUNDLED_CATALOG]
    key = tuple((str(p), _mtime(p)) for p in candidates)
    if _active_cache is not None and _active_cache[0] == key:
        return _active_cache[1], _active_cache[2]
    best: tuple[str, Path, dict] | None = None
    for path in candidates:
        data = _load_valid(path) if _mtime(path) else None
        if data is not None and (best is None or str(data.get("synced_at") or "") > best[0]):
            best = (str(data.get("synced_at") or ""), path, data)
    if best is None:
        raise RuntimeError("The Higgsfield model catalog could not be read. Reinstall the pack, or run "
                           "Settings → Higgsfield → Model catalog → Check for updates.")
    _active_cache = (key, best[1], best[2])
    return best[1], best[2]


@dataclass(frozen=True)
class ModeSpec:
    key: str                 # option label shown in the node's "mode" dropdown
    endpoint: str            # e.g. "bytedance/seedance-2.0/text-to-video"
    schema: dict
    ui_schema: dict
    description: str
    pricing: str
    docs_url: str


@dataclass(frozen=True)
class FamilySpec:
    slug: str
    title: str
    company: str
    description: str
    output: str              # "video" | "image"
    modes: tuple[ModeSpec, ...]
    list_price: str = ""
    released_on: str = ""

    @property
    def node_id(self) -> str:
        return "Higgsfield_" + re.sub(r"[^0-9A-Za-z]+", "_", self.slug).strip("_")

    @property
    def display_name(self) -> str:
        return f"Higgsfield {self.title}"

    @property
    def category(self) -> str:
        return f"Higgsfield/{'Video' if self.output == 'video' else 'Image'}"


# Endpoints that are a third-party model under a Higgsfield product name, e.g.
# marketing-studio/image/flare: "Generate and edit Marketing Studio images with GPT Image 2.5 Flare."
UNDERLYING_MODEL = re.compile(r"\bwith (GPT Image \d+(?:\.\d+)?) ([A-Z][\w-]*)")


def _underlying(endpoint: dict) -> tuple[str, str] | None:
    match = UNDERLYING_MODEL.search(endpoint.get("description") or "")
    return (match.group(1), match.group(2)) if match else None


def _mode_key(endpoint: dict, used: set[str]) -> str:
    key = (endpoint.get("variant_title") or endpoint.get("operation") or endpoint["slug"]).strip()
    underlying = _underlying(endpoint)
    if underlying and underlying[0] not in key:
        key = f"{underlying[0]} {underlying[1]}"  # "2.5 Flare" -> "GPT Image 2.5 Flare"
    if key in used:  # keep option labels unique (stable: falls back to the endpoint slug)
        key = f"{key} [{endpoint['slug']}]"
    used.add(key)
    return key


def _output_kind(family: dict, endpoints: list[dict]) -> str:
    fields = {name for ep in endpoints for name in ep.get("output_fields") or []}
    if "images" in fields and "video" not in fields:
        return "image"
    if "video" in fields:
        return "video"
    return "image" if "image" in (family.get("output_types") or []) else "video"


def _price(family: dict) -> str:
    primary = ((family.get("pricing") or {}).get("primary") or {})
    amount, unit = primary.get("amount"), primary.get("unit")
    return f"from ${amount}/{unit}" if amount and unit else ""


def read_catalog_file(path: Path | None = None) -> dict:
    """Read a specific catalog file (falling back to its .bak), or the active catalog when path is None."""
    if path is None:
        return active_catalog()[1]
    errors = []
    for candidate in (path, path.with_name(path.name + ".bak")):
        try:
            data = json.loads(candidate.read_text(encoding="utf-8"))
            if isinstance(data, dict) and isinstance(data.get("families"), list) and isinstance(data.get("endpoints"), dict):
                if candidate != path:
                    logging.getLogger("higgsfield").warning(
                        "Higgsfield: %s is unreadable (%s); using the backup %s", path.name, errors[-1], candidate.name)
                return data
            errors.append("unexpected structure")
        except (OSError, ValueError) as error:
            errors.append(str(error))
    raise RuntimeError(f"The Higgsfield model catalog could not be read ({'; '.join(errors)}). "
                       "Run Settings → Higgsfield → Model catalog → Check for updates, or reinstall the pack.")


@lru_cache(maxsize=1)
def load_families(path: Path | None = None) -> tuple[FamilySpec, ...]:
    catalog = read_catalog_file(path)
    endpoints: dict[str, dict] = catalog["endpoints"]
    families = []
    for family in catalog["families"]:
        slugs = family.get("modes") or [family.get("default_mode_id")]
        members = [endpoints[slug] for slug in slugs if slug in endpoints]
        if not members:
            continue
        # Order modes: the family's default first, then the order Higgsfield lists them.
        default = family.get("default_mode_id")
        members.sort(key=lambda ep: (ep["slug"] != default,))
        used: set[str] = set()
        modes = tuple(
            ModeSpec(
                key=_mode_key(ep, used),
                endpoint=ep["slug"],
                schema=ep["input_schema"],
                ui_schema=ep.get("ui_schema") or {},
                description=ep.get("description") or "",
                pricing=ep.get("pricing_description") or "",
                docs_url=ep.get("docs_url") or "",
            )
            for ep in members
        )
        families.append(FamilySpec(
            slug=family["slug"],
            title=family.get("title") or family["slug"],
            company=family.get("company") or "",
            description=family.get("description") or "",
            output=_output_kind(family, members),
            modes=modes,
            list_price=_price(family),
            released_on=family.get("released_on") or "",
        ))
    families.extend(_underlying_families(endpoints))
    return tuple(families)


def _underlying_families(endpoints: dict[str, dict]) -> list[FamilySpec]:
    """Extra nodes named after the real model (e.g. "GPT Image 2.5"), reusing the same endpoints."""
    groups: dict[str, list[tuple[str, dict]]] = {}
    for endpoint in endpoints.values():
        found = _underlying(endpoint)
        if found:
            groups.setdefault(found[0], []).append((found[1], endpoint))
    result = []
    for model, members in sorted(groups.items()):
        members.sort(key=lambda m: m[0])
        result.append(FamilySpec(
            slug=re.sub(r"[^0-9a-z]+", "-", model.lower()).strip("-"),
            title=model,
            company="OpenAI" if model.startswith("GPT") else "",
            description=f"{model} through Higgsfield ({', '.join(e['slug'] for _, e in members)}).",
            output="image" if any("images" in (e.get("output_fields") or []) for _, e in members) else "video",
            modes=tuple(ModeSpec(
                key=f"{model} {variant}",
                endpoint=e["slug"],
                schema=e["input_schema"],
                ui_schema=e.get("ui_schema") or {},
                description=e.get("description") or "",
                pricing=e.get("pricing_description") or "",
                docs_url=e.get("docs_url") or "",
            ) for variant, e in members),
        ))
    return result


