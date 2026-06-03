"""Locate and crop the BLANK/NULL/UNMARKED/TOTAL band of an E14 tally sheet.

The handwritten counts in that band are tiny on a full-page raster, so the
vision model misreads them (it reads '- - 2' as 0). This module finds the band
by its PRINTED labels (which OCR reads reliably) and crops the handwritten
digits region to the right of those labels, upscaled, so the model can read it.

No OpenCV needed: PIL + pytesseract only.

    python crop_totals.py <pdf_path>     # writes data/debug/<name>_totals.png
"""
import os
import sys

import pytesseract
from PIL import Image

import config

if getattr(config, "TESSERACT_CMD", None):
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD

# Point Tesseract at the bundled tessdata (spa.traineddata) via the env var.
# More reliable than --tessdata-dir when the path contains spaces.
if getattr(config, "TESSDATA_DIR", None):
    os.environ["TESSDATA_PREFIX"] = os.path.abspath(config.TESSDATA_DIR)

# DPI for locating labels (low is fine) and for the final crop (high for digits).
LOCATE_DPI = 150
CROP_DPI = 420

# Printed label keywords that mark the rows we need, in order.
ROW_LABELS = {
    "blank": ["BLANCO"],
    "null": ["NULOS", "NULO"],
    "unmarked": ["MARCADOS"],   # "NO MARCADOS"
    "total": ["SUMA"],          # "SUMA TOTAL"
}


def _render(pdf_path: str, dpi: int) -> list[Image.Image]:
    from pdf2image import convert_from_path
    kwargs = {"dpi": dpi}
    if config.POPPLER_PATH:
        kwargs["poppler_path"] = config.POPPLER_PATH
    return convert_from_path(pdf_path, **kwargs)


def _label_rows(img: Image.Image) -> dict:
    """Return {field: (top, bottom, label_left_x)} in image pixel coords."""
    data = pytesseract.image_to_data(
        img, lang="spa", output_type=pytesseract.Output.DICT,
    )
    rows: dict = {}
    n = len(data["text"])
    for i in range(n):
        word = (data["text"][i] or "").strip().upper()
        if not word:
            continue
        for field, keys in ROW_LABELS.items():
            if field in rows:
                continue
            if any(k in word for k in keys):
                top = data["top"][i]
                height = data["height"][i]
                left = data["left"][i]
                rows[field] = (top, top + height, left)
    return rows


def _find_band_page(pdf_path: str) -> tuple[int, dict]:
    """The totals table can be on any page (the E14 spans 3). Scan pages and
    return (page_index, located_rows) for the page that anchors best."""
    pages = _render(pdf_path, LOCATE_DPI)
    best = (-1, {})
    for idx, img in enumerate(pages):
        located = _label_rows(img)
        # A good anchor has the SUMA TOTAL row plus at least one component row.
        if "total" in located and len(located) > len(best[1]):
            best = (idx, located)
    return best


def crop_band(pdf_path: str, out_path: str | None = None) -> str | None:
    """Crop the totals band (all four labelled rows + the digits to their right)
    at high DPI and save it. Returns the saved path, or None if labels not found.
    """
    page_idx, located = _find_band_page(pdf_path)
    if page_idx == -1 or "total" not in located or len(located) < 2:
        return None  # cannot anchor reliably; let caller fall back

    # Vertical span across all labelled rows.
    tops = [v[0] for v in located.values()]
    bottoms = [v[1] for v in located.values()]
    # Keep the printed LABELS in the crop (start a bit left of the leftmost
    # label): the model reads far better when each digit row is anchored by its
    # name ("VOTOS EN BLANCO -> 2"), instead of a context-free column of digits.
    label_left = min(v[2] for v in located.values())

    # Rows are evenly spaced; one row height ~ span / (rows-1). Pad a FULL row
    # above and below so the top digit and SUMA TOTAL are never clipped.
    span = max(bottoms) - min(tops)
    row_h = span / max(1, len(located) - 1)
    scale = CROP_DPI / LOCATE_DPI

    hi = _render(pdf_path, CROP_DPI)[page_idx]
    top = max(0, int((min(tops) - row_h) * scale))
    bottom = min(hi.height, int((max(bottoms) + row_h) * scale))
    left = max(0, int((label_left - 0.2 * row_h) * scale))
    right = hi.width                            # to the page edge / table border

    band = hi.crop((left, top, right, bottom))
    # Upscale so each digit has plenty of pixels for the model.
    band = band.resize((band.width * 2, band.height * 2), Image.LANCZOS)

    out_path = out_path or os.path.join(
        "data", "debug",
        os.path.splitext(os.path.basename(pdf_path))[0] + "_totals.png",
    )
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    band.save(out_path)
    return out_path


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python crop_totals.py <pdf_path>")
        return
    out = crop_band(sys.argv[1])
    print(f"[OK] {out}" if out else "[X] totals labels not found; use full page")


if __name__ == "__main__":
    main()
