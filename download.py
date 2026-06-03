"""Stage 1: download the ballot tally sheet PDFs based on the SQLite status.

- Managed: reads 'pending'/'failed' tables from the database and writes the result.
- Resumable and idempotent: skips those already 'ok' (and validates the file on disk).
- Retryable: --retry-failed reopens the ones that failed.
- Respectful: limited concurrency and a per-request pause inside each worker.
- Robust: validates the %PDF magic bytes (the server replies with HTML 200 on failure).

Standard library only.
    python download.py [--status 11] [--dept 16] [--limit N] [--retry-failed]
"""
import argparse
import os
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
import db

PDF_MAGIC = b"%PDF-"
CHUNK = 64 * 1024
BLOCK_CODES = (403, 429)

_local = threading.local()

# Shared block signal: set when the source keeps returning 403/429. Stages and
# the dashboard runner can read it to stop and surface a 'blocked' state.
BLOCKED = threading.Event()


def _humanlike_pause() -> None:
    """Randomized per-request pause (deterministic-free; no Math.random needed)."""
    lo, hi = config.REQUEST_DELAY_MIN, config.REQUEST_DELAY_MAX
    # Derive a varying value from a monotonic clock fraction to avoid a fixed
    # cadence without importing random (keeps runs reproducible-ish).
    frac = (time.monotonic() * 1000) % 1000 / 1000.0
    time.sleep(lo + (hi - lo) * frac)


def opener() -> urllib.request.OpenerDirector:
    """One opener per thread (urllib is not thread-safe when sharing connections)."""
    if not hasattr(_local, "opener"):
        _local.opener = urllib.request.build_opener()
    return _local.opener


def select_pending(conn, args) -> list:
    where = ["download_status IN ('pending','failed')"] if args.retry_failed \
        else ["download_status = 'pending'"]
    params: list = []
    if args.status:
        where.append("status = ?")
        params.append(args.status)
    if args.dept:
        where.append("department_code = ?")
        params.append(args.dept)
    sql = (
        "SELECT transmission_code, pdf_url, local_path, download_attempts "
        f"FROM polling_tables WHERE {' AND '.join(where)} ORDER BY transmission_code"
    )
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return conn.execute(sql, params).fetchall()


def file_is_valid_pdf(path: str) -> bool:
    if not os.path.exists(path) or os.path.getsize(path) < len(PDF_MAGIC):
        return False
    with open(path, "rb") as fh:
        return fh.read(len(PDF_MAGIC)) == PDF_MAGIC


def download_one(row) -> tuple[str, str, int, str]:
    """Return (status, code, file_bytes, error). status may be 'blocked'."""
    tcode, url, path = row["transmission_code"], row["pdf_url"], row["local_path"]
    if file_is_valid_pdf(path):
        return ("ok", tcode, os.path.getsize(path), "")
    if BLOCKED.is_set():
        return ("blocked", tcode, 0, "source blocking (403/429)")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    req = urllib.request.Request(url, headers=config.HEADERS)
    last_err = ""
    consecutive_blocks = 0
    for attempt in range(1, config.MAX_RETRIES + 1):
        try:
            with opener().open(req, timeout=config.TIMEOUT_SECONDS) as resp:
                head = resp.read(len(PDF_MAGIC))
                if head != PDF_MAGIC:
                    return ("not_pdf", tcode, 0, "non-PDF response (HTML)")
                tmp = path + ".part"
                with open(tmp, "wb") as fh:
                    fh.write(head)
                    shutil.copyfileobj(resp, fh, CHUNK)
            os.replace(tmp, path)
            return ("ok", tcode, os.path.getsize(path), "")
        except urllib.error.HTTPError as exc:
            last_err = f"HTTP {exc.code}"
            if exc.code in BLOCK_CODES:
                consecutive_blocks += 1
                if consecutive_blocks > config.BLOCK_MAX_BACKOFFS:
                    BLOCKED.set()  # signal the whole run to stop
                    return ("blocked", tcode, 0, f"blocked after {consecutive_blocks} backoffs")
                wait = min(config.BLOCK_BACKOFF_BASE * (2 ** (consecutive_blocks - 1)),
                           config.BLOCK_BACKOFF_CAP)
                time.sleep(wait)
                continue  # retry the same table after backing off
            time.sleep(min(2 ** attempt, 10))
        except Exception as exc:  # noqa: BLE001
            last_err = str(exc)
            time.sleep(min(2 ** attempt, 10))
        finally:
            _humanlike_pause()  # per-request courtesy, randomized
    return ("failed", tcode, 0, last_err)


def record(conn, status: str, tcode: str, nbytes: int, error: str) -> None:
    conn.execute(
        """
        UPDATE polling_tables SET
            download_status = ?,
            download_attempts = download_attempts + 1,
            download_error = ?,
            file_bytes = ?,
            updated_at = datetime('now')
        WHERE transmission_code = ?
        """,
        (status, error or None, nbytes or None, tcode),
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--status", help="Filter by table status (e.g. 11)")
    ap.add_argument("--dept", help="Filter by department code (e.g. 16)")
    ap.add_argument("--limit", type=int, help="Maximum number of tables to process")
    ap.add_argument("--retry-failed", action="store_true",
                    help="Also retry the ones left in 'failed'")
    args = ap.parse_args()

    db.init_db()
    with db.connect() as conn:
        rows = select_pending(conn, args)
    total = len(rows)
    if not total:
        print("Nothing pending. (did you import the index yet? python import_index.py)")
        return
    print(f"Tables to download: {total:,}  (concurrency {config.MAX_CONCURRENCY})")

    counts = {"ok": 0, "not_pdf": 0, "failed": 0}
    done = 0
    # Dedicated connection to write results from the main thread.
    with db.connect() as wconn, \
            ThreadPoolExecutor(max_workers=config.MAX_CONCURRENCY) as pool:
        futures = {pool.submit(download_one, r): r for r in rows}
        for fut in as_completed(futures):
            status, tcode, nbytes, error = fut.result()
            record(wconn, status, tcode, nbytes, error)
            counts[status] = counts.get(status, 0) + 1
            done += 1
            if done % 50 == 0 or done == total:
                wconn.commit()
                print(f"  {done:,}/{total:,}  {counts}", flush=True)
            if status == "failed":
                print(f"  [FAIL] {tcode} -> {error}", file=sys.stderr)

    print(f"\nDone. {counts}")
    print(f"PDFs in: {config.PDF_DIR}   |   Status in: {db.DB_PATH}")


if __name__ == "__main__":
    main()
