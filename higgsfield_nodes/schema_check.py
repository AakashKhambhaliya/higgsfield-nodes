"""Small JSON-schema validator for request bodies (no third-party dependency).

Covers the keywords Higgsfield's model schemas use, including the conditional rules
(if/then/else, allOf/anyOf/oneOf, not) that plain `required` lists do not express, e.g.
"Reference to Video needs image_urls or video_urls" or "enhance_prompt needs preset_id + 1–2 images".
"""

from __future__ import annotations

import re
from typing import Any, Callable

_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "array": lambda v: isinstance(v, list),
    "object": lambda v: isinstance(v, dict),
    "null": lambda v: v is None,
}


def errors(value: Any, schema: Any, path: str = "") -> list[str]:
    """Return human-readable problems ("" path = request root). Empty list = valid."""
    if not isinstance(schema, dict):
        return []
    out: list[str] = []
    where = path or "request"

    types = schema.get("type")
    if types is not None:
        allowed = types if isinstance(types, list) else [types]
        if not any(_TYPES.get(t, lambda v: True)(value) for t in allowed):
            return [f"{where} must be {' or '.join(allowed)}"]
    if "const" in schema and value != schema["const"]:
        out.append(f"{where} must be {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        out.append(f"{where} must be one of {', '.join(map(str, schema['enum']))}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            out.append(f"{where} must be ≥ {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            out.append(f"{where} must be ≤ {schema['maximum']}")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and value <= schema["exclusiveMinimum"]:
            out.append(f"{where} must be > {schema['exclusiveMinimum']}")
        if isinstance(schema.get("exclusiveMaximum"), (int, float)) and value >= schema["exclusiveMaximum"]:
            out.append(f"{where} must be < {schema['exclusiveMaximum']}")
        step = schema.get("multipleOf")
        if step and abs(round(value / step) * step - value) > 1e-9:
            out.append(f"{where} must be a multiple of {step}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            out.append(f"{where} is empty" if schema["minLength"] == 1 else f"{where} is too short")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            out.append(f"{where} is longer than {schema['maxLength']} characters")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            out.append(f"{where} has an invalid format")
    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            out.append(f"{where} needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            out.append(f"{where} accepts at most {schema['maxItems']} item(s)")
        if isinstance(schema.get("items"), dict):
            for i, item in enumerate(value):
                out += errors(item, schema["items"], f"{path}[{i}]" if path else f"[{i}]")
    if isinstance(value, dict):
        for name in schema.get("required") or []:
            if name not in value:
                out.append(f"missing {name}" if not path else f"{path}.{name} is missing")
        properties = schema.get("properties") or {}
        for name, sub in properties.items():
            if name in value:
                out += errors(value[name], sub, f"{path}.{name}" if path else name)
        if schema.get("additionalProperties") is False:
            extra = [k for k in value if k not in properties]
            if extra:
                out.append(f"unsupported field(s): {', '.join(extra)}")

    for sub in schema.get("allOf") or []:
        out += errors(value, sub, path)
    if schema.get("anyOf") and not any(not errors(value, sub, path) for sub in schema["anyOf"]):
        out.append(_first(value, schema["anyOf"], path) or f"{where} does not match any allowed form")
    if schema.get("oneOf") and sum(not errors(value, sub, path) for sub in schema["oneOf"]) != 1:
        out.append(_first(value, schema["oneOf"], path) or f"{where} must match exactly one allowed form")
    if "not" in schema and not errors(value, schema["not"], path):
        out.append(f"{where} has a value that is not allowed")
    if "if" in schema:
        branch = schema.get("then") if not errors(value, schema["if"], path) else schema.get("else")
        if branch is not None:
            out += errors(value, branch, path)
    return out


def _first(value, alternatives, path) -> str | None:
    found = [errors(value, alt, path) for alt in alternatives]
    found = [f for f in found if f]
    return min(found, key=len)[0] if found else None
