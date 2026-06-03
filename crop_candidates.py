"""Locate and crop each CANDIDATE vote box of an E14 tally sheet. EXPERIMENTAL.

Same idea as crop_totals.py, applied per candidate: reading 13 handwritten vote
counts off the full page makes each digit tiny and the model confuses rows. This
detects each vote box by its printed horizontal borders in the right-hand vote
column and crops it tightly, so a crop never bleeds into a neighbour's digits.

STATUS — not yet reliable, do not wire into the pipeline:
- Tight box crops fixed the bleed (1 was being read as 108), but the box->
  candidate INDEX MAPPING is still off: border detection can pick up non-
  candidate boxes (headers, the totals row on page 1), which shifts every index.
  A wrong mapping assigns a digit to the wrong candidate — worse than a misread.
- Next step: validate box count per page (must be 7 then 6), and anchor at least
  one box to its candidate name to lock the offset, before trusting the output.

No OpenCV needed: PIL + numpy + pytesseract.

    python crop_candidates.py <pdf_path>   # writes data/debug/<name>_cand_<i>.png
"""
import os
import sys

import pytesseract
from PIL import Image

import candidates as CAND
import config
from crop_totals import _render  # reuse the same PDF rasteriser

if getattr(config, "TESSERACT_CMD", None):
    pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
if getattr(config, "TESSDATA_DIR", None):
    os.environ["TESSDATA_PREFIX"] = os.path.abspath(config.TESSDATA_DIR)

LOCATE_DPI = 150
CROP_DPI = 420

# One distinctive printed token per master candidate to anchor its row. Chosen
# to be unique on the page and robust to OCR (a surname, not a common word).
ANCHOR_TOKENS = [
    "CEPEDA", "LOPEZ", "BOTERO", "ESPRIELLA", "LIZCANO", "URIBE", "GARVIN",
    "BARRERAS", "CAICEDO", "MATAMOROS", "VALENCIA", "FAJARDO", "MURILLO",
]
assert len(ANCHOR_TOKENS) == len(CAND.MASTER_CANDIDATES)


def _ascii_upper(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return s.upper()


def _anchor_rows(img: Image.Image) -> dict:
    """Return {candidate_index: (y_top, y_bottom)} for anchors found on this page."""
    data = pytesseract.image_to_data(
        img, lang="spa", output_type=pytesseract.Output.DICT)
    rows: dict = {}
    for i in range(len(data["text"])):
        word = _ascii_upper(data["text"][i]).strip()
        if not word:
            continue
        for idx, token in enumerate(ANCHOR_TOKENS):
            if idx in rows:
                continue
            if token in word:
                top = data["top"][i]
                rows[idx] = (top, top + data["height"][i])
    return rows


def _row_height(anchors: dict, page_h: int) -> float:
    """Estimate the vertical pitch between candidate rows on a page."""
    tops = sorted(t for t, _ in anchors.values())
    if len(tops) >= 2:
        gaps = [b - a for a, b in zip(tops, tops[1:])]
        return sum(gaps) / len(gaps)
    return page_h * 0.12  # fallback: ~12% of page height


def _fill_missing_rows(anchors: dict, row_h: float, page_h: int) -> dict:
    """Infer rows whose surname OCR missed. Candidate indices on a page are
    consecutive and evenly spaced, so project missing ones from the found ones
    using the row pitch. Only fills gaps between/adjacent to detected anchors."""
    if len(anchors) < 2:
        return anchors
    found = sorted(anchors)            # candidate indices detected on this page
    lo_idx, hi_idx = found[0], found[-1]
    # Reference line: top of the lowest-index anchor.
    ref_idx = lo_idx
    ref_top = anchors[lo_idx][0]
    filled = dict(anchors)
    for idx in range(lo_idx, hi_idx + 1):
        if idx in filled:
            continue
        top = int(ref_top + (idx - ref_idx) * row_h)
        if 0 <= top < page_h:
            # Reuse the median anchor height for the synthetic box.
            h = int(sum(b - t for t, b in anchors.values()) / len(anchors))
            filled[idx] = (top, top + h)
    return filled


# Vote column lives on the right of the page. Tune if a source uses a different
# layout. Fractions of page width.
VOTE_COL_X0 = 0.62
VOTE_COL_X1 = 0.95


def _vote_boxes(lo: "Image.Image") -> list[tuple[int, int]]:
    """Detect each vote box on a page as (y_top, y_bottom) from its printed
    horizontal borders in the vote column. Robust to the candidate name OCR
    failing, because it reads the form's geometry, not the text."""
    import numpy as np
    arr = np.asarray(lo.convert("L"))
    h, w = arr.shape
    x0, x1 = int(w * VOTE_COL_X0), int(w * VOTE_COL_X1)
    dark_frac = (arr[:, x0:x1] < 100).mean(axis=1)
    # Cluster contiguous dark rows into single border lines.
    lines, run = [], []
    for y in range(h):
        if dark_frac[y] > 0.5:
            run.append(y)
        elif run:
            lines.append(sum(run) // len(run))
            run = []
    if run:
        lines.append(sum(run) // len(run))
    # A box is the gap between two consecutive borders that is tall enough to
    # hold digits (filters out the thin double-lines that frame a single border).
    boxes = []
    for a, b in zip(lines, lines[1:]):
        if b - a >= 0.06 * h:          # ~ one candidate-row height
            boxes.append((a, b))
    return boxes


def crop_candidate_rows(pdf_path: str, out_dir: str | None = None) -> dict:
    """Crop one tight image per candidate VOTE BOX. Returns
    {candidate_index: crop_path}. Candidate indices are assigned top-to-bottom
    across pages (the ballot order is fixed). Uses detected box borders so a
    crop never bleeds into the neighbouring candidate's digits."""
    out_dir = out_dir or os.path.join("data", "debug")
    os.makedirs(out_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(pdf_path))[0]

    lo_pages = _render(pdf_path, LOCATE_DPI)
    hi_pages = _render(pdf_path, CROP_DPI)
    scale = CROP_DPI / LOCATE_DPI

    crops: dict = {}
    next_idx = 0
    for page_idx, lo in enumerate(lo_pages):
        boxes = _vote_boxes(lo)
        if not boxes:
            continue
        hi = hi_pages[page_idx]
        x0 = int(hi.width * VOTE_COL_X0)
        x1 = int(hi.width * VOTE_COL_X1)
        for (ytop, ybot) in boxes:
            if next_idx >= len(CAND.MASTER_CANDIDATES):
                break
            # Small inward margin so the printed borders themselves don't show.
            pad = int(0.10 * (ybot - ytop) * scale)
            y0 = max(0, int(ytop * scale) + pad)
            y1 = min(hi.height, int(ybot * scale) - pad)
            strip = hi.crop((x0, y0, x1, y1))
            strip = strip.resize((strip.width * 2, strip.height * 2), Image.LANCZOS)
            path = os.path.join(out_dir, f"{base}_cand_{next_idx + 1}.png")
            strip.save(path)
            crops[next_idx] = path
            next_idx += 1
    return crops


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python crop_candidates.py <pdf_path>")
        return
    crops = crop_candidate_rows(sys.argv[1])
    if not crops:
        print("[X] no candidate anchors found")
        return
    for idx in sorted(crops):
        print(f"[OK] {CAND.MASTER_CANDIDATES[idx]:35} -> {crops[idx]}")


if __name__ == "__main__":
    main()
