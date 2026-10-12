"""One ComfyUI node per Higgsfield model family, generated from the bundled catalog."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from comfy_api.latest import io, ui

from . import credentials, media, schema_check
from .catalog import FamilySpec, ModeSpec, load_families
from .client import HiggsfieldClient, HiggsfieldError
from .params import Param, ParamError, build_input, classify, mode_params, to_argument

log = logging.getLogger("higgsfield")

RUN_GENERATE = "generate"
RUN_ESTIMATE = "estimate only"
MEDIA_KINDS = ("image", "video", "audio")
MODE_INPUT = "mode"
# Ids used by the node itself; an API parameter with one of these names gets a different input id.
NODE_INPUT_IDS = {MODE_INPUT, "prompt", "seed", "variation", "run", "max_usd", "save_output", "timeout_minutes"}
RENAMED_INPUTS = {"mode": "quality_mode"}  # Kling O1/O3 "mode" = std/pro/4k


@dataclass
class NodeSpec:
    """Everything needed to rebuild a node's request outside of execution (used by the live price badge)."""
    family: FamilySpec
    params_by_mode: dict
    modes_by_key: dict
    hoist_prompt: bool
    seed_param: Param | None
    multi: bool


NODE_SPECS: dict[str, NodeSpec] = {}


def _assign_input_ids(params: list[Param], reserved: set[str]) -> list[Param]:
    for param in params:
        if param.name in reserved:
            param.input_id = RENAMED_INPUTS.get(param.name, f"api_{param.name}")
    return params


# --------------------------------------------------------------------------- schema building

def _hoistable(family: FamilySpec, name: str) -> bool:
    return all(name in (m.schema.get("properties") or {}) for m in family.modes)


def _hoisted_seed(family: FamilySpec) -> Param:
    params = [classify("seed", m.schema["properties"]["seed"], False) for m in family.modes]
    seed = params[0]
    seed.minimum = max(p.minimum for p in params)
    seed.maximum = min(p.maximum for p in params)
    seed.default = seed.minimum
    seed.tooltip = ("Seed sent to the model. With 'fixed', re-queuing unchanged settings reuses the last result "
                    "(no new charge); change it, or set control to increment/randomize, for a new generation.")
    return seed


def _common_inputs() -> list:
    return [
        io.Combo.Input("run", options=[RUN_GENERATE, RUN_ESTIMATE], default=RUN_GENERATE,
                       tooltip="'estimate only' asks Higgsfield for the exact price of these settings "
                               "without generating (free). Downstream nodes are skipped."),
        io.Float.Input("max_usd", default=0.0, min=0.0, max=1000.0, step=0.01, round=0.01,
                       display_name="max cost (USD)",
                       tooltip="Safety limit: if Higgsfield's estimate is above this, nothing is submitted. "
                               "0 = no limit."),
        io.Boolean.Input("save_output", default=True, advanced=True, display_name="save output",
                         tooltip="Save results to ComfyUI's output/higgsfield folder (Higgsfield deletes "
                                 "results after ~7 days). Off = temp folder."),
        io.Int.Input("timeout_minutes", default=30, min=1, max=240, advanced=True, display_name="timeout (minutes)",
                     tooltip="How long to wait for the generation before giving up."),
    ]


def _aliases(family: FamilySpec) -> list[str]:
    aliases = ["higgsfield", family.title, family.slug] + ([family.company] if family.company else [])
    if any("GPT Image" in m.key for m in family.modes):
        aliases += ["gpt image", "gpt", "openai", "chatgpt image"]
    return aliases


def _description(family: FamilySpec) -> str:
    lines = [f"{family.company} {family.title} via the Higgsfield API. {family.description}".strip()]
    if family.list_price:
        lines.append(f"List price {family.list_price} (use run = 'estimate only' for the exact price).")
    lines.append("Modes: " + ", ".join(m.key for m in family.modes) + ".")
    lines.append("API key: ComfyUI Settings → Higgsfield.")
    return " ".join(lines)


