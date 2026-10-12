"""Model catalog updates from inside ComfyUI (Settings → Higgsfield → Model catalog).

* update_now(): re-crawl Higgsfield, diff against the current catalog, write atomically (keeping a .bak).
* status(): counts, last sync, whether a ComfyUI restart is needed for new/changed nodes.
* start_auto_update(): once-a-day background check on startup (can be turned off in Settings).

Node definitions are built when ComfyUI starts, so new or changed models appear after a restart.
Prices are read live, so price updates need no restart.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .catalog import _load_valid, active_catalog, user_catalog_path
from .credentials import user_directory

log = logging.getLogger("higgsfield")
AUTO_INTERVAL_SECONDS = 24 * 60 * 60
_lock = threading.Lock()
_loaded_fingerprint: str | None = None


# --------------------------------------------------------------------------- settings

def _settings_path() -> Path:
    return user_directory() / "higgsfield" / "settings.json"


def load_settings() -> dict:
    try:
        data = json.loads(_settings_path().read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes) -> dict:
    data = load_settings() | changes
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(data, indent=2))
    return data


def _atomic_write(path: Path, text: str) -> None:
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


# --------------------------------------------------------------------------- fingerprint / diff

def fingerprint(catalog: dict) -> str:
    """Hash of what shapes the nodes (families, modes, schemas) — not prices or timestamps."""
    shape = {
        "families": sorted((f.get("slug"), f.get("title"), tuple(f.get("modes") or [])) for f in catalog.get("families", [])),
        "endpoints": {slug: [e.get("input_schema"), e.get("ui_schema"), e.get("variant_title"), e.get("output_fields"),
                             e.get("description")]
                      for slug, e in sorted(catalog.get("endpoints", {}).items())},
    }
    return hashlib.sha256(json.dumps(shape, sort_keys=True, default=str).encode("utf-8")).hexdigest()


def read_catalog(path: Path | None = None) -> dict:
    """A specific catalog file, or the active one (newest of the user download and the bundled release)."""
    if path is None:
        return active_catalog()[1]
    return json.loads(path.read_text(encoding="utf-8"))


def mark_loaded(path: Path | None = None) -> None:
    """Remember which catalog the running nodes were built from."""
    global _loaded_fingerprint
    try:
        _loaded_fingerprint = fingerprint(read_catalog(path))
    except (OSError, ValueError, RuntimeError):
        _loaded_fingerprint = None


def diff(old: dict, new: dict) -> dict:
    old_fams = {f["slug"]: f for f in old.get("families", [])}
    new_fams = {f["slug"]: f for f in new.get("families", [])}
    old_eps, new_eps = old.get("endpoints", {}), new.get("endpoints", {})
    changed = [s for s in set(old_eps) & set(new_eps)
               if json.dumps(old_eps[s].get("input_schema"), sort_keys=True)
               != json.dumps(new_eps[s].get("input_schema"), sort_keys=True)]
    return {
        "added_models": sorted(new_fams[s].get("title") or s for s in set(new_fams) - set(old_fams)),
        "removed_models": sorted(old_fams[s].get("title") or s for s in set(old_fams) - set(new_fams)),
        "added_endpoints": sorted(set(new_eps) - set(old_eps)),
        "removed_endpoints": sorted(set(old_eps) - set(new_eps)),
        "changed_endpoints": sorted(changed),
    }


# --------------------------------------------------------------------------- update / status

def update_now(path: Path | None = None) -> dict:
    """Blocking (run in a thread). Raises on failure; the current catalog is kept untouched then.

    Writes to `path`, or by default to the user copy (ComfyUI/user/higgsfield/catalog/models.json) —
    never into the installed pack, so ComfyUI Manager / git updates of the pack never conflict."""
    target = path or user_catalog_path()
    from .catalog_sync import sync

    if not _lock.acquire(blocking=False):
        raise RuntimeError("A catalog update is already running.")
    try:
        try:
            old = read_catalog(path)  # compare against what is in use now
        except (OSError, ValueError, RuntimeError):  # missing/corrupt catalog: let the update repair it
            log.warning("Higgsfield: current catalog unreadable; it will be replaced by the update")
            old = {"families": [], "endpoints": {}}
        new = sync()
        changes = diff(old, new)
        shape_changed = fingerprint(old) != fingerprint(new)
        if shape_changed or json.dumps(old.get("endpoints"), sort_keys=True) != json.dumps(new.get("endpoints"), sort_keys=True):
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and _load_valid(target):  # keep the previous download as a fallback
                _atomic_write(target.with_name(target.name + ".bak"), target.read_text(encoding="utf-8"))
            _atomic_write(target, json.dumps(new, indent=2, ensure_ascii=False) + "\n")
        result = {
            "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "changed": shape_changed,
            **changes,
            "failures": new.get("failures") or {},
        }
        save_settings(last_check=result["checked_at"], last_result=result)
        log.info("Higgsfield catalog checked: %s", "changes found" if shape_changed else "no model changes")
        return result
    finally:
        _lock.release()


def status(path: Path | None = None) -> dict:
    settings = load_settings()
    try:
        catalog = read_catalog(path)
        current = fingerprint(catalog)
        families = sum(1 for _ in catalog.get("families", []))
        endpoints = len(catalog.get("endpoints", {}))
        synced_at = catalog.get("synced_at")
    except (OSError, ValueError, RuntimeError):
        current, families, endpoints, synced_at = None, 0, 0, None
    return {
        "families": families,
        "endpoints": endpoints,
        "synced_at": synced_at,
        "auto_update": settings.get("auto_update", True),
        "last_check": settings.get("last_check"),
        "last_result": settings.get("last_result"),
        "updating": _lock.locked(),
        "restart_needed": bool(_loaded_fingerprint and current and current != _loaded_fingerprint),
    }


def start_auto_update(path: Path | None = None) -> None:
    settings = load_settings()
    if not settings.get("auto_update", True):
        return
    last = settings.get("last_check")
    try:
        age = time.time() - datetime.fromisoformat(last).timestamp() if last else None
    except ValueError:
        age = None
    if age is not None and age < AUTO_INTERVAL_SECONDS:
        return

    def worker():
        time.sleep(20)  # let ComfyUI finish starting first
        try:
            update_now(path)
        except Exception as error:  # never disturb ComfyUI startup
            log.warning("Higgsfield automatic catalog check failed: %s", error)

    threading.Thread(target=worker, name="higgsfield-catalog-update", daemon=True).start()
