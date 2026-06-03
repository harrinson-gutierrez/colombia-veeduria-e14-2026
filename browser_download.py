"""Plan B: download the tally forms using a real browser (Playwright).

Guaranteed to pass Akamai because it runs the downloads inside the page
context, just like the official app. Use it if check_access.py reports that
plain Python does not pass.

Requires (one time only):
    pip install --user playwright
    python -m playwright install chromium

Usage:
    python browser_download.py --status 11 [--limit N] [--dept 01]

Resumable and polite (batches with a pause). Validates the %PDF magic bytes.
"""
import argparse
import base64
import csv
import os
import time

import config

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    raise SystemExit("Missing playwright. Run: pip install --user playwright "
                     "&& python -m playwright install chromium")

PDF_MAGIC = b"%PDF-"
BATCH = 20  # downloads per batch before a brief pause


def already_done(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) < len(PDF_MAGIC):
        return False
    with open(path, "rb") as fh:
        return fh.read(len(PDF_MAGIC)) == PDF_MAGIC


def read_rows(filters: dict, limit: int | None) -> list[dict]:
    with open(config.INDEX_CSV, encoding="utf-8") as fh:
        rows = []
        for r in csv.DictReader(fh):
            if filters.get("status") and r["status"] != filters["status"]:
                continue
            if filters.get("dept") and r["department_code"] != filters["dept"]:
                continue
            rows.append(r)
    return rows[:limit] if limit else rows


# Runs INSIDE the page: downloads the PDF and returns it as base64.
FETCH_JS = """
async (url) => {
  const r = await fetch(url);
  const buf = new Uint8Array(await r.arrayBuffer());
  let bin = "";
  for (let i = 0; i < buf.length; i++) bin += String.fromCharCode(buf[i]);
  return { status: r.status, b64: btoa(bin) };
}
"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status")
    ap.add_argument("--dept")
    ap.add_argument("--limit", type=int)
    args = ap.parse_args()

    rows = read_rows({"status": args.status, "dept": args.dept}, args.limit)
    pending = [r for r in rows if not already_done(r["local_path"])]
    print(f"Total {len(rows):,} | pending {len(pending):,}")

    counts = {"ok": 0, "not_pdf": 0, "error": 0}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page(user_agent=config.HEADERS["user-agent"])
        page.goto(f"{config.BASE}/home", wait_until="domcontentloaded")

        for i, row in enumerate(pending, 1):
            try:
                res = page.evaluate(FETCH_JS, row["pdf_url"])
                data = base64.b64decode(res["b64"])
                if data[:len(PDF_MAGIC)] != PDF_MAGIC:
                    counts["not_pdf"] += 1
                else:
                    os.makedirs(os.path.dirname(row["local_path"]), exist_ok=True)
                    tmp = row["local_path"] + ".part"
                    with open(tmp, "wb") as fh:
                        fh.write(data)
                    os.replace(tmp, row["local_path"])
                    counts["ok"] += 1
            except Exception as exc:  # noqa: BLE001
                counts["error"] += 1
                print(f"  [ERR] {row['pdf_url'][:70]} -> {exc}")

            if i % 50 == 0 or i == len(pending):
                print(f"  {i:,}/{len(pending):,}  {counts}", flush=True)
            if i % BATCH == 0:
                time.sleep(config.REQUEST_DELAY_SECONDS)

        browser.close()

    print(f"\nDone. {counts}\nPDFs in: {config.PDF_DIR}")


if __name__ == "__main__":
    main()
