"""Stage 3: internal consistency validation of each tally sheet.

Reads the OCR text stored by ocr.py, extracts the numeric rows, and checks the
sheet against itself (no external source needed):

    total_sum   : sum(candidate votes + blank + null + unmarked) == reported total
    voter_bound : reported total <= MAX_VOTERS_PER_TABLE

A failed check flags the polling table for HUMAN REVIEW. A flag is NOT proof of
fraud: handwriting OCR is error-prone, so many mismatches are OCR noise. Every
flag stores what was read (raw) so a human can verify against the original PDF.

IMPORTANT: the extraction patterns below are a STARTING POINT. They must be tuned
against real OCR output (run `python ocr.py --limit 1 --debug` and inspect the
text). Until tuned, validation marks rows it cannot parse as 'skipped', never
as flagged — so we never raise a false alarm from an unparsed sheet.

    python validate.py [--dept 16] [--limit N] [--retry-failed]
"""
import argparse
import re
import sys

import db

# Upper bound on voters registered per polling table (jurisdiction-specific).
MAX_VOTERS_PER_TABLE = 400

# Tolerance for the sum check: 0 means it must match exactly. Raising this trades
# sensitivity for fewer OCR-driven false alarms.
SUM_TOLERANCE = 0

# Label patterns -> canonical field. Tune against real OCR text.
# Each entry: (canonical_name, compiled regex capturing the integer value).
FIELD_PATTERNS = [
    ("total", re.compile(r"total\s+de\s+votos\D{0,20}(\d{1,4})", re.IGNORECASE)),
    ("blank", re.compile(r"votos?\s+en\s+blanco\D{0,20}(\d{1,4})", re.IGNORECASE)),
    ("null", re.compile(r"votos?\s+nulos?\D{0,20}(\d{1,4})", re.IGNORECASE)),
    ("unmarked", re.compile(r"no\s+marcad\w*\D{0,20}(\d{1,4})", re.IGNORECASE)),
]


def select_pending(conn, args) -> list:
    where = ["ocr_status = 'ok'"]
    where.append(
        "validation_status IN ('pending','failed')" if args.retry_failed
        else "validation_status = 'pending'"
    )
    params: list = []
    if args.dept:
        where.append("department_code = ?")
        params.append(args.dept)
    sql = (
        "SELECT transmission_code, ocr_text, pdf_url "
        f"FROM polling_tables WHERE {' AND '.join(where)} ORDER BY transmission_code"
    )
    if args.limit:
        sql += f" LIMIT {int(args.limit)}"
    return conn.execute(sql, params).fetchall()


def extract_fields(text: str) -> dict[str, int]:
    """Pull known numeric fields out of the OCR text. Returns {name: value}."""
    found: dict[str, int] = {}
    for name, pattern in FIELD_PATTERNS:
        m = pattern.search(text or "")
        if m:
            found[name] = int(m.group(1))
    return found


def validate_one(text: str) -> tuple[str, list[dict]]:
    """Return (status, findings). status is 'ok' (parsed & checked) or 'skipped'
    (not enough fields parsed to judge). findings carry severity 'flag' when a
    check fails.
    """
    fields = extract_fields(text)
    # Need at least the reported total to do anything meaningful.
    if "total" not in fields:
        return ("skipped", [])

    findings: list[dict] = []
    total = fields["total"]

    # voter_bound check
    if total > MAX_VOTERS_PER_TABLE:
        findings.append({
            "check_name": "voter_bound", "severity": "flag",
            "expected": MAX_VOTERS_PER_TABLE, "got": total,
            "detail": f"reported total {total} exceeds bound {MAX_VOTERS_PER_TABLE}",
        })

    # total_sum check: only run if we parsed the components.
    components = [v for k, v in fields.items() if k != "total"]
    if components:
        summed = sum(components)
        # Candidate votes are not individually parsed yet (needs real layout);
        # so this is a partial sum check until FIELD_PATTERNS includes candidates.
        if summed > total + SUM_TOLERANCE:
            findings.append({
                "check_name": "total_sum", "severity": "flag",
                "expected": total, "got": summed,
                "detail": f"parsed components sum {summed} exceed reported total {total}",
            })
    return ("ok", findings)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--retry-failed", action="store_true")
    args = ap.parse_args()

    db.init_db()
    with db.connect() as conn:
        rows = select_pending(conn, args)
    if not rows:
        print("Nothing pending for validation. (run OCR first: python ocr.py ...)")
        return
    print(f"Tables to validate: {len(rows):,}")

    counts = {"ok": 0, "skipped": 0, "flagged": 0}
    with db.connect() as conn:
        for i, row in enumerate(rows, 1):
            tcode = row["transmission_code"]
            try:
                status, findings = validate_one(row["ocr_text"])
                flagged = any(f["severity"] == "flag" for f in findings)
                conn.execute(
                    """UPDATE polling_tables SET validation_status=?, flagged=?,
                       updated_at=datetime('now') WHERE transmission_code=?""",
                    (status, 1 if flagged else 0, tcode),
                )
                # Replace this table's findings idempotently.
                conn.execute("DELETE FROM validations WHERE transmission_code=?", (tcode,))
                for f in findings:
                    conn.execute(
                        """INSERT OR REPLACE INTO validations
                           (transmission_code, check_name, severity, expected, got, detail)
                           VALUES (?,?,?,?,?,?)""",
                        (tcode, f["check_name"], f["severity"],
                         f.get("expected"), f.get("got"), f.get("detail")),
                    )
                counts[status] += 1
                if flagged:
                    counts["flagged"] += 1
            except Exception as exc:  # noqa: BLE001
                conn.execute(
                    """UPDATE polling_tables SET validation_status='failed',
                       updated_at=datetime('now') WHERE transmission_code=?""",
                    (tcode,),
                )
                print(f"  [FAIL] {tcode}: {exc}", file=sys.stderr)
            if i % 50 == 0 or i == len(rows):
                conn.commit()
                print(f"  {i:,}/{len(rows):,}  {counts}", flush=True)

    print(f"\nDone. {counts}")
    print("Reminder: a flag means NEEDS MANUAL REVIEW, not fraud. Verify each "
          "against its original PDF.")


if __name__ == "__main__":
    main()
