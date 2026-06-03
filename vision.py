"""Local vision reader for tally sheets via Ollama (fully on-machine).

Rasterizes a tally-sheet PDF and asks a local vision model to read the
handwritten vote numbers and candidate names into structured JSON. Nothing
leaves the machine: it talks to Ollama on 127.0.0.1.

The result is a PRE-FILL for the review station, never a final answer. Vision
models misread handwriting too; a human confirms every number against the PDF.

    python vision.py <pdf_path>          # quick manual test
"""
import base64
import io
import json
import sys
import urllib.request

import config

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
# Vision model for reading handwritten digits. llama3.2-vision is ~2.8x faster
# than qwen2.5vl (~24-34s vs ~68s per sheet) with comparable digit accuracy on a
# 12 GB GPU, so it is the default. Override via the VISION_MODEL env var, e.g.
# VISION_MODEL=qwen2.5vl:7b (more consistent name mapping, but slower).
import os as _os
VISION_MODEL = _os.environ.get("VISION_MODEL", "llama3.2-vision:latest")
VISION_TIMEOUT = 300
RASTER_DPI = 210   # higher detail for handwriting; balances accuracy vs speed

PROMPT = (
    "This is a Colombian E14 presidential vote tally sheet (handwritten counts). "
    "Read the handwritten number written next to each candidate name, and the "
    "rows for blank votes (VOTOS EN BLANCO), null votes (VOTOS NULOS), unmarked "
    "(NO MARCADOS / VOTOS NO MARCADOS), and the reported total (SUMA TOTAL). "
    "Return ONLY a compact JSON object, no prose, exactly in this shape:\n"
    '{"candidates":[{"name":"FULL NAME","votes":N}],'
    '"blank":N,"null":N,"unmarked":N,"total":N}\n'
    "CRITICAL - how to read each number box on this form:\n"
    "- Every number box has THREE positional sub-cells: hundreds, tens, units.\n"
    "- A DASH '-' (or blank sub-cell) means that position is ZERO/omitted, it is "
    "NOT a digit. Combine only the written digits, right-aligned.\n"
    "  Examples: '- - 2' = 2;  '- - 7' = 7;  '- 6 2' = 62;  '2 6 2' = 262;  "
    "'- - -' = 0 (all dashes = the number zero).\n"
    "- So VOTOS EN BLANCO shown as '- - 2' must be read as 2, not 0.\n"
    "Rules:\n"
    "- A row that is ALL DASHES ('- - -') is the number 0 - return 0, not null.\n"
    "- Return null ONLY if the whole box is missing/illegible (cannot tell).\n"
    "Read carefully sub-cell by sub-cell. Do not invent numbers, but do read the "
    "dashes correctly as omitted leading zeros."
)

try:
    from pdf2image import convert_from_path
except ImportError:
    convert_from_path = None


def available_model(model: str) -> bool:
    """True if Ollama is reachable and `model` (by name or base) is installed."""
    try:
        req = urllib.request.Request("http://127.0.0.1:11434/api/tags")
        with urllib.request.urlopen(req, timeout=5) as r:
            tags = json.load(r)
        names = {m.get("name", "") for m in tags.get("models", [])}
        base = model.split(":")[0]
        present = any(n == model or n.split(":")[0] == base for n in names)
        return present and convert_from_path is not None
    except Exception:  # noqa: BLE001
        return False


def available() -> bool:
    """True if Ollama is reachable and the configured vision model is present."""
    return available_model(VISION_MODEL)


def _pages_as_png_b64(pdf_path: str) -> list[str]:
    kwargs = {"dpi": RASTER_DPI}
    if config.POPPLER_PATH:
        kwargs["poppler_path"] = config.POPPLER_PATH
    pages = convert_from_path(pdf_path, **kwargs)
    out = []
    for img in pages:
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        out.append(base64.b64encode(buf.getvalue()).decode())
    return out


