"""Snapshot the public Higgsfield API model catalog into higgsfield_nodes/catalog/models.json.

Sources (public pages, no API key needed):
  * https://open.higgsfield.ai/explore/{video,image}  -> model families, default endpoint, list price
  * https://open.higgsfield.ai/models/<endpoint>/api-reference
        -> official input JSON schema, UI hints, sibling modes ("family"), pricing description

The pages are server-rendered with a serialized JS payload (seroval: `$R[n]=` references,
`!0`/`!1` booleans, bare object keys). JSParser below reads that literal subset.

Usage:  python tools/sync_catalog.py [--out PATH]   (also run from ComfyUI Settings → Higgsfield)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

HOST = "https://open.higgsfield.ai"
EXPLORE_PAGES = ("explore/video", "explore/image")
USER_AGENT = "comfyui-higgsfield-catalog-sync/1.0"
MAX_PAGE_BYTES = 8 * 1024 * 1024
MIN_ENDPOINTS = 40  # refuse to write a suspiciously small catalog
DEFAULT_OUT = Path(__file__).resolve().parent / "catalog" / "models.json"

SLUG_RE = re.compile(r"^[a-zA-Z0-9._~-]+(?:/[a-zA-Z0-9._~-]+)*$")


# --------------------------------------------------------------------------- fetching

def fetch(path: str, attempts: int = 3) -> str:
    url = f"{HOST}/{path.lstrip('/')}"
    if urlsplit(url).netloc != "open.higgsfield.ai":
        raise ValueError(f"refusing to fetch outside open.higgsfield.ai: {url}")
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=45) as response:
                data = response.read(MAX_PAGE_BYTES + 1)
            if len(data) > MAX_PAGE_BYTES:
                raise ValueError(f"page too large: {url}")
            return data.decode("utf-8")
        except Exception as error:  # network hiccups: retry with backoff
            last = error
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")


# --------------------------------------------------------------------------- JS literal parser

class Ref:
    """Unresolved `$R[n]` back-reference (defined outside the parsed span)."""

    def __init__(self, index: int) -> None:
        self.index = index

    def __repr__(self) -> str:
        return f"$R[{self.index}]"


_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", "b": "\b", "f": "\f", "v": "\v", "0": "\0",
            '"': '"', "'": "'", "\\": "\\", "/": "/", "`": "`", "$": "$"}


class JSParser:
    def __init__(self, text: str) -> None:
        self.s = text
        self.refs: dict[int, object] = {}

    def value_at(self, index: int):
        value, _ = self._value(index)
        return value

    def _ws(self, i: int) -> int:
        while i < len(self.s) and self.s[i] in " \t\r\n":
            i += 1
        return i

    def _value(self, i: int):
        i = self._ws(i)
        s = self.s
        match = re.compile(r"\$R\[(\d+)\]").match(s, i)
        if match:
            index = int(match.group(1))
            i = match.end()
            if s.startswith("=", i):
                value, i = self._value(i + 1)
                self.refs[index] = value
                return value, i
            return self.refs.get(index, Ref(index)), i
        ch = s[i]
        if ch == "{":
            return self._object(i)
        if ch == "[":
            return self._array(i)
        if ch in "\"'":
            return self._string(i)
        if s.startswith("!0", i):
            return True, i + 2
        if s.startswith("!1", i):
            return False, i + 2
        for word, value in (("true", True), ("false", False), ("null", None), ("void 0", None),
                            ("undefined", None)):
            if s.startswith(word, i):
                return value, i + len(word)
        match = re.compile(r"-?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?").match(s, i)
        if match:
            text = match.group(0)
            return (float(text) if any(c in text for c in ".eE") else int(text)), match.end()
        raise ValueError(f"unsupported JS literal at {i}: {s[i:i + 60]!r}")

    def _object(self, i: int):
        out: dict = {}
        i = self._ws(i + 1)
        while self.s[i] != "}":
            if self.s[i] in "\"'":
                key, i = self._string(i)
            else:
                match = re.compile(r"[A-Za-z_$][\w$]*|\d+").match(self.s, i)
                if not match:
                    raise ValueError(f"bad object key at {i}: {self.s[i:i + 40]!r}")
                key, i = match.group(0), match.end()
            i = self._ws(i)
            if self.s[i] != ":":
                raise ValueError(f"expected ':' at {i}")
            out[key], i = self._value(i + 1)
            i = self._ws(i)
            if self.s[i] == ",":
                i = self._ws(i + 1)
        return out, i + 1

    def _array(self, i: int):
        out: list = []
        i = self._ws(i + 1)
        while self.s[i] != "]":
            item, i = self._value(i)
            out.append(item)
            i = self._ws(i)
            if self.s[i] == ",":
                i = self._ws(i + 1)
        return out, i + 1

    def _string(self, i: int):
        quote = self.s[i]
        i += 1
        parts: list[str] = []
        while True:
            ch = self.s[i]
            if ch == quote:
                return "".join(parts), i + 1
            if ch == "\\":
                nxt = self.s[i + 1]
                if nxt == "u":
                    if self.s[i + 2] == "{":
                        end = self.s.index("}", i)
                        parts.append(chr(int(self.s[i + 3:end], 16)))
                        i = end + 1
                    else:
                        parts.append(chr(int(self.s[i + 2:i + 6], 16)))
                        i += 6
                    continue
                if nxt == "x":
                    parts.append(chr(int(self.s[i + 2:i + 4], 16)))
                    i += 4
                    continue
                parts.append(_ESCAPES.get(nxt, nxt))
                i += 2
                continue
            parts.append(ch)
            i += 1


def _after_key(text: str, key: str, start: int, end: int | None = None) -> int | None:
    """Index just after `key:` (bare JS key) within [start, end)."""
    match = re.compile(r"[{,]" + re.escape(key) + r":").search(text, start, end if end is not None else len(text))
    return match.end() if match else None


def _read_key(parser: JSParser, key: str, start: int, end: int | None = None):
    index = _after_key(parser.s, key, start, end)
    return None if index is None else parser.value_at(index)


def _clean(value):
    """Drop unresolved references so the snapshot is plain JSON."""
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if not isinstance(v, Ref)}
    if isinstance(value, list):
        return [_clean(v) for v in value if not isinstance(v, Ref)]
    return value


# --------------------------------------------------------------------------- explore pages

def parse_families(page: str) -> list[dict]:
    parser = JSParser(page)
    # Multi-mode families use `id:"family:<uuid>"`; single-endpoint models use `id:"model:<slug>"`.
    starts = [m.start() for m in re.finditer(r'id:"(?:family|model):', page)]
    families = []
    for n, start in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(page)
        default_mode = _read_key(parser, "default_mode_id", start, end)
        if not isinstance(default_mode, str) and page.startswith('id:"model:', start):
            default_mode = _read_key(parser, "slug", start, end)
        if not isinstance(default_mode, str):
            continue
        company = _read_key(parser, "company", start, end) or {}
        families.append(_clean({
            "slug": _read_key(parser, "slug", start, end),
            "title": _read_key(parser, "title", start, end),
            "company": company.get("name") if isinstance(company, dict) else None,
            "description": _read_key(parser, "description", start, end),
            "output_types": _read_key(parser, "output_types", start, end),
            "default_mode_id": default_mode,
            "entry_points": _read_key(parser, "entry_points", start, end),
            "pricing": _read_key(parser, "pricing", start, end),
            "released_on": _read_key(parser, "released_on", start, end),
            "tags": _read_key(parser, "tags", start, end),
        }))
    return families


# --------------------------------------------------------------------------- api-reference pages

def model_markdown(parser: JSParser) -> str:
    """The LLM-readable model document embedded as a JS string literal (longest match wins)."""
    best = ""
    for match in re.finditer(r'"(?=(?:\\.|[^"\\])*## API Information)', parser.s):
        try:
            text, _ = parser._string(match.start())
        except (ValueError, IndexError):
            continue
        if len(text) > len(best):
            best = text
    return best


def _json_block(markdown: str, heading: str):
    match = re.search(re.escape(heading) + r"\s+(?:[^`]*?)```json\s*(\{.*?\})\s*```", markdown, re.DOTALL)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def _section(markdown: str, heading: str) -> str | None:
    match = re.search(r"^## " + re.escape(heading) + r"\s*\n(.*?)(?=^## |\Z)", markdown, re.DOTALL | re.MULTILINE)
    return match.group(1).strip() if match else None


def output_fields(output_schema) -> list[str]:
    """Media fields of the CompletedRequestStatus variant (e.g. ["video"] or ["images"])."""
    if not isinstance(output_schema, dict):
        return []
    for variant in output_schema.get("oneOf") or []:
        if isinstance(variant, dict) and (variant.get("properties") or {}).get("status", {}).get("const") == "completed":
            return [name for name in variant.get("properties", {}) if name not in {"status", "request_id", "status_url", "cancel_url"}]
    return []


def parse_endpoint(slug: str, page: str) -> dict:
    parser = JSParser(page)
    anchor = page.find(f'canonical_route_slug:"{slug}"')
    if anchor == -1:
        raise ValueError(f"model data missing on api-reference page: {slug}")
    # The model object's fields follow the anchor; bound the search to a generous window.
    window_end = min(len(page), anchor + 400_000)

    markdown = model_markdown(parser)
    js_schema = _clean(_read_key(parser, "input_schema", anchor, window_end))
    markdown_schema = _json_block(markdown, "### Input JSON Schema") if markdown else None
    schema = markdown_schema or js_schema
    if not isinstance(schema, dict) or not isinstance(schema.get("properties"), dict):
        raise ValueError(f"no usable input schema for {slug}")
    output_schema = _json_block(markdown, "### Output Schema") if markdown else None

    family = _clean(_read_key(parser, "family", anchor, window_end)) or []
    current = next((f for f in family if isinstance(f, dict) and f.get("slug") == slug), {})
    return {
        "slug": slug,
        "title": _read_key(parser, "title", anchor, window_end),
        "variant_title": current.get("variant_title") or _read_key(parser, "variant_title", anchor, window_end),
        "type": current.get("type"),
        "operation": current.get("operation_type_readable"),
        "description": _read_key(parser, "description", anchor, window_end),
        "input_schema": schema,
        "schema_source": "markdown" if markdown_schema else "serialized",
        "ui_schema": _clean(_read_key(parser, "ui_schema", anchor, window_end)) or {},
        "output_fields": output_fields(output_schema),
        "pricing_description": _read_key(parser, "pricing_description", anchor, window_end)
        or (_section(markdown, "Pricing") if markdown else None),
        "job_set_type": _read_key(parser, "job_set_type", anchor, window_end),
        "family": [f["slug"] for f in family if isinstance(f, dict) and isinstance(f.get("slug"), str)],
        "docs_url": f"{HOST}/models/{slug}/api-reference",
    }


def fetch_endpoint(slug: str) -> dict:
    if not SLUG_RE.match(slug) or ".." in slug:
        raise ValueError(f"unsafe slug: {slug}")
    return parse_endpoint(slug, fetch(f"models/{slug}/api-reference"))


# --------------------------------------------------------------------------- docs.higgsfield.ai

def resolve_refs(schema):
    """Inline local `#/$defs/...` references so every property is self-describing."""
    if not isinstance(schema, dict):
        return schema
    defs = schema.get("$defs") or schema.get("definitions") or {}

    def walk(node, depth=0):
        if depth > 20:
            return node
        if isinstance(node, list):
            return [walk(item, depth + 1) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith(("#/$defs/", "#/definitions/")):
            target = defs.get(ref.rsplit("/", 1)[-1])
            if isinstance(target, dict):
                merged = {**walk(target, depth + 1), **{k: v for k, v in node.items() if k != "$ref"}}
                return walk(merged, depth + 1)
        return {k: walk(v, depth + 1) for k, v in node.items() if k not in ("$defs", "definitions")}

    return walk(schema)


DOCS_SECTION = re.compile(r"(?m)^# (.+?) API\s*$")


def parse_docs_endpoints(text: str) -> list[dict]:
    """Generation endpoints documented in llms-full.txt (some are absent from the explore pages)."""
    entries = []
    heads = list(DOCS_SECTION.finditer(text))
    for n, head in enumerate(heads):
        body = text[head.end(): heads[n + 1].start() if n + 1 < len(heads) else len(text)]
        endpoint = re.search(r"\*\*Endpoint ID:\*\* `([^`]+)`", body)
        source = re.search(r"Source: https://docs\.higgsfield\.ai/docs/models/([\w.-]+)/([\w.-]+)", body)
        block = re.search(r'Complete JSON schema">\s*```json[^\n]*\n(.*?)\n\s*```', body, re.DOTALL)
        if not (endpoint and source and block):
            continue  # overview pages, training workflows (no Endpoint ID), shared docs
        slug = endpoint.group(1).strip("/")
        if not SLUG_RE.match(slug):
            continue
        try:
            schema = resolve_refs(json.loads(block.group(1)))
        except ValueError:
            continue
        if not isinstance(schema.get("properties"), dict):
            continue
        family_title, _, variant = head.group(1).partition(" — ")
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        description = lines[1] if len(lines) > 1 and not lines[1].startswith(("<", "[", "*")) else ""
        if re.search(r'\bimages\b\s*(\?\.)?\[0\]|result\["images"\]', body):
            outputs = ["images"]
        elif re.search(r'result\["video"\]|\.video\b', body):
            outputs = ["video"]
        else:
            outputs = []
        entries.append({
            "slug": slug,
            "title": family_title.strip(),
            "variant_title": variant.strip() or family_title.strip(),
            "type": None,
            "operation": variant.strip(),
            "description": description,
            "input_schema": schema,
            "schema_source": "docs",
            "ui_schema": {},
            "output_fields": outputs,
            "pricing_description": _section(body, "Pricing"),
            "job_set_type": None,
            "family": [],
            "docs_url": f"https://docs.higgsfield.ai/docs/models/{source.group(1)}/{source.group(2)}",
            "docs_family": source.group(1),
        })
    return entries


def fetch_docs() -> str:
    url = "https://docs.higgsfield.ai/docs/llms-full.txt"
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
        data = response.read(MAX_PAGE_BYTES * 4 + 1)
    if len(data) > MAX_PAGE_BYTES * 4:
        raise ValueError("docs file too large")
    return data.decode("utf-8")


def merge_docs(families: dict[str, dict], endpoints: dict[str, dict], docs: list[dict]) -> list[str]:
    """Add documented endpoints the explore pages do not list. Returns the added slugs."""
    by_docs_family: dict[str, list[dict]] = {}
    known_folder: dict[str, str] = {}  # docs folder -> explore family slug (via an endpoint both share)
    for entry in docs:
        by_docs_family.setdefault(entry["docs_family"], []).append(entry)
        for family in families.values():
            if entry["slug"] in family["modes"]:
                known_folder[entry["docs_family"]] = family["slug"]
    added = []
    for folder, entries in by_docs_family.items():
        new = [e for e in entries if e["slug"] not in endpoints]
        if not new:
            continue
        for entry in new:
            endpoints[entry["slug"]] = entry
            added.append(entry["slug"])
        if folder in known_folder:  # a hidden mode of a model we already have
            families[known_folder[folder]]["modes"] = sorted({*families[known_folder[folder]]["modes"],
                                                              *(e["slug"] for e in new)})
            continue
        first = new[0]
        company = "Higgsfield" if first["slug"].startswith("higgsfield") else first["slug"].split("/")[0].title()
        families[f"docs:{folder}"] = {
            "slug": folder,
            "title": first["title"].title() if first["title"].isupper() else first["title"],
            "company": company,
            "description": first["description"],
            "output_types": ["image" if "images" in first["output_fields"] else "video"],
            "default_mode_id": first["slug"],
            "entry_points": [],
            "pricing": None,
            "released_on": None,
            "tags": ["docs-only"],
            "modes": sorted(e["slug"] for e in new),
        }
    return added


# --------------------------------------------------------------------------- pricing page

def parse_pricing(page: str) -> dict[str, dict]:
    """Per-endpoint price ranges from open.higgsfield.ai/pricing (current price incl. public discount)."""
    parser = JSParser(page)
    prices: dict[str, dict] = {}
    for match in re.finditer(r'\{id:"[0-9a-f-]{36}",slug:"([^"]+)",model_slug:', page):
        try:
            mode = _clean(parser.value_at(match.start()))
        except (ValueError, IndexError):
            continue
        pricing = mode.get("pricing") if isinstance(mode, dict) else None
        if not isinstance(pricing, dict) or not isinstance(pricing.get("primary"), dict):
            continue
        primary = pricing["primary"]
        options = [o for o in pricing.get("options") or [] if isinstance(o, dict) and o.get("amount")]
        amounts = sorted({float(o["amount"]) for o in options} | {float(primary["amount"])})
        list_amounts = sorted({float(o.get("original_amount") or o["amount"]) for o in options}
                              | {float(primary.get("original_amount") or primary["amount"])})
        prices[mode["slug"]] = {
            "unit": primary.get("unit"),                     # "second" | "image"
            "min": amounts[0], "max": amounts[-1],          # current price per unit
            "list_min": list_amounts[0], "list_max": list_amounts[-1],
            "discount_percentage": primary.get("discount_percentage") or 0,
            "exact": primary.get("qualifier") == "exact" and len(amounts) == 1,
        }
    return prices


# --------------------------------------------------------------------------- main

def sync() -> dict:
    families: dict[str, dict] = {}
    for path in EXPLORE_PAGES:
        for family in parse_families(fetch(path)):
            families.setdefault(family["slug"], family)
    seeds = {f["default_mode_id"] for f in families.values()}
    for family in families.values():
        for entry in family.get("entry_points") or []:
            if isinstance(entry, dict) and isinstance(entry.get("mode_id"), str):
                seeds.add(entry["mode_id"])
    if not seeds:
        raise RuntimeError("no model families found on explore pages")

    endpoints: dict[str, dict] = {}
    failures: dict[str, str] = {}
    pending = set(seeds)
    while pending:
        batch = sorted(pending - endpoints.keys() - failures.keys())
        pending = set()
        with ThreadPoolExecutor(max_workers=6) as pool:
            for slug, result in zip(batch, pool.map(_safe_fetch_endpoint, batch)):
                if isinstance(result, dict):
                    endpoints[slug] = result
                    pending.update(result["family"])
                else:
                    failures[slug] = result
        pending -= endpoints.keys() | failures.keys()

    # Attach each endpoint to the explore family whose modes include it.
    for family in families.values():
        root = endpoints.get(family["default_mode_id"])
        # Single-endpoint models list no siblings: the default endpoint is the only mode.
        family["modes"] = sorted(root["family"]) if root and root["family"] else [family["default_mode_id"]]
    if len(endpoints) < MIN_ENDPOINTS:
        raise RuntimeError(f"only {len(endpoints)} endpoints parsed; refusing to write a partial catalog")

    # Documented endpoints that the explore pages do not list (e.g. SOUL Cinema).
    try:
        docs_added = merge_docs(families, endpoints, parse_docs_endpoints(fetch_docs()))
    except Exception as error:
        failures["docs.higgsfield.ai"] = f"{type(error).__name__}: {error}"
        docs_added = []
    for endpoint in endpoints.values():
        endpoint["input_schema"] = resolve_refs(endpoint["input_schema"])

    # Published price ranges (used for the price badge when no live estimate is available).
    try:
        prices = parse_pricing(fetch("pricing"))
    except Exception as error:
        failures["pricing"] = f"{type(error).__name__}: {error}"
        prices = {}
    for slug, endpoint in endpoints.items():
        endpoint["price"] = prices.get(slug)
    return {
        "schema_version": 1,
        "source": HOST,
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "families": sorted(families.values(), key=lambda f: (f.get("company") or "", f["title"] or "")),
        "endpoints": dict(sorted(endpoints.items())),
        "docs_only_endpoints": sorted(docs_added),
        "failures": failures,
    }


def _safe_fetch_endpoint(slug: str):
    try:
        return fetch_endpoint(slug)
    except Exception as error:
        return f"{type(error).__name__}: {error}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    catalog = sync()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(catalog, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"families: {len(catalog['families'])}  endpoints: {len(catalog['endpoints'])}  "
          f"failures: {len(catalog['failures'])}  -> {args.out}")
    for slug, error in catalog["failures"].items():
        print(f"  FAILED {slug}: {error}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
