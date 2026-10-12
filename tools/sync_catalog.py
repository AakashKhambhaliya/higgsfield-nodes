"""Command-line wrapper: refresh higgsfield_nodes/catalog/models.json from Higgsfield's public pages.

Same as Settings → Higgsfield → Model catalog → "Check for updates" in ComfyUI.
Usage:  python tools/sync_catalog.py [--out PATH]
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from higgsfield_nodes.catalog_sync import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
