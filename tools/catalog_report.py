"""Print a compact overview of higgsfield_nodes/catalog/models.json."""

import json
import sys
from pathlib import Path

path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parents[1] / "higgsfield_nodes" / "catalog" / "models.json"
catalog = json.loads(path.read_text(encoding="utf-8"))
endpoints = catalog["endpoints"]
covered = set()
for family in catalog["families"]:
    price = ((family.get("pricing") or {}).get("primary") or {})
    print(f"\n## {family['company']} / {family['title']}  [{family['slug']}]  "
          f"from ${price.get('amount')}/{price.get('unit')}  out={family.get('output_types')}")
    for slug in family["modes"]:
        covered.add(slug)
        ep = endpoints.get(slug)
        if not ep:
            print(f"   !! missing {slug}")
            continue
        props = ep["input_schema"]["properties"]
        required = set(ep["input_schema"].get("required", []))
        params = []
        for name, spec in props.items():
            kind = spec.get("type") or ("enum" if "enum" in spec else "?")
            if "enum" in spec:
                kind = f"enum{len(spec['enum'])}"
            params.append(("*" if name in required else "") + f"{name}:{kind}")
        print(f"   - {ep['variant_title']!s:45} {slug}")
        print(f"       {', '.join(params)}")
orphans = sorted(set(endpoints) - covered)
print(f"\nendpoints not in any family: {orphans}")
