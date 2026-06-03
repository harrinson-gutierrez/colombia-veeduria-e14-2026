"""In-process pipeline runner for the review dashboard.

Runs one batch (download -> OCR -> validate) in a background thread, or loops
batches on an interval when auto mode is on. Thread-safe state is exposed for
the dashboard to render live. Fully local; no network beyond the source site.

OCR and validate are imported and called in-process (not subprocesses) so the
dashboard can show progress and so a missing OCR dependency degrades to a clear
status instead of crashing.
"""
import collections
import threading
import time
import traceback

import config
import db
import download
import validate

try:
    import ocr
    _OCR_AVAILABLE = ocr.pytesseract is not None and ocr.convert_from_path is not None
    if _OCR_AVAILABLE:
        ocr.ensure_deps()  # applies TESSERACT_CMD and TESSDATA_PREFIX
except Exception:  # noqa: BLE001
    ocr = None
    _OCR_AVAILABLE = False


class Runner:
    def __init__(self):
        self._lock = threading.Lock()
        self.batch_size = 25
        self.dept = None
        self.auto = False
        self.interval = 10           # seconds between auto batches
        self.running = False         # a batch is in progress
        self.last_result = {}        # counts from the last batch
        self.last_error = ""
        self.last_run_at = ""
        self.total_batches = 0
        self.blocked = False
        self._stop = threading.Event()
        self._worker = None
        # Live activity feed: monotonically-id'd events for the dashboard stream.
        self._events = collections.deque(maxlen=500)
        self._event_seq = 0

    def log(self, kind: str, msg: str) -> None:
        """Append a live event (kind: download|ocr|validate|info|block)."""
        with self._lock:
            self._event_seq += 1
            self._events.append({"id": self._event_seq, "kind": kind, "msg": msg})

    def events_since(self, last_id: int) -> list[dict]:
        with self._lock:
            return [e for e in self._events if e["id"] > last_id]

    # ---- state snapshot for the UI ----
    def snapshot(self) -> dict:
        with self._lock:
            dept = self.dept
        prog = self.dept_progress(dept)
        with self._lock:
            return {
                "auto": self.auto,
                "running": self.running,
                "batch_size": self.batch_size,
                "interval": self.interval,
                "dept": dept,
                "last_result": dict(self.last_result),
                "last_error": self.last_error,
                "last_run_at": self.last_run_at,
                "total_batches": self.total_batches,
                "ocr_available": _OCR_AVAILABLE,
                "blocked": self.blocked,
                "progress": prog,
            }

    def dept_progress(self, dept) -> dict:
        """Per-stage counters for the selected scope (one dept or all)."""
        where = "WHERE status='11'"
        params: list = []
        if dept:
            where += " AND department_code = ?"
            params.append(dept)
        with db.connect() as conn:
            row = conn.execute(
                f"""SELECT count(*) total,
                       sum(download_status='ok') downloaded,
                       sum(ocr_status='ok') ocr_done,
                       sum(validation_status IN ('ok','skipped')) validated,
                       sum(flagged) flagged,
                       sum(download_status='pending') dl_pending
                    FROM polling_tables {where}""",
                params,
            ).fetchone()
        return {k: (row[k] or 0) for k in row.keys()}

    def list_departments(self) -> list[dict]:
        with db.connect() as conn:
            rows = conn.execute(
                """SELECT department_code code, max(department_name) name,
                       count(*) total,
                       sum(download_status='ok') downloaded
                   FROM polling_tables WHERE status='11'
                   GROUP BY department_code ORDER BY department_code"""
            ).fetchall()
        return [dict(r) for r in rows]

    def set_dept(self, dept) -> None:
        with self._lock:
            self.dept = dept or None

    # ---- one batch across the three stages ----
    def run_once(self) -> dict:
        with self._lock:
            if self.running:
                return {"skipped": "a batch is already running"}
            self.running = True
            self.last_error = ""
        result = {"download": {}, "vision": {}}
        try:
            result["download"] = self._download_batch()
            result["vision"] = self._vision_batch()
        except Exception as exc:  # noqa: BLE001
            with self._lock:
                self.last_error = f"{exc}\n{traceback.format_exc()[:400]}"
        finally:
            with self._lock:
                self.running = False
                self.last_result = result
                self.last_run_at = _now()
                self.total_batches += 1
        return result

    def _download_batch(self) -> dict:
        args = _Args(status="11", dept=self.dept, limit=self.batch_size, retry_failed=False)
        with db.connect() as conn:
            rows = download.select_pending(conn, args)
        counts = {"ok": 0, "not_pdf": 0, "failed": 0, "blocked": 0}
        if not rows:
            return counts
        with db.connect() as wconn:
            for r in rows:
                status, tcode, nbytes, error = download.download_one(r)
                download.record(wconn, status, tcode, nbytes, error)
                counts[status] = counts.get(status, 0) + 1
                if status == "blocked":
                    with self._lock:
                        self.blocked = True
                    self.log("block", f"BLOCKED by source ({error}). Pausing.")
                    break
                if status == "ok":
                    self.log("download", f"downloaded {tcode} ({nbytes//1024} KB)")
                elif status == "not_pdf":
                    self.log("download", f"{tcode}: not published yet")
                else:
                    self.log("download", f"{tcode}: {status} ({error[:40]})")
            wconn.commit()
        return counts

    def _vision_batch(self) -> dict:
        """Read downloaded tables with the LOCAL vision model, store the numbers
        as ocr_value (per master-candidate row), and run the sum check. These are
        machine guesses for a human to confirm; the cross-check only flags for
        review, never declares fraud.
        """
        try:
            import vision
            import candidates as C
            if not vision.available():
                self.log("info", "vision model unavailable (start Ollama)")
                return {"unavailable": True}
        except Exception as exc:  # noqa: BLE001
            self.log("info", f"vision import error: {exc}")
            return {"unavailable": True}

        # Downloaded tables not yet read by vision (ocr_status pending/failed).
        where = "download_status='ok' AND ocr_status IN ('pending','failed')"
        params: list = []
        if self.dept:
            where += " AND department_code=?"
            params.append(self.dept)
        with db.connect() as conn:
            rows = conn.execute(
                f"SELECT transmission_code, local_path FROM polling_tables "
                f"WHERE {where} ORDER BY transmission_code LIMIT ?",
                params + [self.batch_size],
            ).fetchall()
        counts = {"read": 0, "mismatch": 0, "failed": 0}
        if not rows:
            return counts

        for row in rows:
            tcode, path = row["transmission_code"], row["local_path"]
            try:
                result = vision.read_tally(path)
            except Exception as exc:  # noqa: BLE001
                with db.connect() as conn:
                    conn.execute(
                        "UPDATE polling_tables SET ocr_status='failed', "
                        "ocr_error=?, updated_at=datetime('now') WHERE transmission_code=?",
                        (str(exc)[:120], tcode),
                    )
                counts["failed"] += 1
                continue

            comp_sum, total_val, stored = 0, None, 0
            with db.connect() as conn:
                def store(field, val):
                    nonlocal stored
                    if val is None:
                        return None
                    try:
                        val = int(val)
                    except (TypeError, ValueError):
                        return None
                    conn.execute(
                        """INSERT INTO entries (transmission_code, field, ocr_value, entered_value)
                           VALUES (?,?,?,NULL)
                           ON CONFLICT(transmission_code, field) DO UPDATE SET
                             ocr_value=excluded.ocr_value""",
                        (tcode, field, val),
                    )
                    stored += 1
                    return val

                # The blank/null/unmarked band is unreadable from the full page
                # (the model returns 0 for everything). It DOES read correctly
                # from a tight, LABEL-INCLUDED crop of the totals table, so we
                # read that band separately and prefill from it. Still a guess
                # the human confirms - but a real reading, not a false zero.
                try:
                    band = vision.read_totals_band(path)
                except Exception:  # noqa: BLE001
                    band = {}
                for fixed in ("blank", "null", "unmarked"):
                    v = store(fixed, band.get(fixed))
                    if v is not None:
                        comp_sum += v
                total_val = store("total", result.get("total"))
                for cand in result.get("candidates", []):
                    idx = C.match_index(cand.get("name") or "")
                    if idx is None:
                        continue
                    v = store(C.field_for_index(idx), cand.get("votes"))
                    if v is not None:
                        comp_sum += v

                # Cross-check on the vision numbers (a hint, not a verdict).
                # comp_sum now includes candidates + blank/null/unmarked, all
                # read by vision. If it does not match the reported total, flag
                # for human review - the mismatch may be a vision misread (often)
                # or a genuine anomaly (rare). The human decides against the PDF.
                conn.execute("DELETE FROM validations WHERE transmission_code=?", (tcode,))
                flagged = 0
                if total_val is not None and stored > 1 and comp_sum != total_val:
                    flagged = 1
                    conn.execute(
                        """INSERT OR REPLACE INTO validations
                           (transmission_code, check_name, severity, expected, got, detail)
                           VALUES (?,?,?,?,?,?)""",
                        (tcode, "total_sum", "flag", total_val, comp_sum,
                         f"vision sum {comp_sum} vs total {total_val} (verify against PDF)"),
                    )
                conn.execute(
                    "UPDATE polling_tables SET ocr_status='ok', validation_status='ok', "
                    "flagged=?, updated_at=datetime('now') WHERE transmission_code=?",
                    (flagged, tcode),
                )

            counts["read"] += 1
            if flagged:
                counts["mismatch"] += 1
                self.log("validate", f"MISMATCH {tcode}: vision sum {comp_sum} vs total {total_val} (verify)")
            else:
                self.log("ocr", f"read {tcode}: {stored} numbers via vision")
        return counts

    # ---- manual trigger (non-blocking) ----
    def trigger(self) -> None:
        t = threading.Thread(target=self.run_once, daemon=True)
        t.start()

    # ---- auto loop ----
    def set_auto(self, on: bool) -> None:
        with self._lock:
            self.auto = on
        if on and (self._worker is None or not self._worker.is_alive()):
            self._stop.clear()
            self._worker = threading.Thread(target=self._loop, daemon=True)
            self._worker.start()
        elif not on:
            self._stop.set()

    def _loop(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                auto = self.auto
                dept = self.dept
            if not auto:
                break
            if self.blocked:
                self.log("block", "auto stopped: source is blocking. Wait or switch network, then resume.")
                with self._lock:
                    self.auto = False
                break
            # Stop automatically once nothing is left to advance in this scope.
            if self._scope_complete(dept):
                self.log("info", "department complete. Auto stopped.")
                with self._lock:
                    self.auto = False
                break
            self.run_once()
            self._stop.wait(self.interval)

    def _scope_complete(self, dept) -> bool:
        """True when every table in scope has finished all stages it can."""
        where = "WHERE status='11'"
        params: list = []
        if dept:
            where += " AND department_code = ?"
            params.append(dept)
        # Pending work remains if anything is still 'pending' in any stage that
        # can still progress (download pending; or downloaded-but-OCR-pending
        # when OCR is available; or OCR'd-but-validation-pending).
        ocr_clause = ("OR (download_status='ok' AND ocr_status='pending') "
                      "OR (ocr_status='ok' AND validation_status='pending')"
                      if _OCR_AVAILABLE else "")
        with db.connect() as conn:
            pending = conn.execute(
                f"""SELECT count(*) FROM polling_tables {where}
                    AND (download_status='pending' {ocr_clause})""",
                params,
            ).fetchone()[0]
        return pending == 0


class _Args:
    """Lightweight args object matching what the stage select_pending expects."""
    def __init__(self, dept=None, limit=None, retry_failed=False, status=None):
        self.dept = dept
        self.limit = limit
        self.retry_failed = retry_failed
        self.status = status


def _now() -> str:
    with db.connect() as conn:
        return conn.execute("SELECT datetime('now')").fetchone()[0]
