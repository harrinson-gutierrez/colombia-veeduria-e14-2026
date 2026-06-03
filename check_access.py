"""Step 2.5: check whether Python can download PDFs directly.

The site sits behind Akamai. Python requests may go through with just a
User-Agent, or they may require the browser cookies. This script verifies that
with 3 real PDFs and tells you what to do.

If it fails, export the browser cookies (DevTools > Application > Cookies, or
the "Get cookies.txt" extension) and save them to cookies.txt, then retry.

    python check_access.py
"""
import csv
import http.cookiejar
import os
import urllib.request

import config

PDF_MAGIC = b"%PDF-"
COOKIES_FILE = "cookies.txt"


def build_opener() -> urllib.request.OpenerDirector:
    handlers = []
    if os.path.exists(COOKIES_FILE):
        jar = http.cookiejar.MozillaCookieJar(COOKIES_FILE)
        jar.load(ignore_discard=True, ignore_expires=True)
        handlers.append(urllib.request.HTTPCookieProcessor(jar))
        print(f"[i] Using cookies from {COOKIES_FILE} ({len(jar)} cookies)")
    else:
        print(f"[i] No {COOKIES_FILE}: trying with User-Agent only")
    return urllib.request.build_opener(*handlers)


def sample_urls(n: int = 3) -> list[str]:
    with open(config.INDEX_CSV, encoding="utf-8") as fh:
        rows = [r for r in csv.DictReader(fh) if r["status"] == "11"]
    step = max(1, len(rows) // n)
    return [rows[i]["pdf_url"] for i in range(0, len(rows), step)][:n]


def main() -> None:
    if not os.path.exists(config.INDEX_CSV):
        raise SystemExit("Missing data/index.csv. Run: python discover.py")
    opener = build_opener()
    ok = 0
    for url in sample_urls():
        req = urllib.request.Request(url, headers=config.HEADERS)
        try:
            with opener.open(req, timeout=config.TIMEOUT_SECONDS) as resp:
                head = resp.read(len(PDF_MAGIC))
            is_pdf = head == PDF_MAGIC
            print(f"  {'PDF ok' if is_pdf else 'NO pdf'} <- {url[:80]}...")
            ok += is_pdf
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {exc} <- {url[:80]}...")

    print()
    if ok == 3:
        print("[OK] Python passes Akamai. Run: python download.py --status 11")
    elif os.path.exists(COOKIES_FILE):
        print("[X] Still failing even with cookies. Use browser mode (browser_download.py).")
    else:
        print("[!] Python does NOT pass on its own. Export cookies.txt from the browser and retry,")
        print("    or use browser mode (browser_download.py).")


if __name__ == "__main__":
    main()
