import tempfile
import unittest
from pathlib import Path

from higgsfield_nodes import credentials


class CredentialTests(unittest.TestCase):
    def test_parse_and_mask(self):
        self.assertEqual(credentials.parse_combined(" abcd1234:s3cr3t "), ("abcd1234", "s3cr3t"))
        with self.assertRaises(credentials.CredentialError):
            credentials.parse_combined("no-colon")
        self.assertEqual(credentials.mask("abcd1234"), "abcd…34")
        self.assertNotIn("s3cr3t", repr(credentials.Credentials("abcd1234", "s3cr3t", "x")))

    def test_env_precedence_and_store_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "higgsfield" / "credentials.json"
            self.assertIsNone(credentials.resolve({}, path))
            credentials.save("storedid1", "storedsecret", path)
            self.assertNotIn("storedsecret", path.read_text())  # encrypted or base64, never plain JSON
            stored = credentials.resolve({}, path)
            self.assertEqual((stored.key_id, stored.secret, stored.source), ("storedid1", "storedsecret", "ComfyUI settings"))
            env = credentials.resolve({"HF_KEY": "envid:envsecret"}, path)
            self.assertEqual((env.key_id, env.source), ("envid", "environment (HF_KEY)"))
            split = credentials.resolve({"HF_API_KEY_ID": "a", "HF_API_KEY_SECRET": "b"}, path)
            self.assertEqual(split.header_value, "Key a:b")
            status = credentials.status({}, path)
            self.assertLessEqual({"configured": True, "source": "ComfyUI settings", "key_id": "stor…d1",
                                  "managed_by_env": False}.items(), status.items())
            credentials.delete(path)
            self.assertIsNone(credentials.resolve({}, path))


if __name__ == "__main__":
    unittest.main()
