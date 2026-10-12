"""Async client for the Higgsfield model API (https://docs.higgsfield.ai).

Protocol (docs: requests, polling, file-uploads, billing-and-retention, idempotency, errors):
  POST /estimate/<model>             same JSON body -> {"credits": "1.5", "usd": "0.094"}
  POST /<model>   + Idempotency-Key  -> {"status","request_id","status_url","cancel_url"}
  GET  status_url                    -> queued | in_progress | completed | failed | nsfw | canceled
  POST cancel_url                    -> 202 while still queued, 400 once processing started
  POST /files/generate-upload-url    -> {"public_url","upload_url","upload_headers"}; then PUT bytes

Safety rules applied here:
  * a submission is only ever re-sent with the SAME Idempotency-Key (no duplicate charges)
  * credentials are sent only to api.higgsfield.ai, never to presigned storage or CDN URLs
  * status/cancel URLs from responses must point back at the API host
"""

from __future__ import annotations

import asyncio
import random
import re
import uuid
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

import httpx

from .credentials import Credentials

API_BASE = "https://api.higgsfield.ai"
USER_AGENT = "comfyui-higgsfield/0.1"
TERMINAL = {"completed", "failed", "nsfw", "canceled"}
MAX_DOWNLOAD_BYTES = 4 * 1024 * 1024 * 1024
UPLOAD_CONTENT_TYPES = {"image/jpeg", "image/jpg", "image/png", "image/webp", "image/gif",
                        "audio/wav", "audio/x-wav", "video/mp4"}


