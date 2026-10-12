"""ComfyUI entry point: Higgsfield API nodes (one node per model family).

Works on any ComfyUI >= 0.14.0, local or cloud. On an older ComfyUI the pack logs one clear
"please update ComfyUI" message and loads nothing, instead of breaking ComfyUI's startup.
"""

import logging

from .higgsfield_nodes import MIN_COMFYUI, __version__
from .higgsfield_nodes.compat import problem

WEB_DIRECTORY = "./web"
log = logging.getLogger("higgsfield")
_PROBLEM = problem()

if _PROBLEM is None:
    from typing_extensions import override

    from comfy_api.latest import ComfyExtension, io

    from .higgsfield_nodes import routes, updater
    from .higgsfield_nodes.nodes import build_all_nodes
    from .higgsfield_nodes.soul_id import SOUL_ID_NODES

    class HiggsfieldExtension(ComfyExtension):
        @override
        async def on_load(self) -> None:
            # Each step is independent: a failure is logged and never stops ComfyUI or the nodes from loading.
            for name, step in (("settings routes", routes.register),
                               ("catalog tracking", updater.mark_loaded),        # nodes are built from this catalog
                               ("daily catalog check", updater.start_auto_update)):
                try:
                    step()
                except Exception:
                    log.exception("Higgsfield: %s could not start", name)

        @override
        async def get_node_list(self) -> list[type[io.ComfyNode]]:
            nodes = build_all_nodes()  # logs and skips anything that fails to build
            log.info("Higgsfield nodes %s: %d model nodes + %d Soul ID nodes loaded",
                     __version__, len(nodes), len(SOUL_ID_NODES))
            return nodes + SOUL_ID_NODES

    async def comfy_entrypoint() -> HiggsfieldExtension:
        return HiggsfieldExtension()

else:
    log.error("Higgsfield nodes %s are disabled: %s", __version__, _PROBLEM)
    # Legacy (V1) loader contract with no nodes: ComfyUI loads the pack without errors.
    NODE_CLASS_MAPPINGS: dict = {}
    NODE_DISPLAY_NAME_MAPPINGS: dict = {}
    WEB_DIRECTORY = None
    __all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "MIN_COMFYUI"]
