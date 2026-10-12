"""Soul ID nodes: train a reusable character (custom reference) and list existing ones.

Docs: https://docs.higgsfield.ai/docs/models/soul-id/create-character
The resulting id goes into the `custom_reference_id` ("Soul ID") field of Soul 2 / Soul Cinema / Soul Standard
(right-click that widget → convert to input to wire it).
"""

from __future__ import annotations

import asyncio
import json
import logging

from comfy_api.latest import io, ui

from . import credentials, media
from .client import HiggsfieldClient, HiggsfieldError
from .nodes import _interrupted, _progress

log = logging.getLogger("higgsfield")
VERSIONS = ["v2", "cinema", "v1"]  # v2 = Soul 2, cinema = SOUL Cinema, v1 = Soul Standard
TERMINAL = {"completed", "failed"}
MAX_IMAGES = 100


class HiggsfieldSoulIdTrain(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Higgsfield_soul_id_train",
            display_name="Higgsfield Soul ID (train character)",
            category="Higgsfield/Soul ID",
            description="Train a reusable character from 1–100 photos. Outputs the Soul ID to put in the "
                        "'Soul ID' field of Soul 2 (v2), SOUL Cinema (cinema) or Soul Standard (v1). "
                        "Higgsfield does not publish a training price; check console.higgsfield.ai.",
            inputs=[
                io.String.Input("name", default="My character", tooltip="Character name (max 100 characters)."),
                io.Combo.Input("model_version", options=VERSIONS, default="v2",
                               tooltip="Soul family that will use this character: v2 = Soul 2, cinema = SOUL Cinema, "
                                       "v1 = Soul Standard."),
                io.Autogrow.Input("images", template=io.Autogrow.TemplateNames(
                    io.Image.Input("image"), names=[f"image_{i}" for i in range(1, MAX_IMAGES + 1)], min=1),
                    tooltip="Training photos of the same person/character. Image batches count as several photos."),
                io.Boolean.Input("start_training", default=False,
                                 tooltip="Safety switch: training only starts when this is on, because it may "
                                         "use credits."),
                io.Boolean.Input("wait_for_completion", default=True,
                                 tooltip="Wait until training finishes (output is then ready to use). Off = return "
                                         "the ID immediately; check it later with the Soul ID list node."),
                io.Int.Input("timeout_minutes", default=60, min=1, max=480, advanced=True),
            ],
            outputs=[io.String.Output("soul_id", display_name="soul id"), io.String.Output("info")],
            hidden=[io.Hidden.unique_id],
            search_aliases=["higgsfield", "soul id", "character", "custom reference", "train"],
        )

    @classmethod
    async def execute(cls, name: str, model_version: str, images: dict, start_training: bool,
                      wait_for_completion: bool, timeout_minutes: int) -> io.NodeOutput:
        state = {"reference_id": None}
        try:
            return await cls._train(state, name, model_version, images, start_training, wait_for_completion,
                                    timeout_minutes)
        except (HiggsfieldError, credentials.CredentialError) as error:
            raise HiggsfieldError(_with_reference(str(error), state["reference_id"])) from error
        except asyncio.CancelledError:
            raise
        except Exception as error:
            log.exception("Higgsfield Soul ID training failed")
            raise HiggsfieldError(_with_reference(f"Soul ID: unexpected error ({type(error).__name__}: {error}); "
                                                  "details in the ComfyUI console.", state["reference_id"])) from error

    @classmethod
    async def _train(cls, state: dict, name: str, model_version: str, images: dict, start_training: bool,
                     wait_for_completion: bool, timeout_minutes: int) -> io.NodeOutput:
        name = (name or "").strip()
        if not name or len(name) > 100:
            raise HiggsfieldError("Soul ID: name must be 1–100 characters.")
        frames = [img for _, value in sorted((images or {}).items(), key=lambda kv: _index(kv[0]))
                  if value is not None for img in media.image_batch_to_list(value)]
        if not 1 <= len(frames) <= MAX_IMAGES:
            raise HiggsfieldError(f"Soul ID: connect 1–{MAX_IMAGES} training images ({len(frames)} connected).")
        if not start_training:
            text = (f"Ready to train '{name}' ({model_version}) from {len(frames)} photo(s).\n"
                    "Turn on 'start_training' and queue again to start.")
            _progress(cls, text)
            from comfy_execution.graph_utils import ExecutionBlocker

            return io.NodeOutput(ExecutionBlocker(None), json.dumps({"ready": True, "images": len(frames)}),
                                 ui=ui.PreviewText(text))

        async with HiggsfieldClient(credentials.require()) as client:
            urls = []
            for n, frame in enumerate(frames, 1):
                _progress(cls, f"Uploading photo {n}/{len(frames)}…")
                try:
                    data, content_type = await asyncio.to_thread(media.image_to_upload, frame)
                except Exception as error:
                    raise HiggsfieldError(f"Soul ID: could not convert photo {n} for upload ({error}).") from error
                urls.append(await client.upload(data, content_type))
            _progress(cls, "Starting Soul ID training…")
            reference = await client.create_custom_reference(name, model_version, urls)
            reference_id = str(reference["id"])
            state["reference_id"] = reference_id
            if wait_for_completion and reference.get("status") not in TERMINAL:
                loop = asyncio.get_running_loop()
                start, delay = loop.time(), 5.0
                while True:
                    for _ in range(int(delay * 2)):
                        if _interrupted():
                            import comfy.model_management as mm

                            log.warning("Stopped waiting for Soul ID %s; training continues on Higgsfield.", reference_id)
                            mm.throw_exception_if_processing_interrupted()
                        await asyncio.sleep(0.5)
                    reference = await client.get_custom_reference(reference_id)
                    elapsed = loop.time() - start
                    _progress(cls, f"Training '{name}': {reference.get('status')} · {int(elapsed // 60)} min\n"
                                   f"Soul ID {reference_id}")
                    if reference.get("status") in TERMINAL:
                        break
                    if elapsed > timeout_minutes * 60:
                        raise HiggsfieldError(f"Still training after {timeout_minutes} min. Soul ID: {reference_id} "
                                              "(check it later with the Soul ID list node).")
                    delay = min(delay * 1.5, 30.0)
            if reference.get("status") == "failed":
                raise HiggsfieldError(f"Soul ID training failed: {reference.get('fail_reason') or 'no reason given'} "
                                      f"(Soul ID {reference_id}).")
        info = {k: reference.get(k) for k in ("id", "name", "model_version", "status", "thumbnail_url", "created_at")}
        text = f"Soul ID '{name}' ({model_version}): {reference.get('status')}\n{reference_id}"
        _progress(cls, text)
        return io.NodeOutput(reference_id, json.dumps(info, indent=2), ui=ui.PreviewText(text))


