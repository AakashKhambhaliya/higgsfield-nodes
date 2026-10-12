"""Packaging, update and portability behaviour (no ComfyUI or network needed)."""

import asyncio
import json
import re
import tempfile
import unittest

try:
    import tomllib  # Python 3.11+
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    tomllib = None
from pathlib import Path
from unittest import mock

import httpx

import higgsfield_nodes
from higgsfield_nodes import catalog, credentials, versions

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(tomllib is None, "tomllib needs Python 3.11+")
class PackagingTests(unittest.TestCase):
    def test_versions_and_metadata_agree(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(project["project"]["version"], higgsfield_nodes.__version__)
        self.assertEqual(project["project"]["name"], higgsfield_nodes.REGISTRY_ID)
        self.assertRegex(project["project"]["version"], r"^\d+\.\d+\.\d+$")
        self.assertEqual(project["tool"]["comfy"]["requires-comfyui"], f">={higgsfield_nodes.MIN_COMFYUI}")
        self.assertEqual(project["project"]["license"], {"file": "LICENSE"})
        self.assertTrue((ROOT / "LICENSE").exists())
        # registry name rules: letters/digits/-_. only, no leading special char, no consecutive specials
        self.assertRegex(project["project"]["name"], r"^[A-Za-z][A-Za-z0-9]*(?:[-_.][A-Za-z0-9]+)*$")

    def test_requirements_match_pyproject(self):
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        reqs = [l.strip() for l in (ROOT / "requirements.txt").read_text().splitlines() if l.strip()]
        for req in reqs:
            self.assertIn(req, project["project"]["dependencies"])

    def test_bundled_catalog_and_templates_ship(self):
        data = json.loads(catalog.BUNDLED_CATALOG.read_text(encoding="utf-8"))
        self.assertGreaterEqual(len(data["endpoints"]), 80)
        templates = sorted((ROOT / "example_workflows").glob("*.json"))
        self.assertGreaterEqual(len(templates), 6)
        for path in templates:
            workflow = json.loads(path.read_text(encoding="utf-8"))
            self.assertTrue(any(str(n.get("type", "")).startswith("Higgsfield_") for n in workflow["nodes"]), path.name)

    def test_comfyignore_keeps_runtime_files(self):
        ignored = (ROOT / ".comfyignore").read_text(encoding="utf-8")
        for needed in ("higgsfield_nodes", "web", "example_workflows", "__init__.py", "requirements.txt"):
            self.assertNotRegex(ignored, rf"(?m)^{re.escape(needed)}/?$")


class ActiveCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.user = Path(self.tmp.name) / "higgsfield" / "catalog" / "models.json"
        patcher = mock.patch.object(catalog, "user_catalog_path", return_value=self.user)
        patcher.start()
        self.addCleanup(patcher.stop)
        catalog._active_cache = None

    def write(self, synced_at):
        self.user.parent.mkdir(parents=True, exist_ok=True)
        bundled = json.loads(catalog.BUNDLED_CATALOG.read_text(encoding="utf-8"))
        self.user.write_text(json.dumps(bundled | {"synced_at": synced_at}), encoding="utf-8")

    def test_bundled_used_without_download(self):
        self.assertEqual(catalog.active_catalog()[0], catalog.BUNDLED_CATALOG)

    def test_newer_download_wins_and_older_download_loses(self):
        self.write("2999-01-01T00:00:00+00:00")
        self.assertEqual(catalog.active_catalog()[0], self.user)
        self.write("2000-01-01T00:00:00+00:00")  # e.g. pack updated with a fresher bundled catalog
        self.assertEqual(catalog.active_catalog()[0], catalog.BUNDLED_CATALOG)

    def test_corrupt_download_is_ignored(self):
        self.user.parent.mkdir(parents=True, exist_ok=True)
        self.user.write_text("{ broken", encoding="utf-8")
        self.assertEqual(catalog.active_catalog()[0], catalog.BUNDLED_CATALOG)


class CloudKeyTests(unittest.TestCase):
    def test_environment_key_is_managed_and_reported(self):
        self.assertTrue(credentials.managed_by_environment({"HF_KEY": "a:b"}))
        self.assertTrue(credentials.managed_by_environment({"HF_API_KEY_ID": "a", "HF_API_KEY_SECRET": "b"}))
        self.assertFalse(credentials.managed_by_environment({}))
        with tempfile.TemporaryDirectory() as tmp:
            status = credentials.status({"HF_KEY": "abcdef123:secret"}, Path(tmp) / "c.json")
        self.assertTrue(status["managed_by_env"])
        self.assertIn("storage", status)


class VersionCheckTests(unittest.TestCase):
    def setUp(self):
        versions._cache = None

    def run_check(self, handler):
        return asyncio.run(versions.version_status(transport=httpx.MockTransport(handler), now=1000.0))

    def test_compare(self):
        self.assertTrue(versions.compare("0.9.0", "1.0.0"))
        self.assertTrue(versions.compare("0.9.0", "0.10.0"))
        self.assertFalse(versions.compare("1.0.0", "1.0.0"))
        self.assertFalse(versions.compare("1.2.0", "1.1.9"))
        self.assertFalse(versions.compare("1.0.0", None))

    def test_update_available(self):
        status = self.run_check(lambda r: httpx.Response(200, json={"latest_version": {"version": "99.0.0"}}))
        self.assertTrue(status["update_available"])
        self.assertEqual(status["latest"], "99.0.0")
        self.assertIn("higgsfield-nodes", status["page"])

    def test_not_published_yet_and_offline(self):
        self.assertFalse(self.run_check(lambda r: httpx.Response(404))["published"])
        versions._cache = None

        def offline(request):
            raise httpx.ConnectError("offline")

        status = self.run_check(offline)
        self.assertFalse(status["update_available"])
        self.assertIn("Couldn't reach", status["error"])


class CompatTests(unittest.TestCase):
    def test_old_comfyui_gets_a_clear_message(self):
        from higgsfield_nodes import compat

        fake_io = type("io", (), {"DynamicCombo": 1})  # missing Autogrow etc.
        fake_module = type("m", (), {"ComfyExtension": object, "io": fake_io, "ui": object})
        with mock.patch.dict("sys.modules", {"comfy_api.latest": fake_module}):
            message = compat.problem()
        self.assertIn("Update ComfyUI to 0.14.0", message)


if __name__ == "__main__":
    unittest.main()
