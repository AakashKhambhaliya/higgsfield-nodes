import unittest

from higgsfield_nodes.schema_check import errors


class SchemaCheckTests(unittest.TestCase):
    def test_basic_keywords(self):
        schema = {"type": "object", "required": ["prompt"], "additionalProperties": False, "properties": {
            "prompt": {"type": "string", "minLength": 1, "pattern": r"\S"},
            "duration": {"type": "integer", "minimum": 4, "maximum": 15},
            "ratio": {"enum": ["16:9", "1:1"]},
            "refs": {"type": "array", "maxItems": 2, "items": {"type": "string"}},
        }}
        self.assertEqual(errors({"prompt": "x", "duration": 5, "ratio": "1:1", "refs": ["a"]}, schema), [])
        self.assertIn("missing prompt", errors({}, schema))
        self.assertIn("duration must be ≤ 15", errors({"prompt": "x", "duration": 20}, schema))
        self.assertIn("refs accepts at most 2 item(s)", errors({"prompt": "x", "refs": ["a", "b", "c"]}, schema))
        self.assertIn("unsupported field(s): extra", errors({"prompt": "x", "extra": 1}, schema))
        self.assertTrue(errors({"prompt": "   "}, schema))
        self.assertEqual(errors({"prompt": "x", "duration": True}, schema), ["duration must be integer"])

    def test_if_then_else_chain(self):
        schema = {"if": {"required": ["image_urls"]},
                  "else": {"if": {"required": ["video_urls"]}, "else": {"required": ["audio_urls"]}}}
        self.assertEqual(errors({"image_urls": ["a"]}, schema), [])
        self.assertEqual(errors({"video_urls": ["a"]}, schema), [])
        self.assertEqual(errors({}, schema), ["missing audio_urls"])

    def test_all_of_with_const(self):
        schema = {"allOf": [{"if": {"required": ["enable_thinking"], "properties": {"enable_thinking": {"const": True}}},
                             "then": {"required": ["prompt_extend"], "properties": {"prompt_extend": {"const": True}}}}]}
        self.assertEqual(errors({"enable_thinking": False}, schema), [])
        self.assertEqual(errors({"enable_thinking": True}, schema), ["missing prompt_extend"])
        self.assertEqual(errors({"enable_thinking": True, "prompt_extend": False}, schema), ["prompt_extend must be True"])


if __name__ == "__main__":
    unittest.main()