def make_node(family: FamilySpec) -> type[io.ComfyNode]:
    hoist_prompt = _hoistable(family, "prompt")
    hoist_seed = _hoistable(family, "seed")
    skip = {n for n, flag in (("prompt", hoist_prompt), ("seed", hoist_seed)) if flag}
    multi = len(family.modes) > 1
    # Nested (multi-mode) values share a dict with the selected mode key under "mode";
    # flat (single-mode) inputs share the top level with the node's own inputs.
    reserved = {MODE_INPUT} if multi else NODE_INPUT_IDS
    params_by_mode: dict[str, list[Param]] = {
        m.key: _assign_input_ids(mode_params(m.schema, m.ui_schema, skip), reserved) for m in family.modes}
    modes_by_key: dict[str, ModeSpec] = {m.key: m for m in family.modes}
    seed_param = _hoisted_seed(family) if hoist_seed else None

    def define_schema(cls) -> io.Schema:
        inputs: list = []
        if hoist_prompt:
            inputs.append(io.String.Input("prompt", multiline=True, default="",
                                          tooltip="Prompt sent to the model (required by most modes)."))
        if multi:
            inputs.append(io.DynamicCombo.Input(
                MODE_INPUT,
                options=[io.DynamicCombo.Option(m.key, [build_input(p, io) for p in params_by_mode[m.key]])
                         for m in family.modes],
                tooltip="Higgsfield endpoint to use. Each mode shows exactly the settings that endpoint accepts.",
            ))
        else:
            inputs.extend(build_input(p, io) for p in params_by_mode[family.modes[0].key])
        if seed_param is not None:
            inputs.append(build_input(seed_param, io))
        else:
            inputs.append(io.Int.Input("variation", default=0, min=0, max=2147483647,
                                       control_after_generate=io.ControlAfterGenerate.fixed,
                                       tooltip="Not sent to Higgsfield. With unchanged inputs ComfyUI reuses the "
                                               "last result (no new charge); change this, or set it to "
                                               "increment/randomize, to generate again."))
        inputs.extend(_common_inputs())
        if family.output == "video":
            outputs = [io.Video.Output("video"), io.String.Output("video_url", display_name="video url"),
                       io.String.Output("info")]
        else:
            outputs = [io.Image.Output("images"), io.String.Output("image_urls", display_name="image urls"),
                       io.String.Output("info")]
        return io.Schema(
            node_id=family.node_id,
            display_name=family.display_name,
            category=family.category,
            description=_description(family),
            inputs=inputs,
            outputs=outputs,
            hidden=[io.Hidden.unique_id],
            search_aliases=_aliases(family),
        )

    async def execute(cls, **kwargs) -> io.NodeOutput:
        if multi:
            selected = kwargs.get(MODE_INPUT) or {}
            key = selected.get(MODE_INPUT)
            if key not in modes_by_key:
                raise HiggsfieldError(f"Unknown mode '{key}'. Re-select the mode on the node.")
            values = selected
        else:
            key, values = family.modes[0].key, kwargs
        mode = modes_by_key[key]
        return await run_node(cls, family, mode, params_by_mode[key], values, kwargs,
                              hoist_prompt=hoist_prompt, seed_param=seed_param)

    NODE_SPECS[family.node_id] = NodeSpec(family=family, params_by_mode=params_by_mode, modes_by_key=modes_by_key,
                                          hoist_prompt=hoist_prompt, seed_param=seed_param, multi=multi)
    name = family.node_id
    return type(name, (io.ComfyNode,), {
        "define_schema": classmethod(define_schema),
        "execute": classmethod(execute),
        "__module__": __name__,
        "__qualname__": name,
    })


# --------------------------------------------------------------------------- execution

def _progress(cls, text: str) -> None:
    try:
        from server import PromptServer

        node_id = getattr(getattr(cls, "hidden", None), "unique_id", None)
        if node_id is not None and PromptServer.instance is not None:
            PromptServer.instance.send_progress_text(text, node_id)
    except Exception:  # progress is cosmetic
        pass


def _interrupted() -> bool:
    try:
        import comfy.model_management as mm

        return mm.processing_interrupted()
    except Exception:
        return False


def _output_dir(save: bool) -> tuple[Path, str]:
    import folder_paths

    base = Path(folder_paths.get_output_directory() if save else folder_paths.get_temp_directory())
    return base / "higgsfield", ("output" if save else "temp")


