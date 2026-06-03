"""Pipeline control panel: status of each stage and frequent errors.

    python status.py                # global summary of the 3 stages
    python status.py --errors       # latest download errors
    python status.py --dept 16      # summary filtered by department
    python status.py --flagged      # tables flagged for review (stage 3)
"""
import argparse

import db


def _bar(counts: dict, order: list[str]) -> None:
    total = sum(counts.values()) or 1
    for key in order:
        n = counts.get(key, 0)
        pct = 100 * n / total
        print(f"    {key:<10} {n:>8,}  {pct:5.1f}%")
    extra = {k: v for k, v in counts.items() if k not in order}
    for k, v in extra.items():
        print(f"    {k:<10} {v:>8,}")
    print(f"    {'TOTAL':<10} {total:>8,}")


def summary(conn, dept: str | None) -> None:
    filt = " WHERE department_code = ?" if dept else ""
    params = (dept,) if dept else ()

    def grp(col):
        rows = conn.execute(
            f"SELECT {col} k, COUNT(*) n FROM polling_tables{filt} GROUP BY {col}",
            params,
        ).fetchall()
        return {r["k"]: r["n"] for r in rows}

    title = f" (dept {dept})" if dept else ""
    print(f"\n=== STAGE 1 - Download{title} ===")
    _bar(grp("download_status"), ["pending", "ok", "failed", "not_pdf"])
    print(f"\n=== STAGE 2 - OCR{title} ===")
    _bar(grp("ocr_status"), ["pending", "ok", "failed", "skipped"])
    print(f"\n=== STAGE 3 - Validation{title} ===")
    _bar(grp("validation_status"), ["pending", "ok", "skipped"])
    flagged = conn.execute(
        f"SELECT COUNT(*) FROM polling_tables{filt}{' AND' if dept else ' WHERE'} flagged = 1",
        params,
    ).fetchone()[0]
    print(f"\n  >> Flagged for human review: {flagged:,}")


def show_errors(conn, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT transmission_code, department_code, download_attempts, download_error
        FROM polling_tables WHERE download_status = 'failed'
        ORDER BY download_attempts DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print("No downloads in 'failed' state.")
        return
    print(f"\n=== Latest {len(rows)} download failures ===")
    for r in rows:
        print(f"  {r['transmission_code']} (dept {r['department_code']}, "
              f"attempts {r['download_attempts']}): {r['download_error']}")


def show_flagged(conn, limit: int) -> None:
    rows = conn.execute(
        """
        SELECT m.transmission_code, m.department_code, m.pdf_url,
               GROUP_CONCAT(v.check_name || '=' || COALESCE(v.detail,'')) AS issues
        FROM polling_tables m LEFT JOIN validations v
          ON v.transmission_code = m.transmission_code AND v.severity = 'flag'
        WHERE m.flagged = 1
        GROUP BY m.transmission_code LIMIT ?
        """,
        (limit,),
    ).fetchall()
    if not rows:
        print("No tables flagged (stage 3 has not run yet, or everything matched).")
        return
    print(f"\n=== {len(rows)} tables flagged for review ===")
    for r in rows:
        print(f"  {r['transmission_code']} (dept {r['department_code']}): {r['issues']}")
        print(f"      {r['pdf_url']}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept")
    ap.add_argument("--errors", action="store_true")
    ap.add_argument("--flagged", action="store_true")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()

    db.init_db()
    with db.connect() as conn:
        if args.errors:
            show_errors(conn, args.limit)
        elif args.flagged:
            show_flagged(conn, args.limit)
        else:
            summary(conn, args.dept)


if __name__ == "__main__":
    main()
