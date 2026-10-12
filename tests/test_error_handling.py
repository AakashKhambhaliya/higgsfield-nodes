"""End-to-end error handling of a node run, with a fake Higgsfield client (no network)."""

import asyncio
import importlib.util
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from higgsfield_nodes.client import Accepted, Estimate, HiggsfieldError
from higgsfield_nodes.credentials import Credentials

HAVE_COMFY = importlib.util.find_spec("comfy_api") is not None


class FakeClient:
    """Configurable stand-in for HiggsfieldClient."""

    behaviour: dict = {}

    def __init__(self, creds):
        self.b = FakeClient.behaviour

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def upload(self, data, content_type):
        if self.b.get("upload_error"):
            raise HiggsfieldError("storage unavailable")
        return "https://cdn.example.com/in.png"

    async def estimate(self, endpoint, arguments):
        return Estimate(credits=Decimal("1"), usd=Decimal("0.10"))

    async def submit(self, endpoint, arguments, idempotency_key=None):
        if self.b.get("submit_bug"):
            raise KeyError("request_id")
        return Accepted("req-12345678", "https://api.higgsfield.ai/requests/req-12345678/status",
                        "https://api.higgsfield.ai/requests/req-12345678/cancel")

    async def wait(self, accepted, **kwargs):
        return self.b.get("result", {"status": "completed", "video": {"url": "https://cdn.example.com/out.mp4"}})

    async def cancel(self, accepted):
        return True

    async def download(self, url, destination):
        if self.b.get("download_error"):
            raise HiggsfieldError("Downloading the result failed (HTTP 403).")
        if self.b.get("disk_full"):
            raise OSError(28, "No space left on device")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"x")
        return destination


@unittest.skipUnless(HAVE_COMFY, "ComfyUI not on PYTHONPATH")
class RunErrorTests(unittest.TestCase):
    def setUp(self):
        from higgsfield_nodes import nodes
        from higgsfield_nodes.catalog import load_families

        self.nodes = nodes
        self.family = next(f for f in load_families() if f.slug == "bytedance-seedance-2-0")
        self.mode = self.family.modes[0]  # Text to Video
        self.params = nodes.mode_params(self.mode.schema, self.mode.ui_schema, {"prompt"})
        self.values = {p.input_id: p.default for p in self.params}
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patches = [
            mock.patch.object(nodes, "HiggsfieldClient", FakeClient),
            mock.patch.object(nodes.credentials, "require", return_value=Credentials("k", "s", "t")),
            mock.patch.object(nodes, "_output_dir", return_value=(Path(self.tmp.name), "output")),
            mock.patch.object(nodes, "_progress"),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def run_node(self, top=None, **behaviour):
        FakeClient.behaviour = behaviour
        top = {"prompt": "a fox", "run": "generate", "max_usd": 0.0, "timeout_minutes": 30, **(top or {})}
        return asyncio.run(self.nodes.run_node(object, self.family, self.mode, self.params, self.values, top,
                                               hoist_prompt=True, seed_param=None))

    def test_download_failure_after_payment_keeps_result_url(self):
        with self.assertRaises(HiggsfieldError) as caught:
            self.run_node(download_error=True)
        message = str(caught.exception)
        self.assertIn("saving the result failed", message)
        self.assertIn("charged (request req-12345678)", message)
        self.assertIn("https://cdn.example.com/out.mp4", message)

    def test_disk_full_is_explained(self):
        with self.assertRaisesRegex(HiggsfieldError, "No space left on device"):
            self.run_node(disk_full=True)

    def test_unexpected_bug_before_acceptance_says_nothing_charged(self):
        with self.assertRaises(HiggsfieldError) as caught:
            self.run_node(submit_bug=True)
        message = str(caught.exception)
        self.assertIn("submitting the generation failed", message)
        self.assertIn("KeyError", message)
        self.assertIn("Nothing was submitted, so nothing was charged", message)

    def test_upload_failure(self):
        from higgsfield_nodes.params import mode_params

        mode = next(m for m in self.family.modes if m.key == "Image to Video")
        params = mode_params(mode.schema, mode.ui_schema, {"prompt"})
        values = {p.input_id: p.default for p in params} | {"image_url": object()}
        FakeClient.behaviour = {}
        with self.assertRaisesRegex(HiggsfieldError, "Could not convert the connected image"):
            asyncio.run(self.nodes.run_node(object, self.family, mode, params, values,
                                            {"prompt": "x", "run": "generate"}, hoist_prompt=True, seed_param=None))

    def test_failed_and_moderated_results(self):
        with self.assertRaisesRegex(HiggsfieldError, "not charged"):
            self.run_node(result={"status": "failed", "error": "bad input"})
        with self.assertRaisesRegex(HiggsfieldError, "moderation"):
            self.run_node(result={"status": "nsfw"})
        with self.assertRaisesRegex(HiggsfieldError, "returned no video"):
            self.run_node(result={"status": "completed"})
        with self.assertRaisesRegex(HiggsfieldError, "unexpected status 'weird'"):
            self.run_node(result={"status": "weird"})

    def test_missing_key_message(self):
        from higgsfield_nodes import credentials

        with mock.patch.object(self.nodes.credentials, "require",
                               side_effect=credentials.CredentialError("No Higgsfield API key is set.")):
            with self.assertRaisesRegex(HiggsfieldError, "No Higgsfield API key is set"):
                self.run_node()


class CatalogFallbackTests(unittest.TestCase):
    def test_corrupt_catalog_uses_backup(self):
        from higgsfield_nodes.catalog import read_catalog_file

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "models.json"
            path.write_text("{ not json", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                read_catalog_file(path)
            Path(tmp, "models.json.bak").write_text('{"families": [], "endpoints": {}}', encoding="utf-8")
            self.assertEqual(read_catalog_file(path), {"families": [], "endpoints": {}})


if __name__ == "__main__":
    unittest.main()