def _build_arguments(family: FamilySpec, mode: ModeSpec, params: list[Param], values: dict,
                     top: dict, hoist_prompt: bool, seed_param: Param | None):
    """Returns (arguments, uploads) where uploads = [(param, [comfy media values])]."""
    arguments: dict[str, Any] = {}
    uploads: list[tuple[Param, list]] = []
    properties = mode.schema.get("properties") or {}
    required = set(mode.schema.get("required") or [])
    errors: list[str] = []

    if hoist_prompt:
        prompt_param = classify("prompt", properties["prompt"], "prompt" in required)
        try:
            include, value = to_argument(prompt_param, top.get("prompt", ""))
            if include:
                min_length = properties["prompt"].get("minLength")
                max_length = properties["prompt"].get("maxLength")
                if max_length and len(value) > max_length:
                    raise ParamError(f"prompt is {len(value)} characters; this mode allows {max_length}.")
                if min_length and len(value.strip()) < min_length:
                    raise ParamError("prompt is required for this mode.")
                arguments["prompt"] = value
        except ParamError as error:
            errors.append(str(error))
    if seed_param is not None:
        arguments["seed"] = int(top.get("seed", seed_param.default))

    for param in params:
        value = values.get(param.input_id)
        if param.kind in MEDIA_KINDS:
            items = media.image_batch_to_list(value)[:1] if param.kind == "image" else ([value] if value is not None else [])
            if items:
                uploads.append((param, items))
            elif param.required:
                errors.append(f"Connect an input to '{param.label}' (required for {mode.key}).")
            continue
        if param.kind == "media_list":
            connected = [v for _, v in sorted((value or {}).items(), key=lambda kv: _slot_index(kv[0])) if v is not None]
            items = [img for v in connected for img in media.image_batch_to_list(v)] if param.media == "image" else connected
            # minItems only constrains a list that is sent; an optional list may be left out entirely.
            needed = max(param.min_items, 1) if (items or param.required) else 0
            if len(items) > param.max_items:
                errors.append(f"'{param.label}' accepts at most {param.max_items} ({len(items)} connected).")
            elif len(items) < needed:
                errors.append(f"'{param.label}' needs at least {needed} connected {param.media}(s).")
            elif items:
                uploads.append((param, items))
            continue
        try:
            include, converted = to_argument(param, value if value is not None else param.default)
        except ParamError as error:
            errors.append(str(error))
            continue
        if include:
            arguments[param.name] = converted
    if not errors:
        errors = _schema_problems(mode, params, arguments, uploads)
    if errors:
        raise HiggsfieldError(f"{family.title} — {mode.key}: " + " ".join(errors))
    return arguments, uploads


def _schema_problems(mode: ModeSpec, params: list[Param], arguments: dict, uploads: list) -> list[str]:
    """Check the final request (media as placeholder URLs) against the endpoint's full JSON schema,
    including conditional rules, before anything is uploaded or paid for."""
    probe = dict(arguments)
    for param, items in uploads:
        url = f"https://upload.pending/{param.name}.bin"
        probe[param.name] = [url] * len(items) if param.kind == "media_list" else url
    problems = schema_check.errors(probe, mode.schema)
    if not problems:
        return []
    labels = {p.name: p.label for p in params} | {"prompt": "prompt", "seed": "seed"}
    kinds = {p.name: p.kind for p in params}
    lists = [p for p in params if p.kind == "media_list"]
    messages = []
    for problem in problems:
        missing = re.fullmatch(r"missing (\w+)", problem)
        if missing and kinds.get(missing.group(1)) == "media_list" and not any(p.name in probe for p in lists):
            message = "connect at least one of: " + ", ".join(f"'{p.label}'" for p in lists)
        elif missing and kinds.get(missing.group(1)) in ("image", "video", "audio", "media_list"):
            message = f"connect '{labels.get(missing.group(1), missing.group(1))}'"
        elif missing:
            message = f"set '{labels.get(missing.group(1), missing.group(1))}'"
        else:
            message = re.sub(r"^(\w+)", lambda m: f"'{labels.get(m.group(1), m.group(1))}'", problem)
        if message not in messages:
            messages.append(message)
    return [f"Higgsfield requires: {'; '.join(messages)}."]


def _slot_index(name: str) -> int:
    tail = name.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


async def _download(client: HiggsfieldClient, url: str, destination: Path) -> Path:
    """Download a paid result (the caller's RunContext keeps the URL in any error message)."""
    try:
        return await client.download(url, destination)
    except OSError as error:  # disk full, no permission, path too long, …
        raise HiggsfieldError(f"Could not write {destination} ({error.strerror or error}).") from error


def _money(value: Decimal | None) -> str:
    return "n/a" if value is None else f"${value:.4f}"


@dataclass
class RunContext:
    """What the run has done so far — used to make every error message say what happened to your money."""
    family: str
    mode: str
    stage: str = "checking settings"
    request_id: str | None = None
    result_urls: list[str] = field(default_factory=list)

    def describe_failure(self, error: BaseException) -> str:
        where = f"Higgsfield {self.family} — {self.mode}: {self.stage} failed"
        if self.result_urls:
            urls = "\n".join(self.result_urls)
            return (f"{where}: {error}\nThe generation succeeded and was charged (request {self.request_id}). "
                    f"Download the result within ~7 days:\n{urls}")
        if self.request_id:
            return (f"{where}: {error}\nRequest {self.request_id} was accepted by Higgsfield and may still finish; "
                    "check console.higgsfield.ai before running again.")
        return f"{where}: {error}\nNothing was submitted, so nothing was charged."


