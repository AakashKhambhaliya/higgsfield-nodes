"""List every distinct parameter shape in the catalog (to design widget mappings)."""

import json
import sys
from collections import defaultdict
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "higgsfield_nodes" / "catalog" / "models.json"
catalog = json.loads(path.read_text(encoding="utf-8"))
show = set(sys.argv[1:])
shapes = defaultdict(list)
ui_keys = defaultdict(set)
for slug, ep in catalog["endpoints"].items():
    for name, spec in ep["input_schema"]["properties"].items():
        compact = {k: v for k, v in spec.items() if k not in ("description", "default", "title", "examples")}
        shapes[name].append((slug, json.dumps(compact, sort_keys=True)))
    for name, hint in (ep.get("ui_schema") or {}).items():
        ui_keys[name].add(json.dumps(hint, sort_keys=True))

for name in sorted(shapes):
    variants = sorted({s for _, s in shapes[name]})
    if show and name not in show:
        print(f"{name}: {len(shapes[name])} endpoints, {len(variants)} shapes")
        continue
    print(f"\n### {name}  ({len(shapes[name])} endpoints)")
    for variant in variants:
        users = [slug for slug, s in shapes[name] if s == variant]
        print(f"  {variant[:600]}\n     <- {users[:4]}{' ...' if len(users) > 4 else ''}")
    for hint in sorted(ui_keys.get(name, [])):
        print(f"  ui: {hint[:300]}")
