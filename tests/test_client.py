import asyncio
import json
import tempfile
import unittest
from pathlib import Path

import httpx

from higgsfield_nodes.client import HiggsfieldClient, HiggsfieldError
from higgsfield_nodes.credentials import Credentials

CREDS = Credentials("kid123456", "secret999", "test")


async def no_sleep(_):
    return None


class FakeHiggsfield:
    """Minimal in-memory stand-in for api.higgsfield.ai + storage + CDN."""

    def __init__(self):
        self.calls = []
        self.submit_failures = 0
        self.statuses = ["queued", "in_progress", "completed"]
        self.idempotency_keys = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.calls.append((request.method, url, dict(request.headers)))
        host = request.url.host
        if host == "api.higgsfield.ai":
            assert request.headers["Authorization"] == "Key kid123456:secret999"
            path = request.url.path
            if path.startswith("/estimate/"):
                return httpx.Response(200, json={"credits": "1.500", "usd": "0.094"})
            if path == "/files/generate-upload-url":
                body = json.loads(request.content)
                return httpx.Response(200, json={
                    "public_url": "https://cdn.example.com/in.png",
                    "upload_url": "https://storage.example.com/put?sig=1",
                    "upload_headers": {"Content-Type": body["content_type"], "x-amz-tagging": "retention=temporary"},
                })
            if path.endswith("/status"):
                status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
                payload = {"status": status, "request_id": "r1"}
                if status == "completed":
                    payload["video"] = {"url": "https://cdn.example.com/out.mp4"}
                if status == "failed":
                    payload["error"] = "boom"
                return httpx.Response(200, json=payload)
            if path.endswith("/cancel"):
                return httpx.Response(202)
            if request.method == "POST":  # model submission
                self.idempotency_keys.append(request.headers.get("Idempotency-Key"))
                if self.submit_failures:
                    self.submit_failures -= 1
                    return httpx.Response(502, json={"detail": "bad gateway"})
                return httpx.Response(200, json={
                    "status": "queued", "request_id": "r1",
                    "status_url": "https://api.higgsfield.ai/requests/r1/status",
                    "cancel_url": "https://api.higgsfield.ai/requests/r1/cancel"})
        if host == "storage.example.com":
            assert "Authorization" not in request.headers
            return httpx.Response(200)
        if host == "cdn.example.com":
            assert "Authorization" not in request.headers
            return httpx.Response(200, content=b"video-bytes")
        return httpx.Response(404)


def client_for(fake):
    return HiggsfieldClient(CREDS, transport=httpx.MockTransport(fake), sleep=no_sleep)


