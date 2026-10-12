import asyncio
import unittest

import httpx

from higgsfield_nodes.client import HiggsfieldClient
from higgsfield_nodes.credentials import Credentials
from higgsfield_nodes.pickers import fetch_options, picker_for

import importlib.util

HAVE_COMFY = importlib.util.find_spec("comfy_api") is not None


async def no_sleep(_):
    return None


class PickerTests(unittest.TestCase):
    def test_picker_for(self):
        self.assertEqual(picker_for("higgsfield-ai/soul/v2/standard", "style_id"), "soul-styles-v2")
        self.assertEqual(picker_for("higgsfield-ai/soul/standard", "style_id"), "soul-styles")
        self.assertEqual(picker_for("marketing-studio/image/flare", "preset_id"), "marketing-presets")
        self.assertEqual(picker_for("higgsfield/genjutsu/restyle/v1.0", "preset_id"), "restyle-presets")
        self.assertEqual(picker_for("higgsfield-ai/soul/cinema", "custom_reference_id"), "soul-ids")
        self.assertIsNone(picker_for("bytedance/seedance-2.0/text-to-video", "prompt"))

    @unittest.skipUnless(HAVE_COMFY, "ComfyUI not on PYTHONPATH")
    def test_picker_map_widget_names(self):
        from higgsfield_nodes.pickers import picker_map

        mapping = picker_map()
        genjutsu = mapping["Higgsfield_genjutsu"]
        self.assertTrue(genjutsu["multi"])
        self.assertEqual(genjutsu["modes"]["Restyle"]["mode.preset_id"]["kind"], "restyle-presets")
        soul2 = mapping["Higgsfield_soul_2"]["modes"]
        self.assertTrue(all(fields.get("mode.style_id", {}).get("kind") == "soul-styles-v2" for fields in soul2.values()))
        cinema = mapping["Higgsfield_soul_cinema"]
        self.assertFalse(cinema["multi"])
        self.assertIn("custom_reference_id", next(iter(cinema["modes"].values())))
        self.assertIn("Higgsfield_gpt_image_2_5", mapping)

    def test_fetch_options_paging_and_normalizing(self):
        def handler(request):
            path, query = request.url.path, dict(request.url.params)
            if path == "/marketing-studio/image/presets":
                if query["cursor"] == "0":
                    return httpx.Response(200, json={"total": 3, "cursor": 2, "items": [
                        {"id": "p1", "name": "Editorial", "metadata": {"group_name": "Ads"}},
                        {"id": "p2", "name": "Studio", "cover_image": "https://cdn/x.jpg"}]})
                return httpx.Response(200, json={"total": 3, "cursor": None, "items": [{"id": "p3", "name": "Last"}]})
            if path == "/models/higgsfield/genjutsu/restyle/v1.0/presets":
                return httpx.Response(200, json={"model": "x", "items": [
                    {"id": "r1", "name": "Cel-Shaded CG Anime", "preview_url": "https://cdn/r1.webp"}]})
            if path == "/v1/custom-references/":
                return httpx.Response(200, json=[{"id": "s1", "name": "Me", "status": "completed", "model_version": "v2"},
                                                 {"id": "s2", "name": "Draft", "status": "in_progress"}])
            return httpx.Response(404)

        async def flow():
            creds = Credentials("kid", "sec", "t")
            async with HiggsfieldClient(creds, transport=httpx.MockTransport(handler), sleep=no_sleep) as client:
                return (await fetch_options(client, "marketing-presets"),
                        await fetch_options(client, "restyle-presets", "anime"),
                        await fetch_options(client, "soul-ids"))

        marketing, restyle, souls = asyncio.run(flow())
        self.assertEqual([i["id"] for i in marketing], ["p1", "p2", "p3"])
        self.assertEqual(marketing[0]["group"], "Ads")
        self.assertEqual(marketing[1]["preview"], "https://cdn/x.jpg")
        self.assertEqual([i["name"] for i in restyle], ["Cel-Shaded CG Anime"])
        self.assertEqual([(i["id"], i["group"]) for i in souls], [("s1", "v2")])


if __name__ == "__main__":
    unittest.main()