class HiggsfieldSoulIdList(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return io.Schema(
            node_id="Higgsfield_soul_id_list",
            display_name="Higgsfield Soul ID list",
            category="Higgsfield/Soul ID",
            description="Lists the Soul ID characters on your Higgsfield account with their status and version. "
                        "Optionally outputs the ID of one character by name.",
            inputs=[
                io.String.Input("pick_name", default="", tooltip="Optional: output the ID of the newest completed "
                                                                 "character with this name."),
                io.Int.Input("refresh", default=0, min=0, max=2147483647,
                             control_after_generate=io.ControlAfterGenerate.increment,
                             tooltip="Changes every run so the list is fetched fresh."),
            ],
            outputs=[io.String.Output("soul_id", display_name="soul id"), io.String.Output("list")],
            hidden=[io.Hidden.unique_id],
            search_aliases=["higgsfield", "soul id", "character", "custom reference"],
        )

    @classmethod
    async def execute(cls, pick_name: str, refresh: int) -> io.NodeOutput:
        try:
            async with HiggsfieldClient(credentials.require()) as client:
                items = await client.list_custom_references()
        except credentials.CredentialError as error:
            raise HiggsfieldError(str(error)) from error
        lines = [f"{i.get('name')} | {i.get('model_version')} | {i.get('status')} | {i.get('id')}" for i in items]
        text = "\n".join(lines) or "No Soul IDs on this account yet."
        chosen = ""
        if pick_name.strip():
            matches = [i for i in items if (i.get("name") or "").strip().lower() == pick_name.strip().lower()
                       and i.get("status") == "completed"]
            matches.sort(key=lambda i: i.get("created_at") or "", reverse=True)
            if not matches:
                raise HiggsfieldError(f"No completed Soul ID named '{pick_name}'. Available:\n{text}")
            chosen = str(matches[0]["id"])
        return io.NodeOutput(chosen, text, ui=ui.PreviewText(text))


def _with_reference(message: str, reference_id: str | None) -> str:
    if reference_id and reference_id not in message:
        return (f"{message}\nTraining was started on Higgsfield (Soul ID {reference_id}); check it with the "
                "Soul ID list node instead of training again.")
    return message


def _index(name: str) -> int:
    tail = name.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else 0


SOUL_ID_NODES = [HiggsfieldSoulIdTrain, HiggsfieldSoulIdList]
