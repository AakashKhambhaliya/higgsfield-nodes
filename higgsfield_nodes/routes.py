"""Local HTTP routes for the ComfyUI frontend (web/higgsfield.js).

Every route returns JSON — including on unexpected errors (logged with traceback, summarised for the UI).
The API key is accepted on PUT and never returned; GET only reports a masked key ID and its source.
"""

from __future__ import annotations

import asyncio
import functools
import logging

from . import credentials
from .client import HiggsfieldClient, HiggsfieldError

log = logging.getLogger("higgsfield")
PREFIX = "/higgsfield"
_registered = False

# Cheapest documented estimate call, used only to check that a key is accepted.
VALIDATION_ENDPOINT = "higgsfield-ai/soul/v2/standard"
VALIDATION_BODY = {"prompt": "key check"}


class BadRequest(ValueError):
    pass


def register() -> None:
    global _registered
    if _registered:
        return
    try:
        from aiohttp import web
        from server import PromptServer
    except ImportError:
        return
    if getattr(PromptServer, "instance", None) is None:
        return
    routes = PromptServer.instance.routes

    def safe(handler):
        """Expected problems -> 400 with the message; anything else -> 500, logged with traceback."""
        @functools.wraps(handler)
        async def wrapper(request):
            try:
                return await handler(request)
            except (BadRequest, credentials.CredentialError, HiggsfieldError) as error:
                status = 400 if not isinstance(error, HiggsfieldError) or error.status_code != 401 else 401
                return web.json_response({"error": str(error)}, status=status)
            except asyncio.CancelledError:
                raise
            except Exception as error:
                log.exception("Higgsfield route %s failed", request.path)
                return web.json_response({"error": f"Unexpected error ({type(error).__name__}: {error}). "
                                                   "Details are in the ComfyUI console."}, status=500)
        return wrapper

    async def json_body(request) -> dict:
        try:
            body = await request.json()
        except ValueError as error:
            raise BadRequest("The request body must be JSON.") from error
        if not isinstance(body, dict):
            raise BadRequest("The request body must be a JSON object.")
        return body

    # ---------------------------------------------------------------- API key

    @routes.get(f"{PREFIX}/key")
    @safe
    async def get_key(request):
        return web.json_response(credentials.status())

    @routes.put(f"{PREFIX}/key")
    @safe
    async def put_key(request):
        if credentials.managed_by_environment():
            raise BadRequest("This ComfyUI gets its Higgsfield key from the server's HF_KEY environment variable, "
                             "so it can't be changed here. Change the variable and restart ComfyUI.")
        body = await json_body(request)
        key_id, secret = credentials.parse_combined(str(body.get("key", "")))
        warning = None
        if body.get("validate", True):
            try:
                await _validate(credentials.Credentials(key_id, secret, "validation"))
            except HiggsfieldError as error:
                # Only an authentication failure proves the key is wrong; e.g. a new account with no
                # credits (403) or a network problem must not block saving a valid key.
                if error.status_code == 401:
                    raise
                warning = f"Saved, but the check with Higgsfield did not complete: {error}"
        try:
            credentials.save(key_id, secret)
        except OSError as error:
            raise BadRequest(f"Could not save the key on this computer ({error.strerror or error}).") from error
        log.info("Higgsfield API key saved (%s)", credentials.mask(key_id))
        return web.json_response(credentials.status() | ({"warning": warning} if warning else {}))

    @routes.delete(f"{PREFIX}/key")
    @safe
    async def delete_key(request):
        if credentials.managed_by_environment():
            raise BadRequest("The key comes from the server's HF_KEY environment variable; remove it there.")
        try:
            credentials.delete()
        except OSError as error:
            raise BadRequest(f"Could not remove the saved key ({error.strerror or error}).") from error
        return web.json_response(credentials.status())

    @routes.post(f"{PREFIX}/key/test")
    @safe
    async def test_key(request):
        estimate = await _validate(credentials.require())
        return web.json_response({"ok": True, "sample_estimate": estimate.describe()})

    # ---------------------------------------------------------------- model catalog

    @routes.get(f"{PREFIX}/catalog")
    @safe
    async def get_catalog(request):
        from . import updater

        return web.json_response(updater.status())

    @routes.post(f"{PREFIX}/catalog/update")
    @safe
    async def update_catalog(request):
        from . import updater

        try:
            result = await asyncio.to_thread(updater.update_now)
        except Exception as error:
            log.warning("Higgsfield catalog update failed: %s", error)
            return web.json_response({"error": f"Update failed; your current model list was kept. ({error})"},
                                     status=502)
        return web.json_response(updater.status() | {"result": result})

    @routes.put(f"{PREFIX}/catalog/settings")
    @safe
    async def catalog_settings(request):
        from . import updater

        auto = (await json_body(request)).get("auto_update")
        if not isinstance(auto, bool):
            raise BadRequest("auto_update must be true or false.")
        updater.save_settings(auto_update=auto)
        return web.json_response(updater.status())

    # ---------------------------------------------------------------- pack version / updates

    @routes.get(f"{PREFIX}/version")
    @safe
    async def get_version(request):
        from .versions import version_status

        return web.json_response(await version_status())

    # ---------------------------------------------------------------- pickers

    @routes.get(f"{PREFIX}/pickers")
    @safe
    async def get_pickers(request):
        from .pickers import picker_map

        return web.json_response(picker_map())

    @routes.get(PREFIX + "/options/{kind}")
    @safe
    async def get_options(request):
        from .pickers import PICKERS, fetch_options

        kind = request.match_info["kind"]
        if kind not in PICKERS:
            return web.json_response({"error": f"Unknown list '{kind}'."}, status=404)
        async with HiggsfieldClient(credentials.require()) as client:
            items = await fetch_options(client, kind, request.query.get("search", ""))
        return web.json_response({"kind": kind, "items": items})

    # ---------------------------------------------------------------- live price badge

    @routes.post(f"{PREFIX}/price")
    @safe
    async def get_price(request):
        from .pricing import price_for

        body = await json_body(request)
        values = body.get("values") if isinstance(body.get("values"), dict) else {}
        linked = [str(x) for x in body.get("linked") or [] if isinstance(x, str)]
        return web.json_response(await price_for(str(body.get("node", "")), values, linked))

    _registered = True


async def _validate(creds: credentials.Credentials):
    async with HiggsfieldClient(creds) as client:
        return await client.estimate(VALIDATION_ENDPOINT, VALIDATION_BODY)
