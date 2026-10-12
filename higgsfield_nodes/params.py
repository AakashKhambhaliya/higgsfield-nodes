"""Map Higgsfield JSON-schema parameters to ComfyUI inputs, and node values back to API arguments.

classify() and to_argument() are pure Python (unit-tested without ComfyUI). build_input() creates
the comfy_api V3 input objects.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

AUTO = "auto"
INT32_MAX = 2147483647

PROMPT_LIKE = {"prompt", "negative_prompt", "brief", "niche", "target_audience"}
MEDIA_SINGLE = {"image": "image", "frame": "image", "video": "video", "audio": "audio"}

# Display names for parameters whose schema has no title.
LABELS = {
    "aspect_ratio": "aspect ratio",
    "generate_audio": "generate audio",
    "negative_prompt": "negative prompt",
    "end_image_url": "end image",
    "last_image_url": "last image",
    "first_frame_url": "first frame",
    "last_frame_url": "last frame",
    "image_url": "image",
    "video_url": "video",
    "audio_url": "audio",
    "image_urls": "reference images",
    "video_urls": "reference videos",
    "audio_urls": "reference audios",
    "item_image_urls": "item images",
    "cfg_scale": "cfg scale",
    "multi_prompt": "multi-shot prompts",
    "multi_shots": "multi shots",
    "elements": "element IDs",
    "custom_reference_id": "Soul ID",
    "custom_reference_strength": "Soul ID strength",
    "style_id": "style ID",
    "preset_id": "preset ID",
    "file_url": "reference document URL",
    "link_url": "reference web page URL",
}

EXTRA_HELP = {
    "multi_prompt": "One shot per line as 'seconds: prompt', e.g. '3: wide shot of the city'. Leave empty for a single shot.",
    "elements": "Comma-separated Kling element IDs.",
    "colors": "Comma-separated colors as #RRGGBB or (r,g,b), e.g. '#ff8800, (0,51,102)'.",
    "background_color": "Background color as #RRGGBB. Leave empty for none.",
    "body_color": "Color as #RRGGBB. Leave empty for automatic.",
    "style_id": "UUID of a Higgsfield Soul style (right-click the node → 'Pick … style' to choose from the list). "
                "Leave empty for none.",
    "preset_id": "UUID of a Higgsfield preset (right-click the node → 'Pick … preset/style' to choose from the list).",
    "custom_reference_id": "ID of a completed Soul ID for this Soul version (right-click the node → 'Pick Soul ID', "
                           "or train one with the Soul ID node). Leave empty for none.",
    "file_url": "Public URL of one document (docx/xlsx/pptx/pdf/md).",
    "link_url": "Public URL of one web page.",
}


@dataclass
class Param:
    name: str
    kind: str                      # see KINDS
    required: bool
    schema: dict
    label: str
    tooltip: str
    advanced: bool = False
    default: Any = None
    options: list[str] = field(default_factory=list)
    option_values: dict[str, Any] = field(default_factory=dict)
    minimum: float | None = None
    maximum: float | None = None
    step: float | None = None
    slider: bool = False
    max_items: int = 0
    min_items: int = 0
    media: str = ""                # image | video | audio for media kinds
    unset: Any = None              # numeric widget value meaning "not sent" (optional numbers without a default)
    input_id: str = ""             # ComfyUI input id; differs from `name` only when the API name clashes

    def __post_init__(self) -> None:
        self.input_id = self.input_id or self.name


KINDS = {"text", "enum", "int", "float", "bool", "seed", "image", "video", "audio", "media_list",
         "url", "csv", "multi_prompt", "color", "colors", "json"}


def _types(spec: dict) -> set[str]:
    t = spec.get("type")
    if isinstance(t, list):
        return {x for x in t if x != "null"}
    if t:
        return {t}
    for alt in spec.get("anyOf") or spec.get("oneOf") or []:
        if isinstance(alt, dict) and alt.get("type") and alt.get("type") != "null":
            return {alt["type"]} if isinstance(alt["type"], str) else set(alt["type"]) - {"null"}
    return set()


def _media_kind(name: str) -> str:
    base = name.removesuffix("_urls").removesuffix("_url")
    for word, kind in MEDIA_SINGLE.items():
        if word in base:
            return kind
    return ""


def humanize(name: str) -> str:
    return LABELS.get(name, name.replace("_", " "))


def classify(name: str, spec: dict, required: bool, ui: dict | None = None) -> Param:
    ui = ui or {}
    options = ui.get("ui:options") or {}
    types = _types(spec)
    advanced = bool(options.get("advanced")) or ui.get("ui:widget") == "hidden"
    label = humanize(name) if name in LABELS else (spec.get("title") or humanize(name)).strip() or humanize(name)
    label = label.lower() if label[:1].isupper() and not label.isupper() else label
    param = Param(name=name, kind="json", required=required, schema=spec, label=label, tooltip="",
                  advanced=advanced and not required, default=spec.get("default"))

    is_uri = spec.get("format") == "uri" or name.endswith("_url")
    item = spec.get("items") if isinstance(spec.get("items"), dict) else {}

    if "enum" in spec:
        values = [v for v in spec["enum"] if v is not None]
        param.kind = "enum"
        param.options = [str(v) for v in values]
        param.option_values = {str(v): v for v in values}
        if param.default is None and not required:
            param.options.insert(0, AUTO)
            param.default = AUTO
        elif param.default is None or str(param.default) not in param.option_values:
            param.default = param.options[0]
        else:
            param.default = str(param.default)
    elif name == "seed":
        param.kind = "seed"
        param.minimum = spec.get("minimum", 0)
        param.maximum = min(spec.get("maximum", INT32_MAX), INT32_MAX)
        param.default = param.default if isinstance(param.default, int) else param.minimum
    elif types & {"integer"} and not types & {"string"}:
        param.kind = "int"
        _numeric(param, spec, ui, integer=True, required=required)
    elif types & {"number"}:
        param.kind = "float"
        _numeric(param, spec, ui, integer=False, required=required)
    elif types == {"boolean"}:
        param.kind = "bool"
        param.default = bool(param.default) if param.default is not None else False
    elif types & {"string"} and is_uri and _media_kind(name):
        param.kind, param.media = _media_kind(name), _media_kind(name)
    elif types & {"string"} and is_uri:
        param.kind = "url"
    elif types & {"string"} and name in ("body_color",):
        param.kind = "color"
    elif types & {"string"}:
        param.kind = "text"
    elif types == {"array"} and (item.get("format") == "uri" or name.endswith("_urls")) and _media_kind(name):
        param.kind, param.media = "media_list", _media_kind(name)
        param.max_items = int(spec.get("maxItems") or (10 if param.media != "image" else 30))
        param.min_items = int(spec.get("minItems") or 0)
    elif types == {"array"} and name == "multi_prompt":
        param.kind = "multi_prompt"
        param.max_items = int(spec.get("maxItems") or 6)
        param.min_items = int(spec.get("minItems") or 0)
    elif types == {"array"} and name == "colors":
        param.kind = "colors"
    elif types == {"array"} and _types(item) == {"string"}:
        param.kind = "csv"
        param.max_items = int(spec.get("maxItems") or 0)
    elif types == {"object"} and name == "background_color":
        param.kind = "color"
    else:
        param.kind = "json"
        param.advanced = not required

    param.tooltip = _tooltip(param, spec, ui)
    return param


def _numeric(param: Param, spec: dict, ui: dict, *, integer: bool, required: bool) -> None:
    step = spec.get("multipleOf")
    param.step = step if step else (1 if integer else 0.01)
    param.minimum = spec.get("minimum")
    param.maximum = spec.get("maximum")
    # Exclusive bounds are not allowed values themselves: move one step inside.
    if param.minimum is None and isinstance(spec.get("exclusiveMinimum"), (int, float)):
        param.minimum = spec["exclusiveMinimum"] + param.step
    if param.maximum is None and isinstance(spec.get("exclusiveMaximum"), (int, float)):
        param.maximum = spec["exclusiveMaximum"] - param.step
    if param.minimum is None:
        param.minimum = 0 if integer else 0.0
    if param.maximum is None:
        param.maximum = INT32_MAX if integer else 1e6
    param.slider = ui.get("ui:widget") == "range"
    if param.default is None and not required:
        # No API default: extend the range by one step below the minimum to mean "not sent".
        param.unset = param.minimum - param.step
        param.minimum = param.unset
        param.default = param.unset
    elif param.default is None:
        param.default = param.minimum
    param.default = int(param.default) if integer else float(param.default)


def _tooltip(param: Param, spec: dict, ui: dict) -> str:
    parts = []
    if spec.get("description"):
        parts.append(spec["description"].strip())
    if ui.get("ui:help"):
        parts.append(str(ui["ui:help"]).strip())
    if param.name in EXTRA_HELP:
        parts.append(EXTRA_HELP[param.name])
    if param.kind == "enum" and AUTO in param.options:
        parts.append("'auto' leaves it unset so the model decides.")
    if param.kind in ("int", "float") and spec.get("minimum") is not None and spec.get("maximum") is not None:
        parts.append(f"Range {spec['minimum']}–{spec['maximum']}.")
    if param.unset is not None:
        parts.append(f"{param.unset:g} = auto (not sent).")
    if param.kind == "media_list":
        parts.append(f"Connect up to {param.max_items} {param.media}{'s' if param.max_items != 1 else ''}"
                     + (f" (at least {param.min_items})." if param.min_items else "."))
    if param.kind in ("text", "url", "csv", "json", "color", "colors") and not param.required:
        parts.append("Leave empty to omit.")
    if param.kind in ("image", "video", "audio") and not param.required:
        parts.append("Optional.")
    parts.append(f"API field: {param.name}")
    return " ".join(p if p.endswith((".", ")", ":")) else p + "." for p in parts[:-1]) + " " + parts[-1]


def mode_params(schema: dict, ui_schema: dict, skip: set[str] = frozenset()) -> list[Param]:
    required = set(schema.get("required") or [])
    params = [classify(name, spec, name in required, (ui_schema or {}).get(name))
              for name, spec in (schema.get("properties") or {}).items() if name not in skip]

    def rank(p: Param) -> tuple:
        group = (0 if p.name in PROMPT_LIKE else
                 1 if p.kind in ("image", "video", "audio") else
                 2 if p.kind == "media_list" else
                 3 if p.kind in ("enum", "int", "float", "bool", "seed") else 4)
        return (p.advanced, group)

    return sorted(params, key=rank)  # stable: keeps schema order within a group


# --------------------------------------------------------------------------- values -> API

class ParamError(ValueError):
    pass


def _snap(value: float, param: Param) -> float:
    """Remove float noise (0.30000000000000004) and honour multipleOf exactly."""
    step = param.schema.get("multipleOf")
    if step:
        decimals = max(0, -Decimal(str(step)).as_tuple().exponent)
        value = round(round(value / step) * step, decimals)
    else:
        value = round(value, 6)
    if param.minimum is not None:
        # With an "auto" sentinel the widget minimum is one step below the real minimum.
        lowest = float(param.minimum) + (float(param.step) if param.unset is not None else 0.0)
        value = max(value, lowest)
    if param.maximum is not None:
        value = min(value, float(param.maximum))
    return value


_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_COLOR_TOKEN = re.compile(r"#?\b[0-9a-fA-F]{6}\b|\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}\s*\)")


def _hex_to_rgb(text: str, label: str) -> list[int]:
    text = text.strip()
    match = _HEX.match(text)
    if match:
        h = match.group(1)
        return [int(h[i:i + 2], 16) for i in (0, 2, 4)]
    parts = [p.strip() for p in text.split(",")]
    if len(parts) == 3 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
        return [int(p) for p in parts]
    raise ParamError(f"{label}: '{text}' is not a color. Use #RRGGBB.")


def parse_multi_prompt(text: str, param: Param) -> list[dict]:
    shots = []
    item = (param.schema.get("items") or {}).get("properties") or {}
    dmin = (item.get("duration") or {}).get("minimum", 1)
    dmax = (item.get("duration") or {}).get("maximum", 15)
    for number, line in enumerate((l.strip() for l in text.splitlines()), 1):
        if not line:
            continue
        match = re.match(r"^(\d+)\s*(?:s|sec|seconds)?\s*[:|\-]\s*(.+)$", line)
        if not match:
            raise ParamError(f"{param.label}: line {number} must look like '5: your shot prompt'.")
        seconds, prompt = int(match.group(1)), match.group(2).strip()
        if not dmin <= seconds <= dmax:
            raise ParamError(f"{param.label}: line {number} duration must be {dmin}–{dmax} seconds.")
        shots.append({"prompt": prompt, "duration": seconds})
    if param.max_items and len(shots) > param.max_items:
        raise ParamError(f"{param.label}: at most {param.max_items} shots.")
    return shots


def to_argument(param: Param, value: Any) -> tuple[bool, Any]:
    """Convert a widget value to its API value. Returns (include, value). Media kinds are handled by the node."""
    kind = param.kind
    if kind == "enum":
        if value == AUTO and AUTO in param.options:
            return False, None
        if value not in param.option_values:
            raise ParamError(f"{param.label}: '{value}' is not one of {', '.join(param.options)}.")
        return True, param.option_values[value]
    if kind in ("int", "float") and param.unset is not None and float(value) <= float(param.unset):
        return False, None
    if kind in ("int", "seed"):
        return True, int(value)
    if kind == "float":
        return True, _snap(float(value), param)
    if kind == "bool":
        return True, bool(value)
    text = "" if value is None else str(value)
    if kind in ("text", "url"):
        if not text.strip():
            if param.required:
                raise ParamError(f"{param.label} is required for this mode.")
            return False, None
        if kind == "url" and not re.match(r"^https?://", text.strip()):
            raise ParamError(f"{param.label} must be an http(s) URL.")
        # Prompt-like text is sent exactly as typed; URLs, IDs and short values are trimmed of pasted whitespace.
        return True, text if param.name in PROMPT_LIKE else text.strip()
    if not text.strip():
        if param.required:
            raise ParamError(f"{param.label} is required for this mode.")
        return False, None
    if kind == "csv":
        items = [p.strip() for p in re.split(r"[,\n]", text) if p.strip()]
        if param.max_items and len(items) > param.max_items:
            raise ParamError(f"{param.label}: at most {param.max_items} values.")
        return True, items
    if kind == "multi_prompt":
        return True, parse_multi_prompt(text, param)
    if kind == "color":
        rgb = _hex_to_rgb(text, param.label)
        if param.name == "background_color":
            return True, {"rgb": rgb}
        return True, "#{:02x}{:02x}{:02x}".format(*rgb)
    if kind == "colors":
        tokens = _COLOR_TOKEN.findall(text)
        if _COLOR_TOKEN.sub("", text).strip(" ,;\n\t"):
            raise ParamError(f"{param.label}: use #RRGGBB values (or (r,g,b)) separated by commas.")
        return True, [{"rgb": _hex_to_rgb(t.strip("() "), param.label)} for t in tokens]
    if kind == "json":
        try:
            return True, json.loads(text)
        except ValueError as error:
            raise ParamError(f"{param.label}: invalid JSON ({error}).") from error
    raise ParamError(f"Unsupported parameter kind {kind} for {param.name}.")


# --------------------------------------------------------------------------- ComfyUI inputs

def build_input(param: Param, io) -> Any:
    """Create the comfy_api V3 input for a parameter (io = comfy_api.latest.io)."""
    common = dict(display_name=param.label, tooltip=param.tooltip)
    advanced = {"advanced": True} if param.advanced else {}
    kind = param.kind
    if kind == "enum":
        return io.Combo.Input(param.input_id, options=param.options, default=param.default, **common, **advanced)
    if kind == "seed":
        return io.Int.Input(param.input_id, default=param.default, min=param.minimum, max=param.maximum,
                            control_after_generate=io.ControlAfterGenerate.fixed, **common, **advanced)
    if kind == "int":
        return io.Int.Input(param.input_id, default=param.default, min=int(param.minimum), max=int(param.maximum),
                            step=int(param.step or 1),
                            display_mode=io.NumberDisplay.slider if param.slider else io.NumberDisplay.number,
                            **common, **advanced)
    if kind == "float":
        return io.Float.Input(param.input_id, default=param.default, min=float(param.minimum), max=float(param.maximum),
                              step=float(param.step or 0.01), round=float(param.step or 0.01),
                              display_mode=io.NumberDisplay.slider if param.slider else io.NumberDisplay.number,
                              **common, **advanced)
    if kind == "bool":
        return io.Boolean.Input(param.input_id, default=param.default, **common, **advanced)
    if kind in ("image", "video", "audio"):
        cls = {"image": io.Image, "video": io.Video, "audio": io.Audio}[kind]
        return cls.Input(param.input_id, optional=not param.required, **common)
    if kind == "media_list":
        cls = {"image": io.Image, "video": io.Video, "audio": io.Audio}[param.media]
        return io.Autogrow.Input(
            param.input_id,
            template=io.Autogrow.TemplateNames(
                cls.Input(param.media),
                names=[f"{param.media}_{i}" for i in range(1, param.max_items + 1)],
                min=0,
            ),
            **common,
        )
    multiline = kind in ("multi_prompt", "json") or param.name in PROMPT_LIKE
    default = param.default if isinstance(param.default, str) else ""
    return io.String.Input(param.input_id, default=default, multiline=multiline, **common, **advanced)