async def run_node(cls, family: FamilySpec, mode: ModeSpec, params: list[Param], values: dict, top: dict, *,
                   hoist_prompt: bool, seed_param: Param | None) -> io.NodeOutput:
    """Entry point with end-to-end error handling: every failure becomes one clear HiggsfieldError."""
    ctx = RunContext(family=family.title, mode=mode.key)
    try:
        return await _run(cls, ctx, family, mode, params, values, top, hoist_prompt=hoist_prompt, seed_param=seed_param)
    except HiggsfieldError as error:
        if ctx.request_id and ctx.request_id not in str(error):
            raise HiggsfieldError(ctx.describe_failure(error)) from error
        raise
    except (credentials.CredentialError, ParamError) as error:
        raise HiggsfieldError(str(error)) from error
    except asyncio.CancelledError:
        raise
    except Exception as error:  # unexpected: keep the traceback in the console, give the user a clear summary
        log.exception("Higgsfield %s (%s) failed while %s", mode.endpoint, ctx.request_id or "not submitted", ctx.stage)
        raise HiggsfieldError(ctx.describe_failure(f"{type(error).__name__}: {error} (details in the ComfyUI console)")) from error


async def _prepare_media(param: Param, item: Any) -> tuple[bytes, str]:
    kind = param.media or param.kind
    try:
        return await asyncio.to_thread(media.to_upload, kind, item)
    except Exception as error:
        raise HiggsfieldError(f"Could not convert the connected {kind} for '{param.label}' to upload "
                              f"({type(error).__name__}: {error}).") from error


