"""Step 1.5: find the record of the sample PDF to confirm the mapping of
fields -> URL segments.

Sample PDF (from the browser Network tab):
  /assets/temis/pdf/16/001/001/01/001/PRE/7867965b...fd3.pdf

Standard library only.  python verify_mapping.py
"""
import json

import config

TARGET_HASH = "7867965b060396fbfbd55c5c29ca7e2fa644bd754d8d3487ea3e4089f57d1fd3"


def iter_nodes(obj):
    """Walk all groups (status3, etc.) and yield each node."""
    data = obj["data"]
    for group_key, group in data.items():
        nodes = group.get("nodes") if isinstance(group, dict) else None
        if not nodes:
            continue
        for node in nodes:
            yield group_key, node


def main() -> None:
    with open(f"{config.META_DIR}/allTransmissionCodes.json", encoding="utf-8") as fh:
        obj = json.load(fh)

    groups = {}
    match = None
    for group_key, node in iter_nodes(obj):
        groups[group_key] = groups.get(group_key, 0) + 1
        if node.get("expectedName", "").startswith(TARGET_HASH):
            match = (group_key, node)

    print("=== groups (status -> node count) ===")
    for k, v in sorted(groups.items()):
        print(f"  {k}: {v:,}")
    print(f"  TOTAL: {sum(groups.values()):,}")

    print("\n=== record matching the sample PDF ===")
    if match:
        group_key, node = match
        print(f"group: {group_key}")
        print(json.dumps(node, ensure_ascii=False, indent=2))
        print("\n=== expected URL according to the sample ===")
        print("/assets/temis/pdf/16/001/001/01/001/PRE/" + TARGET_HASH + ".pdf")
        print("\nMap each intermediate segment (001/01/001) to the fields above.")
    else:
        print("NOT found. The hash may belong to another group/status.")


if __name__ == "__main__":
    main()
