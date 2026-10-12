"""Build every node against the real comfy_api (needs ComfyUI on PYTHONPATH; skipped otherwise)."""

import unittest

import importlib.util

HAVE_COMFY = importlib.util.find_spec("comfy_api") is not None


@unittest.skipUnless(HAVE_COMFY, "ComfyUI not on PYTHONPATH")
class NodeBuildTests(unittest.TestCase):
    def test_all_nodes_define_valid_schemas(self):
        from higgsfield_nodes.catalog import load_families
        from higgsfield_nodes.nodes import build_all_nodes

        nodes = build_all_nodes()
        self.assertEqual(len(nodes), len(load_families()))
        ids = set()
        for node in nodes:
            schema = node.define_schema()
            schema.validate()
            schema.finalize()
            info = schema.get_v1_info(node)
            self.assertTrue(info.name.startswith("Higgsfield_"))
            self.assertNotIn(info.name, ids)
            ids.add(info.name)
            self.assertFalse(info.api_node)

    def test_gpt_image_soul_cinema_and_soul_id_nodes(self):
        from higgsfield_nodes.nodes import build_all_nodes
        from higgsfield_nodes.soul_id import SOUL_ID_NODES

        nodes = {n.define_schema().node_id: n for n in build_all_nodes() + SOUL_ID_NODES}
        gpt = nodes["Higgsfield_gpt_image_2_5"].define_schema()
        self.assertEqual(gpt.display_name, "Higgsfield GPT Image 2.5")
        self.assertIn("openai", gpt.search_aliases)
        mode = next(i for i in gpt.inputs if i.id == "mode")
        self.assertEqual([o.key for o in mode.options], ["GPT Image 2.5 Flare", "GPT Image 2.5 Sunburst"])
        marketing = nodes["Higgsfield_marketing_studio_image"].define_schema()
        keys = [o.key for o in next(i for i in marketing.inputs if i.id == "mode").options]
        self.assertIn("GPT Image 2.5 Flare", keys)
        self.assertIn("2.0 Alpha", keys)
        self.assertIn("Higgsfield_soul_cinema", nodes)
        for node_id in ("Higgsfield_soul_id_train", "Higgsfield_soul_id_list"):
            schema = nodes[node_id].define_schema()
            schema.validate()
            schema.finalize()
            schema.get_v1_info(nodes[node_id])

    def test_build_arguments_for_seedance_text_to_video(self):
        from higgsfield_nodes.catalog import load_families
        from higgsfield_nodes.nodes import _build_arguments
        from higgsfield_nodes.params import mode_params

        family = next(f for f in load_families() if f.slug == "bytedance-seedance-2-0")
        mode = family.modes[0]
        params = mode_params(mode.schema, mode.ui_schema, {"prompt"})
        values = {"mode": mode.key, "duration": 8, "resolution": "1080p", "aspect_ratio": "9:16", "generate_audio": False}
        args, uploads = _build_arguments(family, mode, params, values, {"prompt": "a fox"}, True, None)
        self.assertEqual(args, {"prompt": "a fox", "duration": 8, "resolution": "1080p",
                                "aspect_ratio": "9:16", "generate_audio": False})
        self.assertEqual(uploads, [])

    def test_missing_required_image_is_reported(self):
        from higgsfield_nodes.catalog import load_families
        from higgsfield_nodes.client import HiggsfieldError
        from higgsfield_nodes.nodes import _build_arguments
        from higgsfield_nodes.params import mode_params

        family = next(f for f in load_families() if f.slug == "bytedance-seedance-2-0")
        mode = next(m for m in family.modes if m.key == "Image to Video")
        params = mode_params(mode.schema, mode.ui_schema, {"prompt"})
        with self.assertRaisesRegex(HiggsfieldError, "Connect an input to 'image'"):
            _build_arguments(family, mode, params, {"mode": mode.key}, {"prompt": ""}, True, None)


    def _nested(self, node, flat: dict) -> dict:
        """Reproduce how ComfyUI's executor turns a prompt's flat inputs into execute() kwargs."""
        from comfy_api.latest import _io

        valid = node.INPUT_TYPES()
        _, _, v3_data = _io.get_finalized_class_inputs(valid, flat)
        return _io.build_nested_inputs(flat, v3_data)

    def _node(self, slug):
        from higgsfield_nodes.catalog import load_families
        from higgsfield_nodes.nodes import make_node

        family = next(f for f in load_families() if f.slug == slug)
        return family, make_node(family)

    def test_kling_mode_parameter_does_not_clash_with_mode_selector(self):
        from higgsfield_nodes.nodes import _build_arguments, _assign_input_ids
        from higgsfield_nodes.params import mode_params

        family, node = self._node("kling-o3")
        mode = next(m for m in family.modes if m.key == "Video Edit")
        kwargs = self._nested(node, {
            "prompt": "swap the jacket", "mode": mode.key, "mode.quality_mode": "pro",
            "mode.elements": "", "variation": 0, "run": "generate", "max_usd": 0.0,
            "save_output": True, "timeout_minutes": 30,
        })
        selected = kwargs["mode"]
        self.assertEqual(selected["mode"], "Video Edit")
        self.assertEqual(selected["quality_mode"], "pro")
        params = _assign_input_ids(mode_params(mode.schema, mode.ui_schema, {"prompt"}), {"mode"})
        selected["video_urls"] = {"video_1": object()}  # Video Edit requires a source video
        args, uploads = _build_arguments(family, mode, params, selected, kwargs, True, None)
        self.assertEqual(args["mode"], "pro")
        self.assertEqual([p.name for p, _ in uploads], ["video_urls"])

    def _defaults(self, slug, mode_key):
        from higgsfield_nodes.nodes import _assign_input_ids
        from higgsfield_nodes.params import mode_params

        family, _ = self._node(slug)
        mode = next(m for m in family.modes if m.key == mode_key)
        params = _assign_input_ids(mode_params(mode.schema, mode.ui_schema, {"prompt", "seed"}), {"mode"})
        values = {p.input_id: p.default for p in params if p.kind not in ("image", "video", "audio", "media_list")}
        return family, mode, params, values

    def test_optional_reference_list_with_min_items_can_be_empty(self):
        from higgsfield_nodes.nodes import _build_arguments

        # Seedance 2.5 Video Edit: audio_urls is optional but has minItems 1 when sent.
        family, mode, params, values = self._defaults("bytedance-seedance-2-5", "Video Edit")
        values["video_url"] = object()
        args, uploads = _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)
        self.assertEqual([p.name for p, _ in uploads], ["video_url"])
        self.assertNotIn("audio_urls", args)

    def test_conditional_schema_rules_are_enforced_before_upload(self):
        from higgsfield_nodes.client import HiggsfieldError
        from higgsfield_nodes.nodes import _build_arguments

        family, mode, params, values = self._defaults("bytedance-seedance-2-0",
                                                      "Reference to Video (with video reference)")
        with self.assertRaisesRegex(HiggsfieldError, "connect at least one of: 'reference audios', "
                                                     "'reference images', 'reference videos'"):
            _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)
        values["image_urls"] = {"image_1": object()}
        _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)

        family, mode, params, values = self._defaults("kling-o3", "First/Last Frame")
        with self.assertRaisesRegex(HiggsfieldError, "connect 'first frame'"):
            _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)

        family, mode, params, values = self._defaults("qwen-image-3", "Text to Image")
        values.update(enable_thinking=True, prompt_extend=False)
        with self.assertRaisesRegex(HiggsfieldError, "'prompt extend' must be True"):
            _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)

        family, mode, params, values = self._defaults("marketing-studio-image", "GPT Image 2.5 Flare")
        values["enhance_prompt"] = True
        with self.assertRaisesRegex(HiggsfieldError, "set 'preset ID'"):
            _build_arguments(family, mode, params, values, {"prompt": "x"}, True, None)

    def test_ideogram_image_weight_defaults_to_not_sent(self):
        from higgsfield_nodes.nodes import _build_arguments, _assign_input_ids, NODE_INPUT_IDS
        from higgsfield_nodes.params import mode_params

        family, node = self._node("ideogram/v4.0")
        mode = family.modes[0]
        params = _assign_input_ids(mode_params(mode.schema, mode.ui_schema, {"prompt"}), NODE_INPUT_IDS)
        weight = next(p for p in params if p.name == "image_weight")
        self.assertEqual((weight.unset, weight.default, weight.minimum), (0, 0, 0))
        values = {p.input_id: p.default for p in params if p.kind not in ("image", "video", "audio", "media_list")}
        args, _ = _build_arguments(family, mode, params, values, {"prompt": "logo"}, True, None)
        self.assertNotIn("image_weight", args)
        values["image_weight"] = 40
        args, _ = _build_arguments(family, mode, params, values, {"prompt": "logo"}, True, None)
        self.assertEqual(args["image_weight"], 40)

    def test_seed_and_variation_default_to_fixed(self):
        _, node = self._node("bytedance-seedance-2-0")
        info = node.define_schema().get_v1_info(node)
        self.assertEqual(info.input["required"]["variation"][1]["control_after_generate"], "fixed")
        _, node = self._node("happy-horse-1-0")
        info = node.define_schema().get_v1_info(node)
        self.assertEqual(info.input["required"]["seed"][1]["control_after_generate"], "fixed")


if __name__ == "__main__":
    unittest.main()
