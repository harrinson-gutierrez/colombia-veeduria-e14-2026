"""Compare local vision models on the same E14 against known ground truth.

Runs each model (via the VISION_MODEL env override) over one PDF and scores how
close its candidate + totals-band readings are to values you typed by reading
the PDF yourself. Helps pick the model that reads handwritten digits best on a
12 GB GPU.

    python compare_models.py <pdf_path>
Edit TRUTH below to match the sheet you test against.
"""
import sys
import time

import candidates as C
import vision

MODELS = ["qwen2.5vl:7b", "minicpm-v:latest", "llama3.2-vision:latest"]

# Ground truth for the test sheet (read by a human from the PDF).
# Keys: candidate index 0..12, and band fields. None = leave unset.
TRUTH = {
    "candidates": {0: 92, 1: 9},   # CEPEDA=92, CLAUDIA=9 (fill more as you read)
    "blank": 2, "null": 2, "unmarked": 0, "total": 296,
}


def read_with(model: str, pdf: str) -> dict:
    vision.VISION_MODEL = model
    out = {"candidates": {}, "band": {}}
    r = vision.read_tally(pdf)
    out["total"] = r.get("total")
    for c in r.get("candidates", []):
        idx = C.match_index(c.get("name", ""))
        if idx is not None:
            out["candidates"][idx] = c.get("votes")
    try:
        out["band"] = vision.read_totals_band(pdf)
    except Exception:  # noqa: BLE001
        out["band"] = {}
    return out


def score(got: dict) -> tuple[int, int]:
    hits = total = 0
    for idx, exp in TRUTH["candidates"].items():
        total += 1
        if got["candidates"].get(idx) == exp:
            hits += 1
    for f in ("blank", "null", "unmarked"):
        if TRUTH.get(f) is not None:
            total += 1
            if got["band"].get(f) == TRUTH[f]:
                hits += 1
    if TRUTH.get("total") is not None:
        total += 1
        if got.get("total") == TRUTH["total"]:
            hits += 1
    return hits, total


def main():
    if len(sys.argv) < 2:
        print("usage: python compare_models.py <pdf_path>")
        return
    pdf = sys.argv[1]
    print(f"PDF: {pdf}\nGround truth: {TRUTH}\n")
    for model in MODELS:
        if not vision.available_model(model):
            print(f"[skip] {model} not installed")
            continue
        t = time.monotonic()
        try:
            got = read_with(model, pdf)
        except Exception as exc:  # noqa: BLE001
            print(f"[err]  {model}: {exc}")
            continue
        hits, tot = score(got)
        dt = time.monotonic() - t
        cands = {i: got["candidates"].get(i) for i in TRUTH["candidates"]}
        print(f"=== {model}  ->  {hits}/{tot} aciertos, {dt:.0f}s ===")
        print(f"    candidatos clave: {cands}  (verdad {TRUTH['candidates']})")
        print(f"    banda: blank={got['band'].get('blank')} null={got['band'].get('null')} "
              f"unmarked={got['band'].get('unmarked')} total={got.get('total')} "
              f"(verdad 2/2/0/296)\n")


if __name__ == "__main__":
    main()
