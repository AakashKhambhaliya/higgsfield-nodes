import asyncio
import importlib.util
import unittest
from decimal import Decimal
from unittest import mock

from higgsfield_nodes import pricing
from higgsfield_nodes.client import Estimate, HiggsfieldError
from higgsfield_nodes.credentials import Credentials

HAVE_COMFY = importlib.util.find_spec("comfy_api") is not None


class PublishedPriceTests(unittest.TestCase):
    def test_range_and_exact_scaling(self):
        seedance = pricing.published("bytedance/seedance-2.0/text-to-video")
        self.assertEqual(seedance["kind"], "published")
        self.assertRegex(seedance["text"], r"^\$0\.\d+–\$1\.\d+/s$")
        kling = pricing.published("kling-video/v3.0/std/text-to-video", duration=5)
        self.assertRegex(kling["text"], r"^≈\$0\.\d+$")  # exact per-second price × 5 s

    def test_unknown_endpoint(self):
        self.assertIsNone(pricing.published("nope/never"))

    def test_money_format(self):
        self.assertEqual(pricing._money(0.042), "$0.042")
        self.assertEqual(pricing._money(1.0887), "$1.09")
        self.assertEqual(pricing._money(0.0032), "$0.0032")


class FakeClient:
    calls = []

    def __init__(self, creds):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def estimate(self, endpoint, arguments):
        FakeClient.calls.append((endpoint, arguments))
        return Estimate(credits=Decimal("6.7"), usd=Decimal("0.42"))


@unittest.skipUnless(HAVE_COMFY, "ComfyUI not on PYTHONPATH")
class LivePriceTests(unittest.TestCase):
    def setUp(self):
        from higgsfield_nodes.nodes import build_all_nodes

        build_all_nodes()  # registers NODE_SPECS
        pricing._cache.clear()
        FakeClient.calls = []
        self.patches = [mock.patch.object(pricing.credentials, "resolve", return_value=Credentials("k", "s", "t")),
                        mock.patch.object(pricing, "HiggsfieldClient", FakeClient)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def price(self, node, values, linked=()):
        return asyncio.run(pricing.price_for(node, values, list(linked)))

    def test_live_estimate_uses_exact_request_and_caches(self):
        values = {"mode": "Text to Video", "prompt": "", "duration": 8, "resolution": "1080p",
                  "aspect_ratio": "9:16", "generate_audio": True}
        first = self.price("Higgsfield_bytedance_seedance_2_0", values)
        self.assertEqual((first["kind"], first["text"]), ("live", "$0.42"))
        endpoint, arguments = FakeClient.calls[0]
        self.assertEqual(endpoint, "bytedance/seedance-2.0/text-to-video")
        self.assertEqual(arguments["duration"], 8)
        self.assertEqual(arguments["resolution"], "1080p")
        self.assertEqual(arguments["prompt"], "price check")  # empty prompt -> stand-in
        self.price("Higgsfield_bytedance_seedance_2_0", values)
        self.assertEqual(len(FakeClient.calls), 1)  # cached

    def test_connected_video_falls_back_to_published(self):
        values = {"mode": "Video Edit", "prompt": "x", "resolution": "720p", "generate_audio": True,
                  "bitrate_mode": "standard"}
        result = self.price("Higgsfield_bytedance_seedance_2_5", values, ["video_url"])
        self.assertEqual(result["kind"], "published")
        self.assertIn("length of the connected video", result["detail"])
        self.assertEqual(FakeClient.calls, [])

    def test_missing_required_input_falls_back(self):
        result = self.price("Higgsfield_bytedance_seedance_2_0", {"mode": "Image to Video", "prompt": "x"})
        self.assertNotEqual(result["kind"], "live")
        self.assertIn("Not ready", result["detail"])

    def test_estimate_error_falls_back_quietly(self):
        async def broken(self, endpoint, arguments):
            raise HiggsfieldError("boom", status_code=500)

        with mock.patch.object(FakeClient, "estimate", broken):
            result = self.price("Higgsfield_soul_2", {"mode": "Text to Image", "prompt": "x"})
        self.assertEqual(result["kind"], "published")

    def test_unknown_node(self):
        self.assertEqual(self.price("Nope", {})["kind"], "none")


if __name__ == "__main__":
    unittest.main()
