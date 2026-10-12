import unittest

from higgsfield_nodes.catalog import load_families
from higgsfield_nodes.params import AUTO, ParamError, classify, mode_params, parse_multi_prompt, to_argument


class ClassifyTests(unittest.TestCase):
    def test_enum_with_default(self):
        p = classify("resolution", {"enum": ["480p", "720p"], "type": "string", "default": "720p"}, False)
        self.assertEqual((p.kind, p.options, p.default), ("enum", ["480p", "720p"], "720p"))
        self.assertEqual(to_argument(p, "480p"), (True, "480p"))

    def test_optional_enum_without_default_gets_auto(self):
        p = classify("era", {"enum": ["1960s", "1980s"], "type": "string"}, False)
        self.assertEqual(p.options[0], AUTO)
        self.assertEqual(to_argument(p, AUTO), (False, None))
        self.assertEqual(to_argument(p, "1980s"), (True, "1980s"))

    def test_numeric_enum_round_trips_type(self):
        p = classify("duration", {"enum": [5, 10], "type": "integer", "default": 5}, False)
        self.assertEqual(p.options, ["5", "10"])
        self.assertEqual(to_argument(p, "10"), (True, 10))

    def test_int_and_float(self):
        p = classify("duration", {"type": "integer", "default": 5, "minimum": 4, "maximum": 15}, False)
        self.assertEqual((p.kind, p.minimum, p.maximum, p.default), ("int", 4, 15, 5))
        f = classify("cfg_scale", {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.01}, False,
                     {"ui:widget": "range", "ui:options": {"advanced": True}})
        self.assertEqual((f.kind, f.step, f.slider, f.advanced), ("float", 0.01, True, True))

    def test_media_detection(self):
        self.assertEqual(classify("image_url", {"type": "string", "format": "uri"}, True).kind, "image")
        self.assertEqual(classify("last_frame_url", {"type": "string", "format": "uri"}, False).kind, "image")
        self.assertEqual(classify("video_url", {"type": "string", "format": "uri"}, True).kind, "video")
        self.assertEqual(classify("audio_url", {"type": "string", "format": "uri"}, False).kind, "audio")
        self.assertEqual(classify("link_url", {"type": "string", "format": "uri"}, False).kind, "url")
        refs = classify("image_urls", {"type": "array", "items": {"type": "string", "format": "uri"}, "maxItems": 9}, False)
        self.assertEqual((refs.kind, refs.media, refs.max_items), ("media_list", "image", 9))
        nullable = classify("image_url", {"type": ["string", "null"], "format": "uri"}, False)
        self.assertEqual(nullable.kind, "image")

    def test_optional_text_omitted_when_empty_required_raises(self):
        p = classify("negative_prompt", {"type": "string"}, False)
        self.assertEqual(to_argument(p, "  "), (False, None))
        r = classify("preset_id", {"type": "string", "format": "uuid"}, True)
        with self.assertRaises(ParamError):
            to_argument(r, "")

    def test_colors(self):
        bg = classify("background_color", {"type": "object", "properties": {"rgb": {"type": "array"}}}, False)
        self.assertEqual(to_argument(bg, "#ff8800"), (True, {"rgb": [255, 136, 0]}))
        colors = classify("colors", {"type": "array", "items": {"type": "object"}}, False)
        self.assertEqual(to_argument(colors, "#000000, (255, 255, 255)"),
                         (True, [{"rgb": [0, 0, 0]}, {"rgb": [255, 255, 255]}]))
        with self.assertRaises(ParamError):
            to_argument(colors, "#000000, blue")
        with self.assertRaises(ParamError):
            to_argument(bg, "orange")

    def test_multi_prompt(self):
        spec = {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
            "duration": {"type": "integer", "minimum": 1, "maximum": 15}, "prompt": {"type": "string"}}}}
        p = classify("multi_prompt", spec, False)
        self.assertEqual(parse_multi_prompt("3: wide shot\n\n5s | close up", p),
                         [{"prompt": "wide shot", "duration": 3}, {"prompt": "close up", "duration": 5}])
        with self.assertRaises(ParamError):
            parse_multi_prompt("no duration here", p)
        with self.assertRaises(ParamError):
            parse_multi_prompt("20: too long", p)

    def test_float_noise_is_snapped_to_multiple_of(self):
        p = classify("cfg_scale", {"type": "number", "minimum": 0, "maximum": 1, "multipleOf": 0.01, "default": 0.5}, False)
        self.assertEqual(to_argument(p, 0.30000000000000004), (True, 0.3))
        self.assertEqual(to_argument(p, 1.0000001), (True, 1.0))

    def test_exclusive_bounds_stay_inside(self):
        p = classify("strength", {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1, "default": 0.5}, False)
        self.assertGreater(p.minimum, 0)
        self.assertLess(p.maximum, 1)

    def test_ids_are_trimmed_prompts_are_not(self):
        sid = classify("style_id", {"type": "string", "format": "uuid"}, False)
        self.assertEqual(to_argument(sid, "  abc-123\n"), (True, "abc-123"))
        neg = classify("negative_prompt", {"type": "string"}, False)
        self.assertEqual(to_argument(neg, " blurry, low quality "), (True, " blurry, low quality "))

    def test_csv_and_json(self):
        p = classify("elements", {"type": "array", "items": {"type": "string"}, "maxItems": 2}, False)
        self.assertEqual(to_argument(p, "a, b"), (True, ["a", "b"]))
        with self.assertRaises(ParamError):
            to_argument(p, "a,b,c")
        j = classify("selection", {"type": "object", "default": {}}, False)
        self.assertEqual(j.kind, "json")
        self.assertEqual(to_argument(j, '{"hair": ["red"]}'), (True, {"hair": ["red"]}))


class CatalogCoverageTests(unittest.TestCase):
    def test_every_parameter_of_every_endpoint_is_mapped(self):
        families = load_families()
        self.assertGreaterEqual(len(families), 25)
        for family in families:
            for mode in family.modes:
                params = mode_params(mode.schema, mode.ui_schema)
                self.assertEqual({p.name for p in params}, set(mode.schema["properties"]),
                                 f"{mode.endpoint} lost parameters")
                for p in params:
                    self.assertIn(p.kind, {"text", "enum", "int", "float", "bool", "seed", "image", "video", "audio",
                                           "media_list", "url", "csv", "multi_prompt", "color", "colors", "json"})

    def test_seedance_2_family(self):
        family = next(f for f in load_families() if f.slug == "bytedance-seedance-2-0")
        self.assertEqual(family.output, "video")
        self.assertEqual(family.modes[0].endpoint, "bytedance/seedance-2.0/text-to-video")
        keys = [m.key for m in family.modes]
        self.assertIn("Image to Video", keys)
        i2v = next(m for m in family.modes if m.key == "Image to Video")
        kinds = {p.name: p.kind for p in mode_params(i2v.schema, i2v.ui_schema)}
        self.assertEqual(kinds["image_url"], "image")
        self.assertEqual(kinds["end_image_url"], "image")
        self.assertEqual(kinds["resolution"], "enum")


if __name__ == "__main__":
    unittest.main()