class HiggsfieldError(Exception):
    """User-facing error; the message is shown on the node."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False,
                 correlation_id: str | None = None) -> None:
        if correlation_id:
            message = f"{message} (support ref: {correlation_id})"
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


@dataclass
class Estimate:
    credits: Decimal | None
    usd: Decimal | None

    def describe(self) -> str:
        parts = []
        if self.usd is not None:
            parts.append(f"${self.usd:.4f}".rstrip("0").rstrip("."))
        if self.credits is not None:
            parts.append(f"{self.credits.normalize():f} credits")
        return " / ".join(parts) or "unavailable"


@dataclass
class Accepted:
    request_id: str
    status_url: str
    cancel_url: str | None
    raw: dict = field(default_factory=dict)


def _decimal(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text.strip()[:300] or response.reason_phrase
    detail = payload.get("detail") if isinstance(payload, dict) else payload
    if isinstance(detail, list):  # FastAPI validation errors
        lines = []
        for item in detail[:6]:
            if isinstance(item, dict):
                where = ".".join(str(p) for p in item.get("loc", []) if p != "body")
                lines.append(f"{where}: {item.get('msg', item)}" if where else str(item.get("msg", item)))
            else:
                lines.append(str(item))
        return "; ".join(lines)
    return str(detail)[:500]


def error_from_response(response: httpx.Response, action: str) -> HiggsfieldError:
    code = response.status_code
    detail = _detail(response)
    correlation = response.headers.get("X-Correlation-ID")
    messages = {
        401: "Higgsfield rejected the API key. Check it in ComfyUI Settings → Higgsfield.",
        403: f"Not enough Higgsfield credits for this {action}. Top up at console.higgsfield.ai. ({detail})",
        404: f"Higgsfield could not find this model or request for your account. ({detail})",
        423: f"This model is temporarily blocked by Higgsfield; try again later. ({detail})",
        503: f"This model is disabled or not ready on Higgsfield; try again later. ({detail})",
    }
    if code in messages:
        message = messages[code]
    elif code == 400 and "concurrent" in detail.lower():
        message = f"Your Higgsfield concurrency limit is reached; wait for running jobs to finish. ({detail})"
    elif code in (400, 422):
        message = f"Higgsfield rejected the {action} settings: {detail}"
    else:
        message = f"Higgsfield {action} failed (HTTP {code}): {detail}"
    return HiggsfieldError(message, status_code=code, retryable=code in (408, 429) or code >= 500,
                           correlation_id=correlation)


def _same_host(url: str, base: str) -> bool:
    a, b = urlsplit(url), urlsplit(base)
    return a.scheme == b.scheme == "https" and a.netloc == b.netloc


def _json(response: httpx.Response, what: str) -> Any:
    try:
        return response.json()
    except ValueError as error:
        raise HiggsfieldError(f"Higgsfield returned an unreadable {what} response "
                              f"(HTTP {response.status_code}).") from error


def _https(url: str, what: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
        raise HiggsfieldError(f"Higgsfield returned an invalid {what}.")
    return url


class HiggsfieldClient:
    def __init__(self, credentials: Credentials, *, base_url: str = API_BASE,
                 transport: httpx.AsyncBaseTransport | None = None,
                 sleep: Callable[[float], Awaitable[None]] = asyncio.sleep) -> None:
        self.base_url = base_url.rstrip("/")
        self._api = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": credentials.header_value, "User-Agent": USER_AGENT},
            timeout=httpx.Timeout(60.0, connect=20.0),
            follow_redirects=False,
            transport=transport,
        )
        # No credentials on this one: presigned uploads and CDN downloads.
        self._public = httpx.AsyncClient(timeout=httpx.Timeout(300.0, connect=20.0),
                                         follow_redirects=True, transport=transport,
                                         headers={"User-Agent": USER_AGENT})
        self._sleep = sleep

    async def __aenter__(self) -> "HiggsfieldClient":
        return self

    async def __aexit__(self, *exc) -> None:
        await self.close()

    async def close(self) -> None:
        await self._api.aclose()
        await self._public.aclose()

    # ------------------------------------------------------------------ helpers

    def _api_url(self, url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            if not _same_host(url, self.base_url):
                raise HiggsfieldError("Higgsfield returned a status URL on an unexpected host; refusing to send the key there.")
            return url
        return f"{self.base_url}/{url.lstrip('/')}"

    async def _safe_request(self, method: str, url: str, action: str, *, attempts: int = 5,
                            **kwargs: Any) -> httpx.Response:
        """For calls that are free and safe to repeat (status, estimate, lists, upload URLs):
        retry network errors, 408/429 and 5xx with backoff. Anything but 2xx is an error."""
        delay = 1.0
        for attempt in range(1, attempts + 1):
            try:
                response = await self._api.request(method, self._api_url(url), **kwargs)
            except httpx.TransportError as error:
                if attempt == attempts:
                    raise HiggsfieldError(f"Network error during {action}: {error}") from error
            else:
                if response.is_success:
                    return response
                error = error_from_response(response, action)
                if not error.retryable or attempt == attempts:
                    raise error
            await self._sleep(delay + random.uniform(0, 0.5))
            delay = min(delay * 2, 15.0)
        raise AssertionError("unreachable")

    async def get_json(self, url: str, action: str) -> Any:
        return _json(await self._safe_request("GET", url, action), action)

    # ------------------------------------------------------------------ API

    async def estimate(self, endpoint: str, arguments: Mapping[str, Any]) -> Estimate:
        response = await self._safe_request("POST", f"/estimate/{endpoint.strip('/')}", "estimate",
                                            json=dict(arguments))
        payload = _json(response, "estimate") if response.content else {}
        if not isinstance(payload, dict):
            raise HiggsfieldError("Higgsfield returned an invalid estimate.")
        return Estimate(credits=_decimal(payload.get("credits")), usd=_decimal(payload.get("usd")))

    async def submit(self, endpoint: str, arguments: Mapping[str, Any], *,
                     idempotency_key: str | None = None, attempts: int = 4) -> Accepted:
        """Submit once; on ambiguous failures re-send with the same Idempotency-Key only."""
        key = idempotency_key or str(uuid.uuid4())
        delay = 2.0
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await self._api.post(
                    f"/{endpoint.strip('/')}", json=dict(arguments),
                    headers={"Idempotency-Key": key}, timeout=httpx.Timeout(120.0, connect=20.0),
                )
            except httpx.TransportError as error:
                last_error = error
            else:
                if response.is_success:
                    return self._accepted(response)
                error = error_from_response(response, "generation")
                if not error.retryable:
                    raise error
                last_error = error
            if attempt < attempts:
                await self._sleep(delay + random.uniform(0, 1))
                delay = min(delay * 2, 20.0)
        raise HiggsfieldError(
            f"Could not confirm the generation was accepted ({last_error}). Retries reused the same "
            f"idempotency key, so at most one job exists; check console.higgsfield.ai before running again. "
            f"Idempotency-Key: {key}"
        )

    def _accepted(self, response: httpx.Response) -> Accepted:
        try:
            payload = response.json()
            request_id = str(payload["request_id"])
            if not request_id.strip():
                raise ValueError("empty request_id")
        except (ValueError, KeyError, TypeError) as error:
            raise HiggsfieldError("Higgsfield returned an invalid submission response.") from error
        status_url = payload.get("status_url") or f"{self.base_url}/requests/{request_id}/status"
        cancel_url = payload.get("cancel_url") or f"{self.base_url}/requests/{request_id}/cancel"
        self._api_url(status_url)  # host check
        return Accepted(request_id=request_id, status_url=status_url, cancel_url=cancel_url, raw=payload)

    async def status(self, status_url: str) -> dict:
        payload = await self.get_json(status_url, "request status")
        if not isinstance(payload, dict) or "status" not in payload:
            raise HiggsfieldError("Higgsfield returned an invalid status response.")
        return payload

    async def wait(self, accepted: Accepted, *, timeout: float,
                   on_update: Callable[[dict, float], None] | None = None,
                   should_stop: Callable[[], bool] | None = None,
                   clock: Callable[[], float] | None = None) -> dict:
        """Poll until terminal. 2s start, x1.5 backoff, 10s cap, jitter (docs: polling)."""
        loop = asyncio.get_running_loop()
        now = clock or loop.time
        start = now()
        delay = 2.0
        while True:
            payload = await self.status(accepted.status_url)
            elapsed = now() - start
            if on_update:
                on_update(payload, elapsed)
            if payload.get("status") in TERMINAL:
                return payload
            if should_stop and should_stop():
                return {"status": "interrupted", "request_id": accepted.request_id}
            if elapsed > timeout:
                raise HiggsfieldError(
                    f"Timed out after {int(elapsed)}s waiting for request {accepted.request_id}. "
                    "It may still finish on Higgsfield; check console.higgsfield.ai."
                )
            # Sleep in short slices so a ComfyUI cancel is noticed quickly.
            remaining = delay + random.uniform(0, 0.5)
            while remaining > 0:
                if should_stop and should_stop():
                    return {"status": "interrupted", "request_id": accepted.request_id}
                await self._sleep(min(0.5, remaining))
                remaining -= 0.5
            delay = min(delay * 1.5, 10.0)

    async def cancel(self, accepted: Accepted) -> bool:
        if not accepted.cancel_url:
            return False
        try:
            response = await self._api.post(self._api_url(accepted.cancel_url))
        except httpx.TransportError:
            return False
        return response.status_code in (200, 202, 204)

    # ------------------------------------------------------------------ Soul ID (character references)
    # docs: models/soul-id/create-character — POST /v1/custom-references, poll GET /v1/custom-references/{id}

    async def create_custom_reference(self, name: str, model_version: str, image_urls: list[str]) -> dict:
        body = {"name": name, "model_version": model_version,
                "input_images": [{"type": "image_url", "image_url": url} for url in image_urls]}
        try:
            response = await self._api.post("/v1/custom-references", json=body,
                                            timeout=httpx.Timeout(120.0, connect=20.0))
        except httpx.TransportError as error:
            raise HiggsfieldError(f"Network error while starting Soul ID training: {error}. Check "
                                  "console.higgsfield.ai before retrying, in case it was created.") from error
        if not response.is_success:
            raise error_from_response(response, "Soul ID training")
        payload = _json(response, "Soul ID")
        if not isinstance(payload, dict) or not payload.get("id"):
            raise HiggsfieldError("Higgsfield returned an invalid Soul ID response.")
        return payload

    async def get_custom_reference(self, reference_id: str) -> dict:
        if not re.fullmatch(r"[0-9a-fA-F-]{8,64}", reference_id or ""):
            raise HiggsfieldError(f"'{reference_id}' is not a valid Soul ID.")
        payload = await self.get_json(f"/v1/custom-references/{reference_id}", "Soul ID status")
        if not isinstance(payload, dict):
            raise HiggsfieldError("Higgsfield returned an invalid Soul ID status.")
        return payload

    async def list_custom_references(self) -> list[dict]:
        payload = await self.get_json("/v1/custom-references/", "Soul ID list")
        if isinstance(payload, dict):
            for key in ("items", "results", "data", "custom_references"):
                if isinstance(payload.get(key), list):
                    return payload[key]
            return []
        return payload if isinstance(payload, list) else []

    async def upload(self, data: bytes, content_type: str) -> str:
        if content_type not in UPLOAD_CONTENT_TYPES:
            raise HiggsfieldError(f"Higgsfield does not accept uploads of type {content_type}.")
        if not data:
            raise HiggsfieldError("Cannot upload an empty file.")
        response = await self._safe_request("POST", "/files/generate-upload-url", "upload",
                                            json={"content_type": content_type})
        payload = _json(response, "upload")
        try:
            public_url = _https(str(payload["public_url"]), "public media URL")
            upload_url = _https(str(payload["upload_url"]), "upload URL")
        except (KeyError, TypeError) as error:
            raise HiggsfieldError("Higgsfield returned an invalid upload response.") from error
        headers = {k: str(v) for k, v in (payload.get("upload_headers") or {"Content-Type": content_type}).items()
                   if k.lower() != "authorization"}
        delay = 1.0
        for attempt in range(1, 4):  # a PUT of the same bytes to the same presigned URL is safe to repeat
            try:
                put = await self._public.put(upload_url, content=data, headers=headers)
            except httpx.TransportError as error:
                if attempt == 3:
                    raise HiggsfieldError(f"Upload to Higgsfield storage failed: {error}") from error
            else:
                if put.is_success:
                    return public_url
                if put.status_code < 500 or attempt == 3:
                    raise HiggsfieldError(f"Upload to Higgsfield storage failed (HTTP {put.status_code}).")
            await self._sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")

    async def download(self, url: str, destination: Path, *, max_bytes: int = MAX_DOWNLOAD_BYTES,
                       attempts: int = 3) -> Path:
        _https(url, "result URL")
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(destination.name + ".part")
        delay = 2.0
        for attempt in range(1, attempts + 1):
            total = 0
            try:
                async with self._public.stream("GET", url) as response:
                    if not response.is_success:
                        retryable = response.status_code >= 500 or response.status_code in (408, 429)
                        if not retryable or attempt == attempts:
                            raise HiggsfieldError(f"Downloading the result failed (HTTP {response.status_code}).")
                        continue_after = True
                    else:
                        continue_after = False
                        with partial.open("wb") as handle:
                            async for chunk in response.aiter_bytes(1 << 20):
                                total += len(chunk)
                                if total > max_bytes:
                                    raise HiggsfieldError("The result file is larger than the 4 GB safety limit.")
                                handle.write(chunk)
                if not continue_after:
                    partial.replace(destination)
                    return destination
            except httpx.TransportError as error:
                if attempt == attempts:
                    raise HiggsfieldError(f"Network error while downloading the result: {error}") from error
            finally:
                partial.unlink(missing_ok=True)
            await self._sleep(delay)
            delay *= 2
        raise AssertionError("unreachable")