class ClientTests(unittest.TestCase):
    def run_async(self, coro):
        return asyncio.run(coro)

    def test_full_flow(self):
        fake = FakeHiggsfield()

        async def flow():
            async with client_for(fake) as client:
                estimate = await client.estimate("bytedance/seedance-2.0/text-to-video", {"prompt": "x"})
                url = await client.upload(b"png", "image/png")
                accepted = await client.submit("bytedance/seedance-2.0/text-to-video", {"prompt": "x"})
                updates = []
                result = await client.wait(accepted, timeout=60, on_update=lambda p, t: updates.append(p["status"]))
                with tempfile.TemporaryDirectory() as tmp:
                    path = await client.download(result["video"]["url"], Path(tmp) / "out.mp4")
                    data = path.read_bytes()
                return estimate, url, accepted, updates, data

        estimate, url, accepted, updates, data = self.run_async(flow())
        self.assertEqual(str(estimate.usd), "0.094")
        self.assertEqual(estimate.describe(), "$0.094 / 1.5 credits")
        self.assertEqual(url, "https://cdn.example.com/in.png")
        self.assertEqual(accepted.request_id, "r1")
        self.assertEqual(updates, ["queued", "in_progress", "completed"])
        self.assertEqual(data, b"video-bytes")
        put = [c for c in fake.calls if c[0] == "PUT"][0]
        self.assertEqual(put[2]["x-amz-tagging"], "retention=temporary")

    def test_submit_retries_reuse_the_same_idempotency_key(self):
        fake = FakeHiggsfield()
        fake.submit_failures = 2

        async def flow():
            async with client_for(fake) as client:
                return await client.submit("m/x", {"prompt": "x"}, idempotency_key="key-1")

        self.assertEqual(self.run_async(flow()).request_id, "r1")
        self.assertEqual(fake.idempotency_keys, ["key-1", "key-1", "key-1"])

    def test_errors_are_friendly(self):
        def handler(request):
            if request.url.path.startswith("/estimate"):
                return httpx.Response(403, json={"detail": "Insufficient credits"})
            return httpx.Response(400, json={"detail": "Maximum number of concurrent requests (4) has been reached"})

        async def flow(call):
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return await call(client)

        with self.assertRaisesRegex(HiggsfieldError, "Not enough Higgsfield credits"):
            self.run_async(flow(lambda c: c.estimate("m/x", {})))
        with self.assertRaisesRegex(HiggsfieldError, "concurrency limit"):
            self.run_async(flow(lambda c: c.submit("m/x", {})))

    def test_refuses_foreign_status_host(self):
        def handler(request):
            return httpx.Response(200, json={"request_id": "r1", "status_url": "https://evil.example.com/s"})

        async def flow():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return await client.submit("m/x", {})

        with self.assertRaisesRegex(HiggsfieldError, "unexpected host"):
            self.run_async(flow())

    def test_wait_stops_quickly_on_interrupt(self):
        fake = FakeHiggsfield()
        fake.statuses = ["in_progress"]
        sleeps = []

        async def record_sleep(seconds):
            sleeps.append(seconds)

        polls = {"n": 0}

        def should_stop():
            polls["n"] += 1
            return polls["n"] > 3

        async def flow():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(fake), sleep=record_sleep) as client:
                accepted = await client.submit("m/x", {})
                return await client.wait(accepted, timeout=600, should_stop=should_stop)

        self.assertEqual(self.run_async(flow())["status"], "interrupted")
        self.assertTrue(all(s <= 0.5 for s in sleeps))

    def test_transient_errors_are_retried_for_safe_calls(self):
        calls = {"estimate": 0, "download": 0}

        def handler(request):
            if request.url.path.startswith("/estimate"):
                calls["estimate"] += 1
                if calls["estimate"] < 3:
                    return httpx.Response(502, text="<html>bad gateway</html>")
                return httpx.Response(200, json={"usd": "0.10"})
            if request.url.host == "cdn.example.com":
                calls["download"] += 1
                if calls["download"] == 1:
                    return httpx.Response(503)
                return httpx.Response(200, content=b"ok")
            return httpx.Response(404)

        async def flow():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                estimate = await client.estimate("m/x", {})
                with tempfile.TemporaryDirectory() as tmp:
                    data = (await client.download("https://cdn.example.com/o.mp4", Path(tmp) / "o.mp4")).read_bytes()
                return estimate, data

        estimate, data = self.run_async(flow())
        self.assertEqual(str(estimate.usd), "0.10")
        self.assertEqual(data, b"ok")
        self.assertEqual(calls, {"estimate": 3, "download": 2})

    def test_unreadable_and_redirect_responses_are_clear_errors(self):
        def handler(request):
            if request.url.path.endswith("/status"):
                return httpx.Response(200, text="<html>maintenance</html>")
            return httpx.Response(302, headers={"Location": "https://elsewhere.example.com"})

        async def status():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return await client.status("https://api.higgsfield.ai/requests/r1/status")

        async def estimate():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return await client.estimate("m/x", {})

        with self.assertRaisesRegex(HiggsfieldError, "unreadable request status"):
            self.run_async(status())
        with self.assertRaisesRegex(HiggsfieldError, "HTTP 302"):
            self.run_async(estimate())

    def test_validation_detail_list(self):
        def handler(request):
            return httpx.Response(422, json={"detail": [{"loc": ["body", "duration"], "msg": "must be <= 15"}]})

        async def flow():
            async with HiggsfieldClient(CREDS, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return await client.submit("m/x", {})

        with self.assertRaisesRegex(HiggsfieldError, "duration: must be <= 15"):
            self.run_async(flow())


if __name__ == "__main__":
    unittest.main()
