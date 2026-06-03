"""Stage 2: OCR of the downloaded tally sheets (Tesseract, full page).

- Managed: reads tables with download_status='ok' and a pending/failed ocr_status.
- Stores the raw OCR text in polling_tables.ocr_text for auditing and for tuning
  the extraction regex against real data.
- --debug prints the raw text (use it with --limit 1 at the start).

Requires: pytesseract, pdf2image, pillow, Tesseract and Poppler (see INSTALL_OCR.md).

    python ocr.py [--dept 16] [--limit N] [--retry-failed] [--debug]
"""
import argparse
import os
import sys

import config
import db

try:
    import pytesseract
    from pdf2image import convert_from_path
except ImportError:
    pytesseract = None
    convert_from_path = None


def ensure_deps() -> None:
    if pytesseract is None or convert_from_path is None:
        raise SystemExit(
            "Missing OCR dependencies. Install: pip install pytesseract pdf2image pillow\n"
            "and Tesseract + Poppler (see INSTALL_OCR.md)."
        )
    if config.TESSERACT_CMD:
        pytesseract.pytesseract.tesseract_cmd = config.TESSERACT_CMD
    if config.TESSDATA_DIR and os.path.isdir(config.TESSDATA_DIR):
        os.environ["TESSDATA_PREFIX"] = os.path.abspath(config.TESSDATA_DIR)


def select_pending(conn, args) -> list:
    where = ["download_status = 'ok'"]
    where.append(
        "ocr_status IN ('pending','failed')" if args.retry_failed
        else "ocr_status = 'pending'"
    )
    params: list = []
    if args.dept:
        where.append("department_code = ?")
        params.append(args.dept)
    sql = (
        "SELECT transmission_code, local_path "
        f"FROM polling_tables WHERE {' AND '.join(where)} ORDER BY transmission_code"
    )
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return conn.execute(sql, params).fetchall()


def ocr_pdf(path: str) -> str:
    kwargs = {"dpi": config.OCR_DPI}
    if config.POPPLER_PATH:
        kwargs["poppler_path"] = config.POPPLER_PATH
    pages = convert_from_path(path, **kwargs)
    chunks = [pytesseract.image_to_string(img, lang=config.OCR_LANG) for img in pages]
    return "\n\n--- PAGE ---\n\n".join(chunks)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--retry-failed", action="store_true")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    ensure_deps()
    db.init_db()
    with db.connect() as conn:
        rows = select_pending(conn, args)
    if not rows:
        print("Nothing pending for OCR. (did you download yet? python download.py ...)")
        return
    print(f"Tables to process (OCR): {len(rows):,}")

    counts = {"ok": 0, "failed": 0}
    with db.connect() as conn:
        for i, row in enumerate(rows, 1):
            tcode, path = row["transmission_code"], row["local_path"]
            try:
                text = ocr_pdf(path)
                conn.execute(
                    """UPDATE polling_tables SET ocr_status='ok', ocr_attempts=ocr_attempts+1,
                       ocr_error=NULL, ocr_text=?, updated_at=datetime('now')
                       WHERE transmission_code=?""",
                    (text, tcode),
                )
                counts["ok"] += 1
                if args.debug:
                    print(f"\n===== {tcode} =====\n{text}\n========================")
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    """UPDATE polling_tables SET ocr_status='failed', ocr_attempts=ocr_attempts+1,
                       ocr_error=?, updated_at=datetime('now')
                       WHERE transmission_code=?""",
                    (str(exc), tcode),
                )
                counts["failed"] += 1
                print(f"  [FAIL] {tcode}: {exc}", file=sys.stderr)
            if i % 10 == 0 or i == len(rows):
                conn.commit()
                print(f"  {i:,}/{len(rows):,}  {counts}", flush=True)

    print(f"\nDone. {counts}")


if __name__ == "__main__":
    main()