async def _run(cls, ctx: RunContext, family: FamilySpec, mode: ModeSpec, params: list[Param], values: dict,
               top: dict, *, hoist_prompt: bool, seed_param: Param | None) -> io.NodeOutput:
    # Settings first: their errors don't depend on the key and are the more useful message.
    arguments, uploads = _build_arguments(family, mode, params, values, top, hoist_prompt, seed_param)
    ctx.stage = "reading the API key"
    creds = credentials.require()
    run = top.get("run", RUN_GENERATE)
    max_usd = Decimal(str(top.get("max_usd") or 0))
    info: dict[str, Any] = {"model": family.title, "mode": mode.key, "endpoint": mode.endpoint}

    async with HiggsfieldClient(creds) as client:
        ctx.stage = "uploading inputs"
        total = sum(len(items) for _, items in uploads)
        done = 0
        for param, items in uploads:
            urls = []
            for item in items:
                done += 1
                _progress(cls, f"Uploading {param.media or param.kind} {done}/{total}…")
                data, content_type = await _prepare_media(param, item)
                urls.append(await client.upload(data, content_type))
            arguments[param.name] = urls if param.kind == "media_list" else urls[0]
        info["arguments"] = arguments

        ctx.stage = "estimating the cost"
        _progress(cls, "Estimating cost…")
        estimate = None
        try:
            estimate = await client.estimate(mode.endpoint, arguments)
            info["estimate"] = {"usd": str(estimate.usd) if estimate.usd is not None else None,
                                "credits": str(estimate.credits) if estimate.credits is not None else None}
        except HiggsfieldError as error:
            if run == RUN_ESTIMATE or error.status_code in (400, 401, 403, 422):
                raise
            info["estimate_error"] = str(error)
            if max_usd > 0:
                raise HiggsfieldError(f"Could not get a price estimate, so max cost ${max_usd} can't be enforced: {error}")

        if run == RUN_ESTIMATE:
            text = f"{family.title} — {mode.key}\nEstimated cost: {estimate.describe()}\n(nothing was generated)"
            _progress(cls, text)
            from comfy_execution.graph_utils import ExecutionBlocker

            return io.NodeOutput(ExecutionBlocker(None), ExecutionBlocker(None), json.dumps(info, indent=2),
                                 ui=ui.PreviewText(text))

        if max_usd > 0:
            if estimate is None or estimate.usd is None:
                raise HiggsfieldError(f"Higgsfield's estimate has no USD price, so max cost ${max_usd:.2f} can't be "
                                      "enforced. Nothing was submitted. Set max cost to 0 to run without a limit.")
            if estimate.usd > max_usd:
                raise HiggsfieldError(f"Estimated cost {_money(estimate.usd)} is above your max cost ${max_usd:.2f}. "
                                      "Nothing was submitted. Raise 'max cost (USD)' or change the settings.")

        price = f" (est. {estimate.describe()})" if estimate else ""
        ctx.stage = "submitting the generation"
        _progress(cls, f"Submitting{price}…")
        accepted = await client.submit(mode.endpoint, arguments, idempotency_key=str(uuid.uuid4()))
        ctx.request_id = accepted.request_id
        info["request_id"] = accepted.request_id
        log.info("Higgsfield %s accepted: %s", mode.endpoint, accepted.request_id)

        def on_update(payload: dict, elapsed: float) -> None:
            status = str(payload.get("status", "?")).replace("_", " ")
            _progress(cls, f"{status} · {int(elapsed // 60)}:{int(elapsed % 60):02d}{price}\nrequest {accepted.request_id}")

        ctx.stage = "waiting for the result"
        result = await client.wait(accepted, timeout=float(top.get("timeout_minutes", 30)) * 60,
                                   on_update=on_update, should_stop=_interrupted)
        status = result.get("status")
        if status == "interrupted":
            cancelled = await client.cancel(accepted)
            import comfy.model_management as mm

            log.warning("Higgsfield request %s %s", accepted.request_id,
                        "canceled (refunded)" if cancelled else "already processing; it will finish and be charged")
            mm.throw_exception_if_processing_interrupted()
            raise HiggsfieldError("Interrupted.")
        if status == "failed":
            raise HiggsfieldError(f"Higgsfield generation failed: {result.get('error') or 'no reason given'} "
                                  f"(request {accepted.request_id}; failed requests are not charged).")
        if status == "nsfw":
            raise HiggsfieldError(f"Higgsfield content moderation rejected the input or output "
                                  f"(request {accepted.request_id}; not charged).")
        if status == "canceled":
            raise HiggsfieldError(f"The request was canceled (request {accepted.request_id}; refunded).")
        if status != "completed":
            raise HiggsfieldError(f"Higgsfield returned an unexpected status '{status}' (request {accepted.request_id}).")

        if family.output == "video":
            url = (result.get("video") or {}).get("url") if isinstance(result.get("video"), dict) else None
            urls = [url] if url else []
        else:
            urls = [item.get("url") for item in result.get("images") or [] if isinstance(item, dict) and item.get("url")]
        if not urls:
            raise HiggsfieldError(f"Higgsfield completed request {accepted.request_id} but returned no "
                                  f"{'video' if family.output == 'video' else 'images'}.")
        ctx.result_urls = urls  # from here on, any failure must keep these links visible
        info["outputs"] = urls

        ctx.stage = "saving the result"
        folder, folder_type = _output_dir(bool(top.get("save_output", True)))
        stem = f"{family.node_id.removeprefix('Higgsfield_')}_{accepted.request_id[:8]}"
        _progress(cls, "Downloading result…")
        if family.output == "video":
            paths = [await _download(client, urls[0], folder / f"{stem}{media.extension_for(urls[0], '.mp4')}")]
        else:
            paths = [await _download(client, u, folder / f"{stem}_{i}{media.extension_for(u, '.png')}")
                     for i, u in enumerate(urls)]
        info["saved"] = [str(p) for p in paths]
        extras = {k: v.get("url") for k, v in result.items()
                  if k in ("zip", "mov", "jsx", "fbx", "ply") and isinstance(v, dict) and v.get("url")}
        if extras:
            info["extra_outputs"] = extras

        ctx.stage = "loading the result into ComfyUI"
        try:
            if family.output == "video":
                output = media.load_video(paths[0])
                preview = ui.PreviewVideo([ui.SavedResult(paths[0].name, "higgsfield", io.FolderType(folder_type))])
            else:
                output = await asyncio.to_thread(media.load_images, paths)
                preview = ui.SavedImages([ui.SavedResult(p.name, "higgsfield", io.FolderType(folder_type))
                                          for p in paths])
        except Exception as error:
            saved = "\n".join(str(p) for p in paths)
            raise HiggsfieldError(f"The result was saved but could not be opened in ComfyUI "
                                  f"({type(error).__name__}: {error}). Saved file(s):\n{saved}") from error
        _progress(cls, f"Done{price}")
        return io.NodeOutput(output, "\n".join(urls), json.dumps(info, indent=2), ui=preview)


def build_all_nodes() -> list[type[io.ComfyNode]]:
    nodes = []
    try:
        families = load_families()
    except Exception:
        log.exception("Higgsfield: the model catalog could not be loaded; no model nodes are available")
        return nodes
    for family in families:
        try:
            nodes.append(make_node(family))
        except Exception:  # one bad schema must not take down the whole pack
            log.exception("Higgsfield: could not build node for %s", family.slug)
    return nodes
