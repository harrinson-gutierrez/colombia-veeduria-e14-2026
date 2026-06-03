"""National consolidation: roll up every department into one report.

Because every stage writes to the same local database, consolidating is a single
query over the whole table, not a merge of per-department files.

Produces (under data/reports/):
    national_progress.csv    per-department + national totals across all stages
    confirmed_anomalies.csv  polling tables a HUMAN reviewer confirmed as a real
                             issue (reviews.verdict='real'), with their findings
                             and a link to the original tally sheet
    national_summary.html    one-page national overview, bilingual EN/ES

Wording is deliberate and non-negotiable: the final list is
"anomalies confirmed by human review that warrant an official recount /
investigation" — NOT "fraud". Fraud is a legal determination only an electoral
authority or court can make. A failed sum can be a poll-worker error, not fraud.

    python consolidate.py
"""
import csv
import html
import os

import db

REPORT_DIR = f"{db.config.DATA_DIR}/reports"


def national_progress(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT department_code, max(department_name) AS department_name,
               count(*) AS total,
               sum(download_status='ok')      AS downloaded,
               sum(download_status='not_pdf') AS not_published,
               sum(ocr_status='ok')           AS ocr_done,
               sum(validation_status IN ('ok','skipped')) AS validated,
               sum(flagged)                   AS flagged,
               sum(r.verdict='real')          AS confirmed_real,
               sum(r.verdict='ocr_error')     AS dismissed_ocr,
               sum(r.verdict IS NOT NULL)     AS reviewed
        FROM polling_tables m
        LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
        WHERE m.status='11'
        GROUP BY department_code
        ORDER BY confirmed_real DESC, flagged DESC, department_code
        """
    ).fetchall()
    return [dict(r) for r in rows]


def confirmed_anomalies(conn) -> list[dict]:
    """Polling tables a human marked as a real issue, with their findings."""
    rows = conn.execute(
        """
        SELECT m.transmission_code, m.department_code, m.department_name,
               m.municipality_code, m.station_code, m.table_number, m.pdf_url,
               r.note, r.reviewed_at,
               v.check_name, v.expected, v.got, v.detail
        FROM reviews r
        JOIN polling_tables m ON m.transmission_code = r.transmission_code
        LEFT JOIN validations v ON v.transmission_code = m.transmission_code
                                AND v.severity = 'flag'
        WHERE r.verdict = 'real'
        ORDER BY m.department_code, m.transmission_code
        """
    ).fetchall()
    return [dict(r) for r in rows]


def totals(progress: list[dict]) -> dict:
    keys = ["total", "downloaded", "not_published", "ocr_done", "validated",
            "flagged", "confirmed_real", "dismissed_ocr", "reviewed"]
    return {k: sum((r[k] or 0) for r in progress) for k in keys}


def write_csv(path: str, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def render_html(progress: list[dict], anomalies: list[dict], t: dict) -> str:
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    prog_rows = "\n".join(
        f"<tr><td>{esc(r['department_code'])}</td><td>{esc(r['department_name'])}</td>"
        f"<td>{esc(r['total'])}</td><td>{esc(r['downloaded'])}</td>"
        f"<td>{esc(r['ocr_done'])}</td><td>{esc(r['flagged'])}</td>"
        f"<td>{esc(r['reviewed'])}</td><td><b>{esc(r['confirmed_real'])}</b></td></tr>"
        for r in progress
    )
    anom_rows = "\n".join(
        f"<tr><td>{esc(a['transmission_code'])}</td>"
        f"<td>{esc(a['department_name'])} / mun {esc(a['municipality_code'])} / "
        f"st {esc(a['station_code'])} / tbl {esc(a['table_number'])}</td>"
        f"<td>{esc(a['check_name'])}</td><td>{esc(a['expected'])}</td>"
        f"<td>{esc(a['got'])}</td><td>{esc(a['note'])}</td>"
        f"<td><a href=\"{esc(a['pdf_url'])}\">PDF</a></td></tr>"
        for a in anomalies if a.get("check_name") or a.get("note")
    )

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>National Consolidation Report</title><style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:2rem;max-width:1150px;color:#1a1a1a}}
 h1{{font-size:1.4rem}} h2{{font-size:1.1rem;margin-top:2rem}}
 table{{border-collapse:collapse;width:100%;margin-top:.5rem}}
 th,td{{border:1px solid #ddd;padding:.4rem .6rem;text-align:left}} th{{background:#f5f5f5}}
 .kpi{{display:flex;gap:1.5rem;flex-wrap:wrap;margin:1rem 0}}
 .kpi div{{background:#f5f5f5;padding:1rem 1.3rem;border-radius:8px}}
 .kpi b{{font-size:1.5rem;display:block}}
 .note{{background:#fff8e1;border:1px solid #ffe082;padding:.8rem;border-radius:8px}}
 .real b{{color:#c62828}}
</style></head><body>
<h1>National Consolidation &middot; Consolidado nacional</h1>
<div class="kpi">
  <div><b>{t['total']:,}</b>Polling tables / Mesas</div>
  <div><b>{t['downloaded']:,}</b>Downloaded / Descargadas</div>
  <div><b>{t['ocr_done']:,}</b>OCR done / OCR hecho</div>
  <div><b>{t['flagged']:,}</b>Auto-flagged / Auto-marcadas</div>
  <div><b>{t['reviewed']:,}</b>Human-reviewed / Revisadas</div>
  <div class="real"><b>{t['confirmed_real']:,}</b>Confirmed anomalies / Anomal&iacute;as confirmadas</div>
</div>
<p class="note"><b>Read this carefully / L&eacute;ase con cuidado:</b> "Confirmed
anomaly" means a human reviewer judged the inconsistency real against the original
tally sheet &mdash; it warrants an <b>official recount or investigation</b>. It is
<b>NOT</b> a legal finding of fraud. "Anomal&iacute;a confirmada" significa que un
revisor la juzg&oacute; real frente al acta original y que <b>amerita reconteo o
investigaci&oacute;n oficial</b>; <b>no</b> es una determinaci&oacute;n legal de
fraude. Each row links to the original PDF as evidence.</p>

<h2>By department / Por departamento</h2>
<table><thead><tr><th>Code</th><th>Department</th><th>Total</th><th>Downloaded</th>
<th>OCR</th><th>Auto-flagged</th><th>Reviewed</th><th>Confirmed</th></tr></thead>
<tbody>{prog_rows}</tbody></table>

<h2>Confirmed anomalies (human-verified) / Anomal&iacute;as confirmadas</h2>
<table><thead><tr><th>Code</th><th>Location</th><th>Check</th><th>Expected</th>
<th>Got</th><th>Reviewer note</th><th>Evidence</th></tr></thead>
<tbody>{anom_rows or '<tr><td colspan="7">None confirmed yet / Ninguna confirmada a&uacute;n</td></tr>'}</tbody></table>
</body></html>"""


def main() -> None:
    db.init_db()
    os.makedirs(REPORT_DIR, exist_ok=True)
    with db.connect() as conn:
        progress = national_progress(conn)
        anomalies = confirmed_anomalies(conn)
    t = totals(progress)

    write_csv(f"{REPORT_DIR}/national_progress.csv", progress, [
        "department_code", "department_name", "total", "downloaded",
        "not_published", "ocr_done", "validated", "flagged",
        "reviewed", "confirmed_real", "dismissed_ocr",
    ])
    write_csv(f"{REPORT_DIR}/confirmed_anomalies.csv",
              [a for a in anomalies if a.get("check_name") or a.get("note")], [
        "transmission_code", "department_code", "department_name",
        "municipality_code", "station_code", "table_number",
        "check_name", "expected", "got", "note", "reviewed_at", "pdf_url",
    ])
    with open(f"{REPORT_DIR}/national_summary.html", "w", encoding="utf-8") as fh:
        fh.write(render_html(progress, anomalies, t))

    print(f"National consolidation written to {REPORT_DIR}/")
    print(f"  total {t['total']:,} | downloaded {t['downloaded']:,} | "
          f"ocr {t['ocr_done']:,} | flagged {t['flagged']:,} | "
          f"confirmed real {t['confirmed_real']:,}")
    print("  national_summary.html   national_progress.csv   confirmed_anomalies.csv")
    print("\nReminder: 'confirmed anomaly' = needs official investigation, NOT fraud.")


if __name__ == "__main__":
    main()
