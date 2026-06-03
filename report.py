"""Generate local, self-contained reports from the SQLite database.

Fully offline: reads data/pipeline.db and writes report files under data/reports/.
Nothing is uploaded anywhere. You decide what to do with the output.

Outputs:
    data/reports/summary.html   one-page overview: per-stage progress, top
                                departments, and the flagged tables (with links
                                to each original PDF for manual review)
    data/reports/flagged.csv    every flagged polling table + its findings
    data/reports/progress.csv   per-department progress counters

    python report.py [--dept 16]
"""
import argparse
import csv
import html
import os

import db

REPORT_DIR = f"{db.config.DATA_DIR}/reports"


def fetch_progress(conn, dept: str | None) -> list[dict]:
    where = "WHERE department_code = ?" if dept else ""
    params = (dept,) if dept else ()
    rows = conn.execute(
        f"""
        SELECT department_code, max(department_name) AS department_name,
               count(*) AS total,
               sum(download_status='ok')        AS downloaded,
               sum(download_status='failed')    AS download_failed,
               sum(download_status='not_pdf')   AS not_published,
               sum(ocr_status='ok')             AS ocr_done,
               sum(validation_status='ok')      AS validated,
               sum(flagged)                     AS flagged
        FROM polling_tables {where}
        GROUP BY department_code
        ORDER BY flagged DESC, department_code
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def fetch_flagged(conn, dept: str | None) -> list[dict]:
    where = "WHERE m.flagged = 1"
    params: list = []
    if dept:
        where += " AND m.department_code = ?"
        params.append(dept)
    rows = conn.execute(
        f"""
        SELECT m.transmission_code, m.department_code, m.department_name,
               m.municipality_code, m.station_code, m.table_number, m.pdf_url,
               v.check_name, v.severity, v.expected, v.got, v.detail
        FROM polling_tables m
        LEFT JOIN validations v ON v.transmission_code = m.transmission_code
        {where}
        ORDER BY m.department_code, m.transmission_code
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def render_html(progress: list[dict], flagged: list[dict], dept: str | None) -> str:
    scope = f"Department {dept}" if dept else "All departments"
    total = sum(r["total"] for r in progress)
    downloaded = sum(r["downloaded"] or 0 for r in progress)
    flagged_n = sum(r["flagged"] or 0 for r in progress)

    def esc(x) -> str:
        return html.escape(str(x if x is not None else ""))

    prog_rows = "\n".join(
        f"<tr><td>{esc(r['department_code'])}</td><td>{esc(r['department_name'])}</td>"
        f"<td>{esc(r['total'])}</td><td>{esc(r['downloaded'])}</td>"
        f"<td>{esc(r['not_published'])}</td><td>{esc(r['ocr_done'])}</td>"
        f"<td>{esc(r['validated'])}</td><td>{esc(r['flagged'])}</td></tr>"
        for r in progress
    )
    flag_rows = "\n".join(
        f"<tr><td>{esc(r['transmission_code'])}</td>"
        f"<td>{esc(r['department_name'])} / {esc(r['municipality_code'])}</td>"
        f"<td>{esc(r['check_name'])}</td><td>{esc(r['expected'])}</td>"
        f"<td>{esc(r['got'])}</td><td>{esc(r['detail'])}</td>"
        f"<td><a href=\"{esc(r['pdf_url'])}\">PDF</a></td></tr>"
        for r in flagged if r.get("check_name")
    )

    # Bilingual EN/ES labels, self-contained, no external assets.
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tally Sheet Audit Report</title>
<style>
  body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;color:#1a1a1a;max-width:1100px}}
  h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem}}
  table{{border-collapse:collapse;width:100%;margin-top:.5rem}}
  th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}}
  th{{background:#f5f5f5}} .kpi{{display:flex;gap:2rem;margin:1rem 0}}
  .kpi div{{background:#f5f5f5;padding:1rem;border-radius:8px}}
  .kpi b{{font-size:1.6rem;display:block}}
  .note{{background:#fff8e1;border:1px solid #ffe082;padding:.8rem;border-radius:8px}}
</style></head><body>
<h1>Tally Sheet Audit Report &middot; Reporte de auditor&iacute;a de actas</h1>
<p>{esc(scope)}</p>
<div class="kpi">
  <div><b>{total:,}</b>Polling tables / Mesas</div>
  <div><b>{downloaded:,}</b>Downloaded / Descargadas</div>
  <div><b>{flagged_n:,}</b>Flagged / Marcadas</div>
</div>
<p class="note"><b>Important / Importante:</b> A flagged table means it
<b>needs manual review</b>, not that fraud occurred. Una mesa marcada significa
que <b>requiere revisi&oacute;n manual</b>, no que hubo fraude. OCR errors can look
like anomalies &mdash; verify each one against its original PDF.</p>

<h2>Progress by department / Avance por departamento</h2>
<table><thead><tr>
<th>Code</th><th>Department</th><th>Total</th><th>Downloaded</th>
<th>Not published</th><th>OCR</th><th>Validated</th><th>Flagged</th>
</tr></thead><tbody>
{prog_rows}
</tbody></table>

<h2>Flagged for review / Marcadas para revisi&oacute;n</h2>
<table><thead><tr>
<th>Code</th><th>Location</th><th>Check</th><th>Expected</th><th>Got</th>
<th>Detail</th><th>Source</th>
</tr></thead><tbody>
{flag_rows or '<tr><td colspan="7">None yet / Ninguna a&uacute;n</td></tr>'}
</tbody></table>
</body></html>"""


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dept", help="Restrict the report to one department code")
    args = ap.parse_args()

    db.init_db()
    os.makedirs(REPORT_DIR, exist_ok=True)
    with db.connect() as conn:
        progress = fetch_progress(conn, args.dept)
        flagged = fetch_flagged(conn, args.dept)

    write_csv(f"{REPORT_DIR}/progress.csv", progress, [
        "department_code", "department_name", "total", "downloaded",
        "download_failed", "not_published", "ocr_done", "validated", "flagged",
    ])
    write_csv(f"{REPORT_DIR}/flagged.csv", [r for r in flagged if r.get("check_name")], [
        "transmission_code", "department_code", "department_name",
        "municipality_code", "station_code", "table_number",
        "check_name", "severity", "expected", "got", "detail", "pdf_url",
    ])
    with open(f"{REPORT_DIR}/summary.html", "w", encoding="utf-8") as fh:
        fh.write(render_html(progress, flagged, args.dept))

    print(f"Reports written to {REPORT_DIR}/")
    print("  summary.html   open in a browser")
    print("  progress.csv   per-department counters")
    print("  flagged.csv    flagged tables + findings")


if __name__ == "__main__":
    main()