def _ask(images_b64: list[str], prompt: str = PROMPT) -> str:
    # /api/chat: images go inside the user message (qwen2.5-vl expects this).
    body = json.dumps({
        "model": VISION_MODEL,
        "messages": [{"role": "user", "content": prompt, "images": images_b64}],
        "stream": False,
        "options": {"temperature": 0},   # deterministic-ish reading
    }).encode("utf-8")
    req = urllib.request.Request(OLLAMA_URL, data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=VISION_TIMEOUT) as r:
        data = json.load(r)
    # /api/chat returns message.content; fall back to legacy 'response'.
    return data.get("message", {}).get("content", "") or data.get("response", "")


def _extract_json(text: str) -> dict:
    """Pull the first JSON object out of the model's reply."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}


def read_tally(pdf_path: str) -> dict:
    """Read a tally sheet, ONE page at a time (Ollama vision is unreliable with
    several images in one request) and merge the results. Returns the parsed
    dict plus the concatenated raw model text under '_raw' for auditing.

    The E14 splits candidates across pages 1-2; merging keeps all of them. For a
    given field, a real number wins over null; candidates are merged by name.
    """
    if convert_from_path is None:
        raise RuntimeError("pdf2image not installed")
    pages = _pages_as_png_b64(pdf_path)
    use = pages[:2] if len(pages) >= 2 else pages

    merged = {"candidates": [], "blank": None, "null": None,
              "unmarked": None, "total": None}
    seen = {}  # normalized name -> index in merged["candidates"]
    raws = []
    for img in use:
        raw = _ask([img])           # ONE image per request
        raws.append(raw)
        part = _extract_json(raw)
        for key in ("blank", "null", "unmarked", "total"):
            if merged[key] is None and part.get(key) is not None:
                merged[key] = part[key]
        for cand in part.get("candidates", []):
            name = (cand.get("name") or "").strip()
            if not name:
                continue
            k = "".join(name.lower().split())
            if k in seen:
                # keep a real number over a null
                if merged["candidates"][seen[k]].get("votes") is None:
                    merged["candidates"][seen[k]]["votes"] = cand.get("votes")
            else:
                seen[k] = len(merged["candidates"])
                merged["candidates"].append({"name": name, "votes": cand.get("votes")})

    merged["_raw"] = "\n---\n".join(raws)
    return merged


BAND_PROMPT = (
    "This image is the totals table of a Colombian E14 tally sheet. It has "
    "exactly 4 LABELED rows. To the RIGHT of each printed label is a handwritten "
    "number written across 3 cells (hundreds, tens, units). A dash, dot, or "
    "empty cell means that position is empty (an omitted leading zero), NOT a "
    "digit. Combine only the written digits, right-aligned. A row showing only "
    "dashes/dots is 0. Examples: '- - 2'=2; '. . 1'=1; '- - -'=0; '2 6 2'=262.\n"
    "Read each labeled row and return ONLY this JSON, no prose:\n"
    '{"blank":N,"null":N,"unmarked":N}\n'
    "blank=VOTOS EN BLANCO, null=VOTOS NULOS, unmarked=VOTOS NO MARCADOS. "
    "Ignore the SUMA TOTAL row. Read the digit to the right of each label."
)


def read_totals_band(pdf_path: str) -> dict:
    """Read ONLY the blank/null/unmarked/total band, using a tight high-res crop
    so the model can actually see the small handwritten digits. Falls back to an
    empty dict if the band can't be located (caller keeps the full-page result).
    """
    import crop_totals
    crop_path = crop_totals.crop_band(pdf_path)
    if not crop_path:
        return {}
    with open(crop_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    raw = _ask([b64], prompt=BAND_PROMPT)
    parsed = _extract_json(raw)
    parsed["_band_raw"] = raw
    parsed["_band_crop"] = crop_path
    return parsed


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python vision.py <pdf_path>")
        return
    if not available():
        print("Ollama vision not available. Is Ollama running and "
              f"is '{VISION_MODEL}' pulled? (ollama list)")
        return
    result = read_tally(sys.argv[1])
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
