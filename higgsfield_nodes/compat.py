"""Runtime compatibility check, so an older ComfyUI gets one clear message instead of a crash."""

from __future__ import annotations

from . import MIN_COMFYUI

REQUIRED_IO = ("DynamicCombo", "Autogrow", "ControlAfterGenerate", "NumberDisplay", "FolderType")


def comfyui_version() -> str:
    try:
        from comfyui_version import __version__

        return str(__version__)
    except Exception:
        return "unknown"


def problem() -> str | None:
    """None when this ComfyUI can run the nodes; otherwise a user-facing explanation."""
    try:
        import importlib

        latest = importlib.import_module("comfy_api.latest")
        io = latest.io
        if not (hasattr(latest, "ComfyExtension") and hasattr(latest, "ui")):
            raise ImportError("comfy_api.latest is incomplete")
    except Exception as error:
        return (f"this ComfyUI has no V3 node API ({type(error).__name__}: {error}). "
                f"Update ComfyUI to {MIN_COMFYUI} or newer.")
    missing = [name for name in REQUIRED_IO if not hasattr(io, name)]
    if missing:
        return (f"ComfyUI {comfyui_version()} is too old (missing {', '.join(missing)}). "
                f"Update ComfyUI to {MIN_COMFYUI} or newer to use the Higgsfield nodes.")
    return None
