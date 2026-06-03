"""Step 1: download the metadata JSON files and show their structure.

Uses only the Python standard library: NO pip install required.

    python inspect_meta.py

Paste the output to fine-tune the exact mapping department->...->table->hash.
"""
import json
import os
import time
import urllib.request

import config


def fetch(name: str):
    url = f"{config.META_BASE}/{name}.json"
    req = urllib.request.Request(url, headers=config.HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=config.TIMEOUT_SECONDS) as resp:
            raw = resp.read()
    except Exception as exc:  # noqa: BLE001
        print(f"[ERROR] {name}: {exc}")
        return None
    os.makedirs(config.META_DIR, exist_ok=True)
    with open(f"{config.META_DIR}/{name}.json", "wb") as fh:
        fh.write(raw)
    print(f"[OK] {name}.json -> {len(raw):,} bytes")
    return json.loads(raw)


def describe(name: str, obj) -> None:
    print(f"\n===== {name} =====")
    print(f"root type: {type(obj).__name__}")
    if isinstance(obj, dict):
        keys = list(obj.keys())
        print(f"keys ({len(keys)}): {keys[:15]}")
        if keys:
            first = obj[keys[0]]
            print(f"sample value [{keys[0]}] -> {type(first).__name__}")
            print(_preview(first))
    elif isinstance(obj, list):
        print(f"length: {len(obj)}")
        if obj:
            print(f"first element -> {type(obj[0]).__name__}")
            print(_preview(obj[0]))


def _preview(value) -> str:
    text = json.dumps(value, ensure_ascii=False, indent=2)
    return text[:1200] + ("\n... [truncated]" if len(text) > 1200 else "")


def main() -> None:
    for name in config.META_FILES:
        obj = fetch(name)
        if obj is not None:
            describe(name, obj)
        time.sleep(config.REQUEST_DELAY_SECONDS)


if __name__ == "__main__":
    main()
