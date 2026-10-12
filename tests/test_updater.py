import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from higgsfield_nodes import updater


def catalog(endpoints: dict, families: list, synced="2026-10-01T00:00:00+00:00"):
    return {"schema_version": 1, "synced_at": synced, "families": families, "endpoints": endpoints}


EP = {"input_schema": {"properties": {"prompt": {"type": "string"}}}, "price": {"min": 0.1}}
OLD = catalog({"a/x": EP}, [{"slug": "a", "title": "Model A", "modes": ["a/x"]}])


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.path = self.dir / "models.json"
        self.path.write_text(json.dumps(OLD), encoding="utf-8")
        patcher = mock.patch.object(updater, "user_directory", return_value=self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def test_fingerprint_ignores_prices_and_timestamps(self):
        repriced = json.loads(json.dumps(OLD))
        repriced["endpoints"]["a/x"]["price"] = {"min": 0.2}
        repriced["synced_at"] = "2026-10-12T00:00:00+00:00"
        self.assertEqual(updater.fingerprint(OLD), updater.fingerprint(repriced))
        changed = json.loads(json.dumps(OLD))
        changed["endpoints"]["a/x"]["input_schema"]["properties"]["seed"] = {"type": "integer"}
        self.assertNotEqual(updater.fingerprint(OLD), updater.fingerprint(changed))

    def test_update_writes_new_catalog_reports_diff_and_restart(self):
        new = catalog({"a/x": EP, "b/y": EP},
                      [{"slug": "a", "title": "Model A", "modes": ["a/x"]},
                       {"slug": "b", "title": "Model B", "modes": ["b/y"]}], synced="2026-10-12T00:00:00+00:00")
        updater.mark_loaded(self.path)
        with mock.patch("higgsfield_nodes.catalog_sync.sync", return_value=new):
            result = updater.update_now(self.path)
        self.assertTrue(result["changed"])
        self.assertEqual(result["added_models"], ["Model B"])
        self.assertEqual(result["added_endpoints"], ["b/y"])
        self.assertTrue((self.dir / "models.json.bak").exists())
        self.assertIn("b/y", json.loads(self.path.read_text(encoding="utf-8"))["endpoints"])
        status = updater.status(self.path)
        self.assertTrue(status["restart_needed"])
        self.assertEqual((status["families"], status["endpoints"]), (2, 2))
        self.assertEqual(status["last_result"]["added_models"], ["Model B"])

    def test_failed_sync_keeps_current_catalog(self):
        before = self.path.read_text(encoding="utf-8")
        with mock.patch("higgsfield_nodes.catalog_sync.sync", side_effect=RuntimeError("offline")):
            with self.assertRaises(RuntimeError):
                updater.update_now(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)
        self.assertFalse(updater._lock.locked())

    def test_settings_round_trip(self):
        self.assertTrue(updater.status(self.path)["auto_update"])
        updater.save_settings(auto_update=False)
        self.assertFalse(updater.status(self.path)["auto_update"])


if __name__ == "__main__":
    unittest.main()
