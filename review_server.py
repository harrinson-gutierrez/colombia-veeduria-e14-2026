"""Local manual-review dashboard (standard library only, 127.0.0.1 only).

Human-in-the-loop tool: browse flagged polling tables, see the original PDF next
to what OCR read and the failed check, and record a verdict:

    real        the anomaly looks real -> worth escalating for manual recount
    ocr_error   the mismatch is an OCR misread, not a real problem
    unclear     needs a closer look

Verdicts are written to the local 'reviews' table. Nothing leaves the machine;
the server binds to localhost only and serves PDFs straight from data/forms.

    python review_server.py            # then open http://127.0.0.1:8765
    python review_server.py --port 9000 --dept 16

A flagged table means NEEDS MANUAL REVIEW, never "fraud". This tool exists
precisely so a person decides, case by case, against the original document.
"""
import argparse
import html
import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
import db
from runner import Runner

HOST = "127.0.0.1"          # localhost only, never exposed to the network
DEFAULT_PORT = 8765
DEPT_FILTER = None          # set from CLI
RUNNER = Runner()           # shared in-process pipeline runner


def queue_rows(conn, dept):
    where = "WHERE m.flagged = 1"
    params = []
    if dept:
        where += " AND m.department_code = ?"
        params.append(dept)
    return [dict(r) for r in conn.execute(
        f"""
        SELECT m.transmission_code, m.department_code, m.department_name,
               m.municipality_code, m.station_code, m.table_number,
               m.local_path, m.pdf_url, m.ocr_text,
               r.verdict, r.note
        FROM polling_tables m
        LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
        {where}
        ORDER BY (r.verdict IS NOT NULL), m.transmission_code
        """,
        params,
    ).fetchall()]


def findings_for(conn, code):
    return [dict(r) for r in conn.execute(
        """SELECT check_name, severity, expected, got, detail
           FROM validations WHERE transmission_code = ? AND severity = 'flag'""",
        (code,),
    ).fetchall()]


def save_verdict(conn, code, verdict, note):
    conn.execute(
        """INSERT INTO reviews (transmission_code, verdict, note, reviewed_at)
           VALUES (?,?,?,datetime('now'))
           ON CONFLICT(transmission_code) DO UPDATE SET
             verdict=excluded.verdict, note=excluded.note,
             reviewed_at=datetime('now')""",
        (code, verdict, note),
    )


def esc(x):
    return html.escape(str(x if x is not None else ""))


# --- Review station -------------------------------------------------------
# Fixed (non-candidate) numeric rows. (field, EN label, ES label)
FIXED_FIELDS = [
    ("blank", "Blank votes", "Votos en blanco"),
    ("null", "Null votes", "Votos nulos"),
    ("unmarked", "Unmarked", "No marcados"),
    ("total", "Reported total", "Total reportado"),
]
import candidates as CAND
CANDIDATE_FIELDS = CAND.CANDIDATE_FIELDS   # one row per master-list candidate
# OCR field -> entries.field mapping for prefill (validate.py FIELD_PATTERNS reuse).
OCR_PREFILL_FIELDS = ("total", "blank", "null", "unmarked")


# --- Hierarchical browser: Department > Municipality > Zone > Station > tables -
# Like the Registraduria site: drill down level by level, each card shows how
# many of its downloaded tables are already reviewed.

# Verdict -> (CSS class, EN label, ES label) for badges and counts.
VERDICT_META = {
    "valid": ("v-valid", "Verified", "Verificada"),
    "anomaly": ("v-anomaly", "Anomaly", "Anomalia"),
    "unclear": ("v-unclear", "Unclear", "Dudosa"),
}


def _progress_rows(conn, group_col, where, params):
    """For one hierarchy level, return per-group counts: total downloaded tables,
    how many reviewed, and a breakdown by verdict. group_col is a real column."""
    sql = f"""
        SELECT m.{group_col} AS g,
               MIN(m.department_name) AS dept_name,
               MIN(m.station_name) AS station_name,
               COUNT(*) AS total,
               SUM(CASE WHEN m.download_status='ok' THEN 1 ELSE 0 END) AS downloaded,
               SUM(CASE WHEN r.verdict IS NOT NULL THEN 1 ELSE 0 END) AS reviewed,
               SUM(CASE WHEN r.verdict='valid' THEN 1 ELSE 0 END) AS valid,
               SUM(CASE WHEN r.verdict='anomaly' THEN 1 ELSE 0 END) AS anomaly,
               SUM(CASE WHEN r.verdict='unclear' THEN 1 ELSE 0 END) AS unclear,
               SUM(CASE WHEN m.flagged=1 THEN 1 ELSE 0 END) AS flagged
        FROM polling_tables m
        LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
        {where}
        GROUP BY m.{group_col}
        ORDER BY m.{group_col}
    """
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def _level_tables(conn, where, params):
    """Leaf level: the individual tables (E14s) with their verdict."""
    sql = f"""
        SELECT m.transmission_code, m.table_number, m.download_status,
               m.flagged, r.verdict
        FROM polling_tables m
        LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
        {where}
        ORDER BY CAST(m.table_number AS INTEGER), m.table_number
    """
    return [dict(r) for r in conn.execute(sql, params).fetchall()]


def browse_page(conn, dept, mun, zone, station):
    """Render whichever level the selection points at. Empty selection -> the
    list of departments; full selection (dept+mun+zone+station) -> the tables."""
    crumbs = [("Inicio / Home", "/browse")]
    where = ["1=1"]
    params: list = []

    def add(col, val):
        where.append(f"m.{col} = ?")
        params.append(val)

    # Decide the current level from how deep the selection goes.
    if dept:
        add("department_code", dept)
    if mun:
        add("municipality_code", mun)
    if zone:
        add("zone_code", zone)
    if station:
        add("station_code", station)
    wsql = "WHERE " + " AND ".join(where)

    # Build breadcrumb links progressively.
    def crumb_url(**kw):
        q = {"dept": dept, "mun": mun, "zone": zone, "station": station}
        q.update(kw)
        parts = [f"{k}={urllib.parse.quote(v)}" for k, v in q.items() if v]
        return "/browse" + ("?" + "&".join(parts) if parts else "")

    if dept:
        dn = conn.execute(
            "SELECT department_name FROM polling_tables WHERE department_code=? LIMIT 1",
            (dept,)).fetchone()
        crumbs.append((f"{dept} {dn['department_name'] if dn else ''}".strip(),
                       crumb_url(mun="", zone="", station="")))
    if mun:
        crumbs.append((f"Mun {mun}", crumb_url(zone="", station="")))
    if zone:
        crumbs.append((f"Zona {zone}", crumb_url(station="")))
    if station:
        sn = conn.execute(
            "SELECT station_name FROM polling_tables WHERE department_code=? AND "
            "municipality_code=? AND zone_code=? AND station_code=? LIMIT 1",
            (dept, mun, zone, station)).fetchone()
        crumbs.append((f"Puesto {station} {sn['station_name'] if sn else ''}".strip(),
                       crumb_url()))

    # Choose the next level to list (or the leaf table grid).
    if not dept:
        level, group_col, child = "department", "department_code", "dept"
    elif not mun:
        level, group_col, child = "municipality", "municipality_code", "mun"
    elif not zone:
        level, group_col, child = "zone", "zone_code", "zone"
    elif not station:
        level, group_col, child = "station", "station_code", "station"
    else:
        level = "tables"

    if level == "tables":
        items = _level_tables(conn, wsql, params)
        cards = []
        for t in items:
            v = t["verdict"]
            cls, en, es = VERDICT_META.get(v, ("v-todo", "Pending", "Pendiente"))
            avail = t["download_status"] == "ok"
            flag = " flagged" if t["flagged"] else ""
            href = f"/station?code={urllib.parse.quote(t['transmission_code'])}"
            if dept:
                href += f"&dept={urllib.parse.quote(dept)}"
            cards.append(
                f'<a class="bcard table {cls}{flag}" href="{href}">'
                f'<div class="bnum">Mesa {esc(t["table_number"])}</div>'
                f'<div class="bstate">{esc(es)} / {esc(en)}</div>'
                f'{"" if avail else "<div class=bwarn>sin PDF</div>"}</a>'
            )
        body = '<div class="bgrid">' + "".join(cards) + "</div>" if cards \
            else '<p class="bempty">No hay mesas en esta seleccion.</p>'
        heading = "Mesas / Tables"
    else:
        rows = _progress_rows(conn, group_col, wsql, params)
        cards = []
        for r in rows:
            gid = r["g"]
            label = _level_label(level, gid, r)
            done, total = r["reviewed"], r["downloaded"] or r["total"]
            pct = int(100 * done / total) if total else 0
            chips = []
            for vk in ("valid", "anomaly", "unclear"):
                if r[vk]:
                    cls = VERDICT_META[vk][0]
                    chips.append(f'<span class="chip {cls}">{r[vk]}</span>')
            if r["flagged"]:
                chips.append(f'<span class="chip flag">{r["flagged"]} flag</span>')
            href = crumb_url(**{child: gid})
            cards.append(
                f'<a class="bcard" href="{href}">'
                f'<div class="blabel">{esc(label)}</div>'
                f'<div class="bbar"><div class="bfill" style="width:{pct}%"></div></div>'
                f'<div class="bcount">{done}/{total} verificadas '
                f'<span class="es">revisadas</span></div>'
                f'<div class="bchips">{"".join(chips)}</div></a>'
            )
        body = '<div class="bgrid">' + "".join(cards) + "</div>" if cards \
            else '<p class="bempty">Sin datos en este nivel.</p>'
        heading = {"department": "Departamentos", "municipality": "Municipios",
                   "zone": "Zonas", "station": "Puestos"}[level]

    crumb_html = ' <span class="csep">&rsaquo;</span> '.join(
        f'<a href="{esc(u)}">{esc(t)}</a>' for t, u in crumbs)
    return _browse_shell(heading, crumb_html, body)


def _level_label(level, gid, r):
    if level == "department":
        return f"{gid} {r['dept_name'] or ''}".strip()
    if level == "municipality":
        return f"Municipio {gid}"
    if level == "zone":
        return f"Zona {gid}"
    if level == "station":
        return f"Puesto {gid} {r['station_name'] or ''}".strip()
    return str(gid)


def _browse_shell(heading, crumb_html, body):
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>E14 - Navegar / Browse</title><style>
 *{{box-sizing:border-box}} body{{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#f4f6f9;color:#1c2230}}
 header{{background:#0d47a1;color:#fff;padding:.7rem 1.2rem;display:flex;align-items:center;gap:1rem}}
 header b{{font-size:1.05rem}} header a{{color:#cfe0ff;text-decoration:none}}
 .crumbs{{padding:.7rem 1.2rem;font-size:.9rem;background:#fff;border-bottom:1px solid #e2e5ea}}
 .crumbs a{{color:#0d47a1;text-decoration:none}} .crumbs a:hover{{text-decoration:underline}}
 .csep{{color:#aaa;margin:0 .1rem}}
 h2{{margin:1rem 1.2rem .3rem;font-size:1.1rem}}
 .bgrid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:.8rem;padding:1rem 1.2rem}}
 .bcard{{display:block;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:.8rem .9rem;
   text-decoration:none;color:#1c2230;box-shadow:0 1px 3px rgba(0,0,0,.04);transition:.15s}}
 .bcard:hover{{border-color:#0d47a1;box-shadow:0 3px 10px rgba(13,71,161,.12);transform:translateY(-1px)}}
 .blabel{{font-weight:600;margin-bottom:.5rem}}
 .bbar{{height:7px;background:#eef0f3;border-radius:99px;overflow:hidden;margin:.3rem 0}}
 .bfill{{height:100%;background:#2e7d32;border-radius:99px}}
 .bcount{{font-size:.82rem;color:#5b6472}} .bcount .es{{color:#9aa1ab;font-size:.74rem}}
 .bchips{{margin-top:.45rem;display:flex;flex-wrap:wrap;gap:.3rem}}
 .chip{{font-size:.68rem;padding:.08rem .4rem;border-radius:99px;color:#fff;font-weight:600}}
 .chip.v-valid{{background:#2e7d32}} .chip.v-anomaly{{background:#c62828}}
 .chip.v-unclear{{background:#ef6c00}} .chip.flag{{background:#6a1b9a}}
 .bcard.table{{border-left:5px solid #ccc}}
 .bcard.table.v-valid{{border-left-color:#2e7d32}} .bcard.table.v-anomaly{{border-left-color:#c62828}}
 .bcard.table.v-unclear{{border-left-color:#ef6c00}} .bcard.table.v-todo{{border-left-color:#cbd2da}}
 .bcard.table.flagged{{box-shadow:0 0 0 2px #6a1b9a inset}}
 .bnum{{font-weight:700;font-size:1rem}} .bstate{{font-size:.78rem;color:#5b6472;margin-top:.2rem}}
 .bwarn{{font-size:.7rem;color:#c62828;margin-top:.2rem}}
 .bempty{{padding:1.2rem;color:#9aa1ab}}
</style></head><body>
<header><b>E14 &middot; Navegar / Browse</b>
  <a href="/station">&rarr; Ir a la estacion / Review station</a></header>
<div class="crumbs">{crumb_html}</div>
<h2>{esc(heading)}</h2>
{body}
</body></html>"""


def next_pending_code(conn, dept, after_code):
    """Next downloaded + unreviewed table's transmission_code (ordered), or None.

    'Pending' = download_status='ok' and no row in reviews. Optionally filtered by
    department. When after_code is given, only codes strictly greater are returned,
    wrapping is left to the caller (we just look forward from after_code).
    """
    where = ["m.download_status = 'ok'", "r.transmission_code IS NULL"]
    params = []
    if dept:
        where.append("m.department_code = ?")
        params.append(dept)
    if after_code:
        where.append("m.transmission_code > ?")
        params.append(after_code)
    row = conn.execute(
        f"""SELECT m.transmission_code
            FROM polling_tables m
            LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
            WHERE {' AND '.join(where)}
            ORDER BY m.transmission_code LIMIT 1""",
        params,
    ).fetchone()
    if row:
        return row["transmission_code"]
    # No code after after_code; wrap to the first pending one (ignore after_code).
    if after_code:
        return next_pending_code(conn, dept, None)
    return None


def first_station_code(conn, dept):
    """First downloaded table (reviewed or not) for the station default view."""
    code = next_pending_code(conn, dept, None)
    if code:
        return code
    where = ["download_status = 'ok'"]
    params = []
    if dept:
        where.append("department_code = ?")
        params.append(dept)
    row = conn.execute(
        f"SELECT transmission_code FROM polling_tables WHERE {' AND '.join(where)}"
        " ORDER BY transmission_code LIMIT 1",
        params,
    ).fetchone()
    return row["transmission_code"] if row else None


def load_entries(conn, code):
    """{field: {'ocr': int|None, 'entered': int|None, 'name': str|None}}."""
    out = {}
    for r in conn.execute(
        "SELECT field, ocr_value, entered_value FROM entries WHERE transmission_code=?",
        (code,),
    ).fetchall():
        out[r["field"]] = {"ocr": r["ocr_value"], "entered": r["entered_value"]}
    # Candidate names live in a companion 'name_<field>' pseudo-row stored as text
    # in a separate entries field; we keep them under field 'name:<candidate_n>'.
    for r in conn.execute(
        "SELECT field, entered_value FROM entries WHERE transmission_code=? AND field LIKE 'name:%'",
        (code,),
    ).fetchall():
        out[r["field"]] = {"name": r["entered_value"]}
    return out


def parse_int_or_none(raw):
    """Tolerant int parse: empty/whitespace -> None; otherwise digits only."""
    if raw is None:
        return None
    s = str(raw).strip()
    if s == "":
        return None
    s = s.replace(".", "").replace(",", "").replace(" ", "")
    try:
        return int(s)
    except ValueError:
        return None


def upsert_entry(conn, code, field, ocr_value, entered_value):
    conn.execute(
        """INSERT INTO entries (transmission_code, field, ocr_value, entered_value)
           VALUES (?,?,?,?)
           ON CONFLICT(transmission_code, field) DO UPDATE SET
             entered_value=excluded.entered_value,
             ocr_value=COALESCE(excluded.ocr_value, entries.ocr_value)""",
        (code, field, ocr_value, entered_value),
    )


def page(conn, dept, current_code):
    rows = queue_rows(conn, dept)
    total = len(rows)
    done = sum(1 for r in rows if r["verdict"])
    real = sum(1 for r in rows if r["verdict"] == "real")

    if not rows:
        body = ("<p>No flagged tables yet. Run the pipeline "
                "(<code>python agent.py</code>) and validation first.</p>")
        return shell(body, total, done, real)

    current = next((r for r in rows if r["transmission_code"] == current_code), rows[0])
    code = current["transmission_code"]
    fnds = findings_for(conn, code)

    items = "".join(
        f'<a class="qitem {"active" if r["transmission_code"]==code else ""} '
        f'{r["verdict"] or "todo"}" href="/?code={esc(r["transmission_code"])}'
        f'{"&dept="+esc(dept) if dept else ""}">'
        f'{esc(r["transmission_code"])}'
        f'<span class="badge">{esc(r["verdict"] or "&middot;")}</span></a>'
        for r in rows
    )

    findings_html = "".join(
        f"<tr><td>{esc(f['check_name'])}</td><td>{esc(f['expected'])}</td>"
        f"<td>{esc(f['got'])}</td><td>{esc(f['detail'])}</td></tr>"
        for f in fnds
    ) or '<tr><td colspan="4">No flag-level findings recorded.</td></tr>'

    pdf_src = f"/pdf?code={urllib.parse.quote(code)}"
    ocr_text = esc(current["ocr_text"]) or "(no OCR text yet — install Tesseract and run ocr.py)"
    note = esc(current["note"])

    body = f"""
<div class="layout">
  <aside class="queue">
    <h3>Flagged queue</h3>
    <div class="items">{items}</div>
  </aside>
  <main class="detail">
    <h2>{esc(code)}</h2>
    <p class="loc">{esc(current['department_name'])} &middot; mun {esc(current['municipality_code'])}
       &middot; station {esc(current['station_code'])} &middot; table {esc(current['table_number'])}</p>

    <div class="cols">
      <div class="pdfbox">
        <h4>Original tally sheet</h4>
        <embed src="{pdf_src}" type="application/pdf" width="100%" height="640px">
        <p><a href="{esc(current['pdf_url'])}" target="_blank">open source URL</a></p>
      </div>
      <div class="side">
        <h4>Failed checks</h4>
        <table><thead><tr><th>Check</th><th>Expected</th><th>Got</th><th>Detail</th></tr></thead>
          <tbody>{findings_html}</tbody></table>

        <h4>What OCR read</h4>
        <pre class="ocr">{ocr_text}</pre>

        <h4>Your verdict</h4>
        <form method="POST" action="/verdict">
          <input type="hidden" name="code" value="{esc(code)}">
          {'<input type="hidden" name="dept" value="'+esc(dept)+'">' if dept else ''}
          <textarea name="note" placeholder="Optional note (why?)">{note}</textarea>
          <div class="verdicts">
            <button name="verdict" value="real" class="v-real">Real issue</button>
            <button name="verdict" value="ocr_error" class="v-ocr">OCR error</button>
            <button name="verdict" value="unclear" class="v-unclear">Unclear</button>
          </div>
        </form>
      </div>
    </div>
  </main>
</div>"""
    return shell(body, total, done, real)


def save_entries_and_review(conn, code, verdict, note, num_fields, name_fields):
    """Persist typed numbers + candidate names + the verdict, then recompute the
    total_sum validation and the flagged state. num_fields: {field: int|None};
    name_fields: {field: str}. Returns nothing.
    """
    for field, value in num_fields.items():
        upsert_entry(conn, code, field, None, value)
    for field, name in name_fields.items():
        if name and name.strip():
            upsert_entry(conn, code, f"name:{field}", None, name.strip())

    save_verdict(conn, code, verdict, note)

    # Recompute total_sum from entered components vs entered total.
    total = num_fields.get("total")
    components = [v for f, v in num_fields.items()
                 if f != "total" and v is not None]
    conn.execute(
        "DELETE FROM validations WHERE transmission_code=? AND check_name='total_sum'",
        (code,),
    )
    flagged = 0
    if total is not None and components:
        summed = sum(components)
        if summed != total:
            flagged = 1
            conn.execute(
                """INSERT OR REPLACE INTO validations
                   (transmission_code, check_name, severity, expected, got, detail)
                   VALUES (?, 'total_sum', 'flag', ?, ?, ?)""",
                (code, total, summed,
                 f"entered components sum {summed} != entered total {total}"),
            )
    conn.execute(
        """UPDATE polling_tables SET flagged=?, validation_status='ok',
           updated_at=datetime('now') WHERE transmission_code=?""",
        (flagged, code),
    )


def ocr_prefill(conn, code):
    """Pre-fill entries using the LOCAL vision model (Ollama). Reads handwritten
    candidate numbers/names + blank/null/unmarked/total into entries.ocr_value
    and the candidate name pseudo-rows. Resilient: if vision is unavailable it
    returns a clear reason without crashing. Returns {ok, filled, reason?}.

    These are GUESSES for a human to confirm against the PDF, never final values.
    """
    row = conn.execute(
        "SELECT local_path FROM polling_tables WHERE transmission_code=?",
        (code,),
    ).fetchone()
    if not row:
        return {"ok": False, "reason": "unknown code"}
    path = row["local_path"]
    if not path or not os.path.exists(path):
        return {"ok": False, "reason": "pdf not downloaded"}

    try:
        import vision
        if not vision.available():
            return {"ok": False, "reason": "vision model not available (start Ollama)"}
        # Read the full page (candidates + total) and the totals-band crop in
        # PARALLEL: they are independent calls and Ollama overlaps them, cutting
        # the wall-clock from sum(both) to ~max(both).
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_tally = pool.submit(vision.read_tally, path)
            f_band = pool.submit(vision.read_totals_band, path)
            result = f_tally.result()
            try:
                band = f_band.result()
            except Exception:  # noqa: BLE001 - band is best-effort
                band = {}
    except Exception as exc:                # noqa: BLE001 - stay resilient
        return {"ok": False, "reason": str(exc)[:120]}

    filled = 0

    def set_ocr(field, value):
        nonlocal filled
        if value is None:
            return
        try:
            value = int(value)
        except (TypeError, ValueError):
            return
        conn.execute(
            """INSERT INTO entries (transmission_code, field, ocr_value, entered_value)
               VALUES (?,?,?,NULL)
               ON CONFLICT(transmission_code, field) DO UPDATE SET
                 ocr_value=excluded.ocr_value""",
            (code, field, value),
        )
        filled += 1

    # blank/null/unmarked are unreadable from the full page (vision returns 0).
    # They DO read correctly from the totals-band crop, read in parallel above.
    # total comes from the full page.
    set_ocr("total", result.get("total"))
    for fixed in ("blank", "null", "unmarked"):
        set_ocr(fixed, band.get(fixed))

    # Candidate votes: match each read name to the fixed master list so the
    # number lands on the correct row, regardless of vision misspellings.
    import candidates as C
    for cand in result.get("candidates", []):
        idx = C.match_index(cand.get("name") or "")
        if idx is None:
            continue
        set_ocr(C.field_for_index(idx), cand.get("votes"))

    conn.execute(
        "UPDATE polling_tables SET ocr_status='ok', updated_at=datetime('now') "
        "WHERE transmission_code=?",
        (code,),
    )
    return {"ok": True, "filled": filled}


def ensure_pdf(conn, code):
    """Download this table's PDF on demand if it is missing, so browsing to any
    table just works (no need to pre-run the batch). Returns True if a valid PDF
    is on disk afterwards. Also records the new download status in the DB and
    runs vision so the numbers prefill. Safe to call on already-downloaded ones.
    """
    row = conn.execute(
        "SELECT transmission_code, pdf_url, local_path, download_status "
        "FROM polling_tables WHERE transmission_code=?", (code,)).fetchone()
    if not row:
        return False
    import download as dl
    if dl.file_is_valid_pdf(row["local_path"]):
        return True
    status, tcode, nbytes, error = dl.download_one(row)
    conn.execute(
        "UPDATE polling_tables SET download_status=?, file_bytes=?, "
        "download_error=?, updated_at=datetime('now') WHERE transmission_code=?",
        (status, nbytes or None, error or None, tcode))
    conn.commit()
    # Vision is slow (~15-40s); do NOT block page render on it. The PDF now
    # shows immediately; the user (or "Read with vision") triggers OCR.
    return status == "ok"


def station_page(conn, dept, code):
    """Render the single-table review station for `code` (or a default pick)."""
    if not code:
        code = first_station_code(conn, dept)
    if code:
        ensure_pdf(conn, code)  # download on demand so any browsed table works
    if not code:
        body = ("<div class='emptywrap'><p>No downloaded tables yet for this "
                "filter. Run the pipeline first, or pick another department.<br>"
                "<span class='es'>No hay mesas descargadas para este filtro.</span>"
                "</p></div>")
        return station_shell(body, dept, None, "0", "0")

    row = conn.execute(
        """SELECT m.transmission_code, m.department_code, m.department_name,
                  m.municipality_code, m.zone_code, m.station_code, m.table_number,
                  m.download_status, m.pdf_url, m.ocr_text,
                  r.verdict, r.note
           FROM polling_tables m
           LEFT JOIN reviews r ON r.transmission_code = m.transmission_code
           WHERE m.transmission_code = ?""",
        (code,),
    ).fetchone()
    if not row:
        body = "<div class='emptywrap'><p>Unknown code.</p></div>"
        return station_shell(body, dept, None, "0", "0")
    cur = dict(row)
    ent = load_entries(conn, code)
    verdict = cur["verdict"]

    # Counts for the header (within the dept filter).
    cwhere = "WHERE download_status='ok'"
    cparams = []
    if dept:
        cwhere += " AND department_code=?"
        cparams.append(dept)
    total_dl = conn.execute(
        f"SELECT COUNT(*) FROM polling_tables {cwhere}", cparams
    ).fetchone()[0]
    reviewed = conn.execute(
        f"""SELECT COUNT(*) FROM polling_tables m
            JOIN reviews r ON r.transmission_code=m.transmission_code {cwhere}""",
        cparams,
    ).fetchone()[0]

    pdf_src = f"/pdf?code={urllib.parse.quote(code)}"

    # Build number rows. If the reviewer hasn't entered a value yet but vision
    # produced one, pre-fill the input with it and mark it 'fromvision' (purple
    # border) so the reviewer knows to verify it against the PDF.
    def num_input(field, ocr, entered):
        if entered is not None:
            return (f'<input type="number" inputmode="numeric" class="numin" '
                    f'data-field="{field}" name="f_{field}" value="{esc(entered)}">')
        if ocr is not None:
            return (f'<input type="number" inputmode="numeric" class="numin fromvision" '
                    f'data-field="{field}" name="f_{field}" value="{esc(ocr)}" '
                    f'title="From vision - verify against the PDF">')
        return (f'<input type="number" inputmode="numeric" class="numin" '
                f'data-field="{field}" name="f_{field}" placeholder="-">')

    def num_row(field, en, es):
        e = ent.get(field, {})
        return (
            f'<tr><td class="lbl">{esc(en)}<span class="es">{esc(es)}</span></td>'
            f'<td>{num_input(field, e.get("ocr"), e.get("entered"))}</td></tr>'
        )

    fixed_html = "".join(num_row(f, en, es) for f, en, es in FIXED_FIELDS)

    cand_html = ""
    for i, field in enumerate(CANDIDATE_FIELDS):
        e = ent.get(field, {})
        name = CAND.MASTER_CANDIDATES[i]   # fixed master-list name, same every table
        cand_html += (
            f'<tr><td class="lbl candname">{esc(name)}</td>'
            f'<td>{num_input(field, e.get("ocr"), e.get("entered"))}</td></tr>'
        )

    note = esc(cur["note"])
    loc = (f"{esc(cur['department_name'])} &middot; mun {esc(cur['municipality_code'])}"
           f" &middot; zona {esc(cur['zone_code'])}"
           f" &middot; puesto {esc(cur['station_code'])}"
           f" &middot; mesa {esc(cur['table_number'])}")
    has_pdf = cur["download_status"] == "ok"
    dept_in = f'<input type="hidden" name="dept" value="{esc(dept)}">' if dept else ''

    # 5-step strip; JS lights steps as the reviewer progresses. Initial active
    # depends on whether a verdict already exists.
    steps = [
        ("1", "Download", "Descargar"),
        ("2", "View", "Ver"),
        ("3", "Analyze", "Analizar"),
        ("4", "Verdict", "Veredicto"),
        ("5", "Report", "Reporte"),
    ]
    strip = "".join(
        f'<div class="step" id="step{n}"><span class="num">{n}</span>'
        f'<span class="slab">{en}<em>{es}</em></span></div>'
        f'{"<span class=arrow>&rsaquo;</span>" if n != "5" else ""}'
        for n, en, es in steps
    )

    verdict_badge = (
        f'<span class="vbadge v-{esc(verdict)}">{esc(verdict)}</span>' if verdict else ""
    )

    body = f"""
<div class="strip" data-haspdf="{1 if has_pdf else 0}" data-verdict="{esc(verdict)}">{strip}</div>
<div class="station">
  <section class="pdfpane">
    <div class="panehead"><b>{esc(code)}</b> {verdict_badge}
      <span class="loc">{loc}</span></div>
    {'<embed src="'+pdf_src+'" type="application/pdf" class="pdfembed">' if has_pdf
       else '<div class="nopdf">PDF not downloaded yet / PDF no descargado</div>'}
  </section>
  <section class="numpane">
    <form id="entryForm" method="POST" action="/entries">
      <input type="hidden" name="code" value="{esc(code)}">
      {dept_in}
      <h3>Numbers <span class="es">Numeros</span>
        <button type="button" id="revisionBtn" class="rebtn">Read with vision / Leer con vision</button>
      </h3>
      <div id="visionBadge" class="vbadge"></div>
      {'<div class="totalsband"><div class="tbcap">Totals band (read these by eye) / '
       'Banda de totales (lea a ojo): blanco / nulos / no marcados / suma</div>'
       '<img src="/totals?code='+urllib.parse.quote(code)+'" alt="totals band" '
       'loading="lazy" onerror="this.parentNode.style.display=&quot;none&quot;"></div>'
       if has_pdf else ''}
      <table class="numgrid">
        <thead><tr><th>Field / Campo</th><th>Count / Conteo</th></tr></thead>
        <tbody>
          <tr class="grphdr"><td colspan="2">Candidates / Candidatos</td></tr>
          {cand_html}
          <tr class="grphdr"><td colspan="2">Other / Otros</td></tr>
          {fixed_html}
        </tbody>
      </table>

      <div id="sumcheck" class="sumcheck">&nbsp;</div>

      <div class="autorev">
        <label><input type="checkbox" id="autoRevBox"> Auto-review (10s) / Auto-revision</label>
        <div id="arbar" class="arbar"><div id="arfill" class="arfill"></div></div>
        <span id="arnote" class="arnote"></span>
      </div>

      <h3>Verdict <span class="es">Veredicto</span></h3>
      <textarea name="note" placeholder="Optional note / Nota opcional">{note}</textarea>
      <div class="verdicts">
        <button type="submit" name="verdict" value="valid" class="v-valid">Valid<span>Valida</span></button>
        <button type="submit" name="verdict" value="anomaly" class="v-anomaly">Anomaly<span>Anomalia</span></button>
        <button type="submit" name="verdict" value="unclear" class="v-unclear">Unclear<span>Dudosa</span></button>
      </div>
    </form>
  </section>
</div>"""
    prefilled = any(
        (v.get("ocr") is not None or v.get("entered") is not None)
        for v in ent.values()
    )
    return station_shell(body, dept, code, str(total_dl), str(reviewed), prefilled)


def station_shell(body, dept, code, total_dl, reviewed, prefilled=False):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Review Station</title><style>
 *{{box-sizing:border-box}}
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;color:#1a1a1a;background:#f4f5f7}}
 header{{background:#11161d;color:#fff;padding:.7rem 1.1rem;display:flex;gap:1.1rem;align-items:center}}
 header b{{font-size:1.05rem}} header .k{{opacity:.85;font-size:.85rem}}
 .spacer{{flex:1}}
 .es{{display:block;font-size:.72em;opacity:.62;font-weight:400}}
 .pipe{{font-size:.78rem;opacity:.8;font-family:ui-monospace,monospace}}
 .ctl{{background:#1e88e5;color:#fff;border:0;padding:.42rem .8rem;border-radius:6px;cursor:pointer;font-size:.85rem}}
 .ctl:disabled{{opacity:.5;cursor:default}}
 .auto{{font-size:.82rem;display:flex;gap:.3rem;align-items:center;cursor:pointer}}
 .auto select{{background:#222a35;color:#fff;border:1px solid #3a4654;border-radius:4px;padding:.22rem}}
 .bar{{height:4px;background:#dfe3e8}} .barfill{{height:100%;width:0;background:#1e88e5;transition:width .4s}}
 .blockbanner{{background:#ffebee;border-bottom:1px solid #ef9a9a;color:#b71c1c;padding:.6rem 1.1rem;font-size:.9rem}}
 .note{{background:#fff8e1;border-bottom:1px solid #ffe082;padding:.5rem 1.1rem;font-size:.88rem}}

 /* 5-step progress strip */
 .strip{{display:flex;align-items:center;gap:.2rem;padding:.7rem 1.1rem;background:#fff;
   border-bottom:1px solid #e2e5ea;flex-wrap:wrap}}
 .step{{display:flex;align-items:center;gap:.5rem;padding:.4rem .8rem;border-radius:999px;
   background:#eceef1;color:#5a6573;font-size:.82rem;transition:.25s}}
 .step .num{{display:inline-grid;place-items:center;width:1.4rem;height:1.4rem;border-radius:50%;
   background:#c7cdd6;color:#fff;font-weight:700;font-size:.78rem}}
 .step .slab{{line-height:1.05}} .step .slab em{{display:block;font-style:normal;font-size:.7em;opacity:.7}}
 .step.done{{background:#e3f6e9;color:#1b6e3a}} .step.done .num{{background:#2e9e54}}
 .step.active{{background:#e3f0ff;color:#0d4ea8;box-shadow:0 0 0 2px #9ec4ff inset}}
 .step.active .num{{background:#1e88e5}}
 .arrow{{color:#c0c6cf;font-size:1.1rem}}

 /* station split */
 .station{{display:flex;gap:1rem;padding:1rem;height:calc(100vh - 168px)}}
 .pdfpane{{flex:0 0 60%;display:flex;flex-direction:column;background:#fff;border:1px solid #e2e5ea;
   border-radius:10px;overflow:hidden}}
 .numpane{{flex:1;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:.9rem 1.1rem;
   overflow:auto}}
 .panehead{{padding:.55rem .8rem;border-bottom:1px solid #eef0f3;display:flex;align-items:center;gap:.6rem}}
 .panehead .loc{{color:#7a828c;font-size:.8rem;margin-left:auto}}
 .pdfembed{{flex:1;width:100%;border:0}}
 .nopdf{{flex:1;display:grid;place-items:center;color:#9aa1ab}}
 .vbadge{{font-size:.72rem;padding:.1rem .5rem;border-radius:999px;color:#fff;text-transform:uppercase}}
 .totalsband{{margin:.4rem 0 .8rem;border:2px solid #b07cff;border-radius:8px;overflow:hidden;background:#faf7ff}}
 .totalsband .tbcap{{font-size:.7rem;color:#6a3fb0;padding:.25rem .5rem;background:#f0e8ff;font-weight:600}}
 .totalsband img{{display:block;width:100%;height:auto;max-height:220px;object-fit:contain;background:#fff}}
 .vbadge.v-valid{{background:#2e9e54}} .vbadge.v-anomaly{{background:#e53935}} .vbadge.v-unclear{{background:#fb8c00}}

 .numpane h3{{font-size:.92rem;margin:.6rem 0 .4rem}}
 .numgrid{{border-collapse:collapse;width:100%;font-size:.86rem}}
 .numgrid th,.numgrid td{{border:1px solid #e7e9ed;padding:.3rem .45rem;text-align:left}}
 .numgrid th{{background:#f6f7f9;font-size:.78rem;color:#5a6573}}
 .numgrid .grphdr td{{background:#f0f6ff;font-weight:600;color:#34557e;font-size:.76rem;text-transform:uppercase;letter-spacing:.03em}}
 .numgrid .lbl{{width:58%}} .numgrid .lbl .es{{font-size:.7em}}
 .numin{{width:100%;padding:.3rem .4rem;border:1px solid #cfd4db;border-radius:5px;font-size:.9rem;text-align:right}}
 .numin::placeholder{{color:#b9bfc8;font-style:italic}}
 .namein{{width:100%;padding:.3rem .4rem;border:1px solid #e0e3e8;border-radius:5px;font-size:.82rem;background:#fafbfc}}
 .sumcheck{{margin:.7rem 0;padding:.55rem .7rem;border-radius:7px;font-weight:600;font-size:.9rem;
   background:#f0f2f5;color:#5a6573}}
 .sumcheck.ok{{background:#e3f6e9;color:#1b6e3a}} .sumcheck.bad{{background:#fde7e7;color:#b3261e}}
 .rebtn{{font-size:.7rem;font-weight:400;background:#6750a4;color:#fff;border:0;border-radius:5px;padding:.25rem .5rem;cursor:pointer;margin-left:.5rem}}
 .vbadge{{font-size:.78rem;color:#6750a4;min-height:1.1rem;margin:.2rem 0}}
 .vbadge.reading{{background:#ede7f6;color:#4527a0;font-weight:600;padding:.35rem .6rem;
   border-radius:6px;border:1px solid #b39ddb;animation:vpulse 1.2s ease-in-out infinite}}
 @keyframes vpulse{{0%,100%{{opacity:1}}50%{{opacity:.55}}}}
 .fromvision{{border:2px solid #d32f2f !important;background:#fdecea}}
 .confirmed{{border:2px solid #1b6e3a !important;background:#fff}}
 .sumcheck.empty{{background:#eee;color:#666}}
 .candname{{font-size:.82rem;font-weight:500}}
 .autorev{{margin:.6rem 0;padding:.5rem .7rem;background:#eef2f7;border-radius:7px;font-size:.85rem}}
 .autorev label{{display:flex;gap:.4rem;align-items:center;cursor:pointer;font-weight:600}}
 .arbar{{height:6px;background:#d4dae2;border-radius:3px;margin:.4rem 0;overflow:hidden}}
 .arfill{{height:100%;width:0;background:#1e88e5}}
 .arnote{{color:#555;font-size:.78rem}}
 textarea{{width:100%;height:54px;margin:.3rem 0 .6rem;border:1px solid #cfd4db;border-radius:6px;padding:.4rem;font:inherit}}
 .verdicts{{display:flex;gap:.5rem}}
 .verdicts button{{flex:1;padding:.6rem .3rem;border:0;border-radius:7px;color:#fff;cursor:pointer;
   font-size:.88rem;font-weight:600}}
 .verdicts button span{{display:block;font-size:.72em;opacity:.85;font-weight:400}}
 .v-valid{{background:#2e9e54}} .v-anomaly{{background:#e53935}} .v-unclear{{background:#fb8c00}}
 .emptywrap{{padding:2rem}}

 /* live feed (kept, bottom-right, dark) */
 .feed{{position:fixed;right:1rem;bottom:1rem;width:340px;max-height:40vh;background:#0d1117;color:#c9d1d9;
   border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.3);font:12px/1.45 ui-monospace,monospace;overflow:hidden;z-index:50}}
 .feedhead{{background:#161b22;padding:.4rem .7rem;font-weight:600;color:#8b949e;
   display:flex;align-items:center;gap:.5rem}}
 .feedhead .ftog{{margin-left:auto;cursor:pointer;background:none;border:0;color:#8b949e;
   font-size:14px;line-height:1;padding:0 .2rem}}
 .feedhead .ftog:hover{{color:#fff}}
 #feedlist{{padding:.3rem .5rem;overflow:auto;max-height:34vh}}
 .feed.collapsed{{display:none}}
 .feedshow{{position:fixed;right:1rem;bottom:1rem;z-index:50;cursor:pointer;
   background:#161b22;color:#8b949e;border:1px solid #30363d;border-radius:8px;
   padding:.35rem .7rem;font:12px/1 ui-monospace,monospace;box-shadow:0 4px 20px rgba(0,0,0,.3)}}
 .feedshow:hover{{color:#fff}}
 .ev{{padding:.12rem 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 .ev.download{{color:#79c0ff}} .ev.ocr{{color:#d2a8ff}} .ev.validate{{color:#ffa657}}
 .ev.block{{color:#ff7b72;font-weight:600}} .ev.info{{color:#8b949e}}
</style></head><body>
<header><b>Review Station</b>
  <a href="/browse" style="color:#cfe0ff;text-decoration:none;font-size:.85rem">&#9776; Navegar / Browse</a>
  <label class="auto">Dept
    <select id="deptSel"><option value="">All</option></select>
  </label>
  <span class="k">Downloaded: {esc(total_dl)}</span>
  <span class="k">Reviewed: {esc(reviewed)}</span>
  <span class="spacer"></span>
  <span id="prog" class="pipe"></span>
  <span id="pipe" class="pipe">pipeline: loading...</span>
  <button id="runBtn" class="ctl">Run batch now</button>
  <label class="auto"><input type="checkbox" id="autoBox"> Auto (whole dept)</label>
</header>
<div id="bar" class="bar"><div id="barfill" class="barfill"></div></div>
<div id="blockbanner" class="blockbanner" style="display:none">
  Source is blocking requests (403/429). Auto-processing paused. Wait a few
  minutes or switch network, then press Run / Auto again.</div>
<div class="note"><b>Flagged = needs manual review, not fraud.</b> OCR errors look
  like anomalies. Decide each case against the original tally sheet.</div>
{body}
<div id="feed" class="feed"><div class="feedhead"><span>Live activity &middot; Actividad en vivo</span>
  <button type="button" class="ftog" id="feedHide" title="Hide / Ocultar">&minus;</button></div>
  <div id="feedlist"></div></div>
<button type="button" id="feedShow" class="feedshow" style="display:none">Live activity &#9650;</button>
<script>
const CODE={json.dumps(code)};
const DEPT={json.dumps(dept or "")};
const PREFILLED={json.dumps(bool(prefilled))};
// ---- 5-step strip logic ----
const strip=document.querySelector('.strip');
function setSteps(active, doneUpTo){{
  for(let n=1;n<=5;n++){{
    const el=document.getElementById('step'+n); if(!el) continue;
    el.classList.toggle('done', n<=doneUpTo);
    el.classList.toggle('active', n===active);
  }}
}}
if(strip){{
  const hasPdf=strip.dataset.haspdf==='1';
  const verdict=strip.dataset.verdict||'';
  // 1 Download done if pdf present; 2 View active once embedded; 3 Analyze on input;
  // 4 Verdict on click; 5 Report after save (next load).
  let doneUpTo = hasPdf?2:0;          // download + view considered complete
  let active = verdict?4:(hasPdf?3:1); // if already reviewed, sit on verdict
  if(verdict) doneUpTo=4;
  setSteps(active, doneUpTo);
}}
// ---- live sum check ----
const sumEl=document.getElementById('sumcheck');
function curVal(inp){{
  const v=(inp.value||'').trim();
  if(v==='') return 0;
  const n=parseInt(v.replace(/[.,\\s]/g,''),10);
  return isNaN(n)?0:n;
}}
function recompute(){{
  if(!sumEl) return;
  let comp=0, total=0, totalSet=false, anyComp=false;
  document.querySelectorAll('.numin').forEach(inp=>{{
    const f=inp.dataset.field;
    const has=inp.value.trim()!=='';
    if(f==='total'){{ if(has){{total=curVal(inp);totalSet=true;}} }}
    else {{ if(has) anyComp=true; comp+=curVal(inp); }}
  }});
  // Empty / not enough data -> neutral, no misleading green.
  if(!totalSet || !anyComp){{
    sumEl.className='sumcheck empty';
    sumEl.textContent='Sin datos / No data yet - enter the numbers from the PDF';
    return;
  }}
  if(strip && strip.dataset.verdict===''){{ setSteps(3, 2); }}
  const unconfirmed=document.querySelectorAll('.numin.fromvision').length;
  const diff=comp-total;
  let tag='';
  if(unconfirmed>0) tag=' - '+unconfirmed+' unverified vision values (red) / sin verificar';
  if(diff===0){{
    sumEl.className=unconfirmed>0?'sumcheck empty':'sumcheck ok';
    sumEl.textContent=(unconfirmed>0?'Provisional ':'')+'Cuadra / Matches ('+comp+' = '+total+')'+tag;
  }}else{{
    sumEl.className='sumcheck bad';
    sumEl.textContent='No cuadra / Mismatch (sum '+comp+' vs total '+total+', diff='+diff+')'+tag;
  }}
}}
// Editing a vision-prefilled field marks it confirmed (purple -> green).
document.querySelectorAll('.numin, .namein').forEach(i=>i.addEventListener('input',()=>{{
  if(i.classList.contains('fromvision')){{ i.classList.remove('fromvision'); i.classList.add('confirmed'); }}
}}));
document.querySelectorAll('.numin').forEach(i=>i.addEventListener('input',recompute));
recompute();
const ef=document.getElementById('entryForm');
if(ef) ef.addEventListener('submit',()=>{{ if(strip) setSteps(4,4); }});

// ---- prefill with the LOCAL vision model (best-effort, shows progress) ----
async function prefill(force){{
  if(!CODE) return;
  const badge=document.getElementById('visionBadge');
  // Skip auto-run if this table already has stored guesses (unless forced).
  if(!force && PREFILLED){{ if(badge) badge.textContent=''; return; }}
  // Live timer so the ~1 minute vision read never looks frozen.
  let secs=0;
  if(badge){{
    badge.classList.add('reading');
    badge.textContent='Reading with local vision (~1 min)... / Leyendo con vision local... 0s';
  }}
  const timer=setInterval(()=>{{ secs++; if(badge)
    badge.textContent='Reading with local vision (~1 min)... / Leyendo con vision local... '+secs+'s'; }},1000);
  try{{
    const r=await (await fetch('/ocr_prefill?code='+encodeURIComponent(CODE))).json();
    clearInterval(timer);
    if(badge) badge.classList.remove('reading');
    if(r && r.ok && r.filled){{
      if(badge) badge.textContent='Read '+r.filled+' values in '+secs+'s, loading...';
      const u=new URL(location.href); location.replace(u.pathname+u.search);
    }}else if(r && !r.ok){{
      if(badge) badge.textContent='Vision unavailable: '+(r.reason||'')+'. Type the numbers from the PDF.';
    }}else{{
      if(badge) badge.textContent='Vision read nothing readable. Type the numbers from the PDF.';
    }}
  }}catch(e){{ clearInterval(timer); if(badge){{ badge.classList.remove('reading');
    badge.textContent='Vision error. Type the numbers from the PDF.'; }} }}
}}
const reBtn=document.getElementById('revisionBtn');
if(reBtn) reBtn.onclick=()=>prefill(true);

// ---- header controls (reused) ----
const pipe=document.getElementById('pipe');
const prog=document.getElementById('prog');
const barfill=document.getElementById('barfill');
const runBtn=document.getElementById('runBtn');
const autoBox=document.getElementById('autoBox');
const deptSel=document.getElementById('deptSel');
async function loadDepts(){{
  const ds=await (await fetch('/depts')).json();
  for(const d of ds){{
    const o=document.createElement('option');
    o.value=d.code;
    o.textContent=`${{d.code}} ${{d.name}} (${{d.downloaded}}/${{d.total}})`;
    if(d.code===DEPT) o.selected=true;
    deptSel.appendChild(o);
  }}
}}
let lastProcessed=null;
async function poll(){{
  try{{
    const s=await (await fetch('/state')).json();
    autoBox.checked=s.auto;
    const p=s.progress||{{}};
    const total=p.total||0, done=(p.downloaded||0), ocr=(p.ocr_done||0);
    prog.textContent=total?`dept: dl ${{done}}/${{total}} | read ${{ocr}} | flagged ${{p.flagged||0}}`:'';
    barfill.style.width=total?(100*done/total)+'%':'0';
    let txt=s.running?'running batch (download+vision)...':(s.auto?'auto on':'idle');
    if(s.last_error) txt+=' | ERROR';
    pipe.textContent='pipeline: '+txt;
    runBtn.disabled=s.running;
    blockbanner.style.display=s.blocked?'block':'none';
    // Auto-refresh the station when new tables get processed, UNLESS the user
    // is busy (editing inputs or in the middle of an auto-review countdown).
    const busy = document.activeElement && /INPUT|TEXTAREA/.test(document.activeElement.tagName);
    const arOn = autoRevBox && autoRevBox.checked;
    if(lastProcessed!==null && ocr!==lastProcessed && !s.running && !busy && !arOn && !CODE){{
      location.reload();
    }}
    lastProcessed=ocr;
  }}catch(e){{pipe.textContent='pipeline: (server stopped)';}}
}}
const blockbanner=document.getElementById('blockbanner');
runBtn.onclick=async()=>{{runBtn.disabled=true;await fetch('/run',{{method:'POST'}});poll();}};
autoBox.onchange=async()=>{{await fetch('/auto',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'on='+(autoBox.checked?'1':'0')}});poll();}};
deptSel.onchange=async()=>{{
  await fetch('/dept',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'dept='+encodeURIComponent(deptSel.value)}});
  location.href='/station'+(deptSel.value?('?dept='+encodeURIComponent(deptSel.value)):'');
}};
// ---- live feed ----
const feedlist=document.getElementById('feedlist');
let lastEv=0;
async function pollEvents(){{
  try{{
    const evs=await (await fetch('/events?since='+lastEv)).json();
    for(const e of evs){{
      lastEv=e.id;
      const d=document.createElement('div');
      d.className='ev '+e.kind; d.textContent=e.msg; feedlist.appendChild(d);
    }}
    while(feedlist.children.length>200) feedlist.removeChild(feedlist.firstChild);
    if(evs.length) feedlist.scrollTop=feedlist.scrollHeight;
  }}catch(e){{}}
}}
// ---- live activity show/hide (remembered) ----
const FEED_KEY='feed_hidden';
const feedEl=document.getElementById('feed');
const feedShow=document.getElementById('feedShow');
function applyFeed(){{
  const hidden=localStorage.getItem(FEED_KEY)==='1';
  feedEl.classList.toggle('collapsed',hidden);
  feedShow.style.display=hidden?'block':'none';
}}
document.getElementById('feedHide').onclick=()=>{{localStorage.setItem(FEED_KEY,'1');applyFeed();}};
feedShow.onclick=()=>{{localStorage.setItem(FEED_KEY,'0');applyFeed();
  feedlist.scrollTop=feedlist.scrollHeight;}};
applyFeed();
// ---- auto-review mode: scan tables, 10s each, advance WITHOUT a verdict ----
// If you act (a verdict button), it records and jumps now. If 10s pass with no
// action, it advances to the next table leaving this one unreviewed.
const AR_KEY='autoreview_on';
const autoRevBox=document.getElementById('autoRevBox');
const arfill=document.getElementById('arfill');
const arnote=document.getElementById('arnote');
let arTimer=null, arStart=0, arPaused=false;
const AR_MS=10000;
function arStop(){{ if(arTimer){{clearInterval(arTimer);arTimer=null;}} if(arfill) arfill.style.width='0'; }}
async function arAdvance(){{
  arStop();
  if(arnote) arnote.textContent='advancing... / avanzando...';
  try{{
    const u='/next?code='+encodeURIComponent(CODE||'')+(DEPT?('&dept='+encodeURIComponent(DEPT)):'');
    const r=await (await fetch(u)).json();
    if(r && r.next){{ location.href='/station?code='+encodeURIComponent(r.next)+(DEPT?('&dept='+encodeURIComponent(DEPT)):''); }}
    else {{ if(arnote) arnote.textContent='no more pending tables / no hay mas mesas'; if(autoRevBox) autoRevBox.checked=false; localStorage.setItem(AR_KEY,'0'); }}
  }}catch(e){{ if(arnote) arnote.textContent='error advancing'; }}
}}
function arBegin(){{
  arStop(); arStart=Date.now(); arPaused=false;
  if(arnote) arnote.textContent='Auto-review on - 10s, or act now / actua ahora';
  arTimer=setInterval(()=>{{
    if(arPaused) return;
    const el=Date.now()-arStart;
    if(arfill) arfill.style.width=Math.min(100,100*el/AR_MS)+'%';
    if(el>=AR_MS) arAdvance();
  }},100);
}}
function arPause(){{ arPaused=true; if(arnote) arnote.textContent='paused (you are reviewing) / en pausa'; }}
if(autoRevBox){{
  // pause the countdown the moment you interact with the table
  ['mousedown','keydown','input'].forEach(ev=>document.querySelector('.detail') &&
    document.querySelector('.detail').addEventListener(ev,()=>{{ if(autoRevBox.checked) arPause(); }}));
  autoRevBox.onchange=()=>{{
    localStorage.setItem(AR_KEY, autoRevBox.checked?'1':'0');
    if(autoRevBox.checked) arBegin(); else arStop();
  }};
  // persist across the auto-advance page reloads
  if(localStorage.getItem(AR_KEY)==='1'){{ autoRevBox.checked=true; setTimeout(arBegin, 1200); }}
}}

loadDepts(); setInterval(poll,2000); setInterval(pollEvents,1000); poll(); pollEvents(); prefill();
</script>
</body></html>"""


def shell(body, total, done, real):
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Tally Review</title><style>
 body{{font:14px/1.5 system-ui,sans-serif;margin:0;color:#1a1a1a}}
 header{{background:#1a1a1a;color:#fff;padding:.8rem 1.2rem;display:flex;gap:1.4rem;align-items:center}}
 header b{{font-size:1.1rem}} header .k{{opacity:.85}}
 .spacer{{flex:1}}
 .pipe{{font-size:.8rem;opacity:.8;font-family:ui-monospace,monospace}}
 .ctl{{background:#1e88e5;color:#fff;border:0;padding:.45rem .8rem;border-radius:6px;cursor:pointer}}
 .ctl:disabled{{opacity:.5;cursor:default}}
 .auto{{font-size:.85rem;display:flex;gap:.3rem;align-items:center;cursor:pointer}}
 .auto select{{background:#333;color:#fff;border:1px solid #555;border-radius:4px;padding:.2rem}}
 .bar{{height:4px;background:#eee}} .barfill{{height:100%;width:0;background:#1e88e5;transition:width .4s}}
 .blockbanner{{background:#ffebee;border-bottom:1px solid #ef9a9a;color:#b71c1c;padding:.6rem 1.2rem;font-size:.9rem}}
 .feed{{position:fixed;right:1rem;bottom:1rem;width:380px;max-height:46vh;background:#0d1117;color:#c9d1d9;
   border-radius:8px;box-shadow:0 4px 20px rgba(0,0,0,.3);font:12px/1.45 ui-monospace,monospace;overflow:hidden;z-index:50}}
 .feedhead{{background:#161b22;padding:.4rem .7rem;font-weight:600;color:#8b949e;
   display:flex;align-items:center;gap:.5rem}}
 .feedhead .ftog{{margin-left:auto;cursor:pointer;background:none;border:0;color:#8b949e;
   font-size:14px;line-height:1;padding:0 .2rem}}
 .feedhead .ftog:hover{{color:#fff}}
 #feedlist{{padding:.3rem .5rem;overflow:auto;max-height:40vh}}
 .feed.collapsed{{display:none}}
 .feedshow{{position:fixed;right:1rem;bottom:1rem;z-index:50;cursor:pointer;
   background:#161b22;color:#8b949e;border:1px solid #30363d;border-radius:8px;
   padding:.35rem .7rem;font:12px/1 ui-monospace,monospace;box-shadow:0 4px 20px rgba(0,0,0,.3)}}
 .feedshow:hover{{color:#fff}}
 .ev{{padding:.12rem 0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}}
 .ev.download{{color:#79c0ff}} .ev.ocr{{color:#d2a8ff}} .ev.validate{{color:#ffa657}}
 .ev.block{{color:#ff7b72;font-weight:600}} .ev.info{{color:#8b949e}}
 .note{{background:#fff8e1;border-bottom:1px solid #ffe082;padding:.5rem 1.2rem;font-size:.9rem}}
 .layout{{display:flex;height:calc(100vh - 92px)}}
 .queue{{width:230px;border-right:1px solid #e0e0e0;overflow:auto;padding:.5rem}}
 .queue h3{{font-size:.8rem;text-transform:uppercase;color:#666;margin:.4rem}}
 .items{{display:flex;flex-direction:column}}
 .qitem{{display:flex;justify-content:space-between;padding:.4rem .6rem;text-decoration:none;
   color:#222;border-radius:6px;font-size:.82rem}}
 .qitem:hover{{background:#f0f0f0}} .qitem.active{{background:#e3f2fd;font-weight:600}}
 .qitem.real{{border-left:3px solid #e53935}} .qitem.ocr_error{{border-left:3px solid #43a047}}
 .qitem.unclear{{border-left:3px solid #fb8c00}} .qitem.todo{{border-left:3px solid #ccc}}
 .badge{{opacity:.6;font-size:.7rem}}
 .detail{{flex:1;overflow:auto;padding:1rem 1.4rem}}
 .loc{{color:#666}}
 .cols{{display:flex;gap:1.2rem;align-items:flex-start}}
 .pdfbox{{flex:1.4}} .side{{flex:1;min-width:300px}}
 table{{border-collapse:collapse;width:100%;font-size:.85rem}}
 th,td{{border:1px solid #ddd;padding:.35rem .5rem;text-align:left}} th{{background:#f5f5f5}}
 .ocr{{background:#fafafa;border:1px solid #eee;padding:.6rem;max-height:200px;overflow:auto;
   white-space:pre-wrap;font-size:.78rem}}
 textarea{{width:100%;height:60px;margin:.4rem 0}}
 .verdicts{{display:flex;gap:.5rem}} .verdicts button{{flex:1;padding:.6rem;border:0;border-radius:6px;
   color:#fff;cursor:pointer;font-size:.85rem}}
 .v-real{{background:#e53935}} .v-ocr{{background:#43a047}} .v-unclear{{background:#fb8c00}}
</style></head><body>
<header><b>Tally Review</b>
  <label class="auto">Dept
    <select id="deptSel"><option value="">All</option></select>
  </label>
  <span class="k">Flagged: {total}</span>
  <span class="k">Reviewed: {done}</span>
  <span class="spacer"></span>
  <span id="prog" class="pipe"></span>
  <span id="pipe" class="pipe">pipeline: loading...</span>
  <button id="runBtn" class="ctl">Run batch now</button>
  <label class="auto"><input type="checkbox" id="autoBox"> Auto (whole dept)</label>
</header>
<div id="bar" class="bar"><div id="barfill" class="barfill"></div></div>
<div id="blockbanner" class="blockbanner" style="display:none">
  Source is blocking requests (403/429). Auto-processing paused. Wait a few
  minutes or switch network, then press Run / Auto again.</div>
<div id="feed" class="feed"><div class="feedhead"><span>Live activity &middot; Actividad en vivo</span>
  <button type="button" class="ftog" id="feedHide" title="Hide / Ocultar">&minus;</button></div>
  <div id="feedlist"></div></div>
<button type="button" id="feedShow" class="feedshow" style="display:none">Live activity &#9650;</button>
<div class="note"><b>Flagged = needs manual review, not fraud.</b> OCR errors look
like anomalies. Decide each case against the original tally sheet.</div>
{body}
<script>
const pipe=document.getElementById('pipe');
const prog=document.getElementById('prog');
const barfill=document.getElementById('barfill');
const runBtn=document.getElementById('runBtn');
const autoBox=document.getElementById('autoBox');
const deptSel=document.getElementById('deptSel');
let lastBatches=null;
async function loadDepts(){{
  const ds=await (await fetch('/depts')).json();
  for(const d of ds){{
    const o=document.createElement('option');
    o.value=d.code;
    o.textContent=`${{d.code}} ${{d.name}} (${{d.downloaded}}/${{d.total}})`;
    deptSel.appendChild(o);
  }}
}}
async function poll(){{
  try{{
    const s=await (await fetch('/state')).json();
    autoBox.checked=s.auto;
    if(deptSel.value!==(s.dept||'')) deptSel.value=s.dept||'';
    const p=s.progress||{{}};
    const total=p.total||0, done=(p.downloaded||0);
    prog.textContent=total?`dept: dl ${{done}}/${{total}} | ocr ${{p.ocr_done||0}} | flagged ${{p.flagged||0}}`:'';
    barfill.style.width=total?(100*done/total)+'%':'0';
    const lr=s.last_result||{{}};
    const dl=lr.download||{{}}, oc=lr.ocr||{{}}, va=lr.validate||{{}};
    let txt=s.running?'running batch...':(s.auto?'auto on':'idle');
    if(!s.ocr_available) txt+=' | OCR deps missing';
    if(s.last_error) txt+=' | ERROR';
    pipe.textContent='pipeline: '+txt;
    runBtn.disabled=s.running;
    if(lastBatches!==null && s.total_batches!==lastBatches && !s.running){{
      const u=new URL(location.href); location.href=u.pathname+u.search;
    }}
    lastBatches=s.total_batches;
  }}catch(e){{pipe.textContent='pipeline: (server stopped)';}}
}}
runBtn.onclick=async()=>{{runBtn.disabled=true;await fetch('/run',{{method:'POST'}});poll();}};
autoBox.onchange=async()=>{{await fetch('/auto',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'on='+(autoBox.checked?'1':'0')}});poll();}};
deptSel.onchange=async()=>{{await fetch('/dept',{{method:'POST',headers:{{'Content-Type':'application/x-www-form-urlencoded'}},body:'dept='+encodeURIComponent(deptSel.value)}});location.href='/';}};

// live activity feed
const feedlist=document.getElementById('feedlist');
const blockbanner=document.getElementById('blockbanner');
let lastEv=0;
async function pollEvents(){{
  try{{
    const evs=await (await fetch('/events?since='+lastEv)).json();
    for(const e of evs){{
      lastEv=e.id;
      const d=document.createElement('div');
      d.className='ev '+e.kind; d.textContent=e.msg; feedlist.appendChild(d);
    }}
    while(feedlist.children.length>200) feedlist.removeChild(feedlist.firstChild);
    if(evs.length) feedlist.scrollTop=feedlist.scrollHeight;
  }}catch(e){{}}
}}
// ---- live activity show/hide (remembered) ----
const FEED_KEY='feed_hidden';
const feedEl=document.getElementById('feed');
const feedShow=document.getElementById('feedShow');
function applyFeed(){{
  const hidden=localStorage.getItem(FEED_KEY)==='1';
  feedEl.classList.toggle('collapsed',hidden);
  feedShow.style.display=hidden?'block':'none';
}}
document.getElementById('feedHide').onclick=()=>{{localStorage.setItem(FEED_KEY,'1');applyFeed();}};
feedShow.onclick=()=>{{localStorage.setItem(FEED_KEY,'0');applyFeed();
  feedlist.scrollTop=feedlist.scrollHeight;}};
applyFeed();
function showBlock(s){{ blockbanner.style.display=s.blocked?'block':'none'; }}
const _origPoll=poll;
poll=async()=>{{ await _origPoll(); try{{const s=await (await fetch('/state')).json(); showBlock(s);}}catch(e){{}} }};
loadDepts(); setInterval(poll,2000); setInterval(pollEvents,1000); poll(); pollEvents();
</script>
</body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, data):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = urllib.parse.parse_qs(parsed.query)
        if parsed.path == "/pdf":
            return self.serve_pdf(qs.get("code", [""])[0])
        if parsed.path == "/totals":
            return self.serve_totals(qs.get("code", [""])[0])
        if parsed.path == "/browse":
            with db.connect() as conn:
                out = browse_page(
                    conn,
                    qs.get("dept", [""])[0] or None,
                    qs.get("mun", [""])[0] or None,
                    qs.get("zone", [""])[0] or None,
                    qs.get("station", [""])[0] or None,
                )
            return self._send(200, "text/html; charset=utf-8", out.encode("utf-8"))
        if parsed.path == "/state":
            data = json.dumps(RUNNER.snapshot()).encode("utf-8")
            return self._send(200, "application/json", data)
        if parsed.path == "/depts":
            data = json.dumps(RUNNER.list_departments()).encode("utf-8")
            return self._send(200, "application/json", data)
        if parsed.path == "/next":
            after = qs.get("code", [""])[0] or None
            dept = qs.get("dept", [""])[0] or DEPT_FILTER
            with db.connect() as conn:
                nxt = next_pending_code(conn, dept, after)
            return self._send(200, "application/json",
                              json.dumps({"next": nxt}).encode("utf-8"))
        if parsed.path == "/events":
            last = int(qs.get("since", ["0"])[0] or 0)
            data = json.dumps(RUNNER.events_since(last)).encode("utf-8")
            return self._send(200, "application/json", data)
        if parsed.path == "/ocr_prefill":
            code = qs.get("code", [""])[0]
            with db.connect() as conn:
                result = ocr_prefill(conn, code)
            return self._send(200, "application/json",
                              json.dumps(result).encode("utf-8"))
        if parsed.path == "/station":
            dept = qs.get("dept", [DEPT_FILTER])[0] or DEPT_FILTER
            with db.connect() as conn:
                out = station_page(conn, dept, qs.get("code", [None])[0])
            return self._send(200, "text/html; charset=utf-8", out.encode("utf-8"))
        if parsed.path in ("/", "/index.html"):
            # Station is the default view now.
            loc = "/station"
            if DEPT_FILTER:
                loc += f"?dept={urllib.parse.quote(DEPT_FILTER)}"
            self.send_response(302)
            self.send_header("Location", loc)
            self.end_headers()
            return
        if parsed.path != "/queue":
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        # Legacy queue+detail view kept reachable at /queue.
        with db.connect() as conn:
            out = page(conn, DEPT_FILTER, qs.get("code", [None])[0])
        self._send(200, "text/html; charset=utf-8", out.encode("utf-8"))

    def serve_pdf(self, code):
        if not code:
            return self._send(400, "text/plain; charset=utf-8", b"missing code")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT local_path FROM polling_tables WHERE transmission_code = ?",
                (code,),
            ).fetchone()
        path = row["local_path"] if row else None
        if not path or not os.path.exists(path):
            return self._send(404, "text/plain; charset=utf-8", b"pdf not downloaded")
        with open(path, "rb") as fh:
            self._send(200, "application/pdf", fh.read())

    def serve_totals(self, code):
        """Serve a high-res crop of the BLANK/NULL/UNMARKED/TOTAL band so the
        reviewer reads those tiny handwritten digits easily. Cached on disk."""
        if not code:
            return self._send(400, "text/plain; charset=utf-8", b"missing code")
        with db.connect() as conn:
            row = conn.execute(
                "SELECT local_path FROM polling_tables WHERE transmission_code = ?",
                (code,),
            ).fetchone()
        path = row["local_path"] if row else None
        if not path or not os.path.exists(path):
            return self._send(404, "text/plain; charset=utf-8", b"pdf not downloaded")
        try:
            import crop_totals
            out = os.path.join("data", "debug", f"{code}_totals.png")
            crop = out if os.path.exists(out) else crop_totals.crop_band(path, out)
            if not crop or not os.path.exists(crop):
                return self._send(404, "text/plain; charset=utf-8", b"band not found")
            with open(crop, "rb") as fh:
                self._send(200, "image/png", fh.read())
        except Exception as exc:  # noqa: BLE001
            self._send(500, "text/plain; charset=utf-8", str(exc).encode("utf-8"))

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        if path == "/run":
            RUNNER.trigger()
            return self._send(200, "application/json", b'{"ok":true}')
        if path == "/auto":
            length = int(self.headers.get("Content-Length", 0))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            RUNNER.set_auto(form.get("on", ["0"])[0] == "1")
            return self._send(200, "application/json", b'{"ok":true}')
        if path == "/dept":
            length = int(self.headers.get("Content-Length", 0))
            form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
            RUNNER.set_dept(form.get("dept", [""])[0])
            return self._send(200, "application/json", b'{"ok":true}')
        if path == "/entries":
            length = int(self.headers.get("Content-Length", 0))
            form = urllib.parse.parse_qs(
                self.rfile.read(length).decode("utf-8"), keep_blank_values=True
            )
            code = form.get("code", [""])[0]
            dept = form.get("dept", [""])[0]
            verdict = form.get("verdict", [""])[0]
            note = form.get("note", [""])[0]
            num_fields = {}
            for f in [x[0] for x in FIXED_FIELDS] + CANDIDATE_FIELDS:
                num_fields[f] = parse_int_or_none(form.get(f"f_{f}", [""])[0])
            name_fields = {}
            for f in CANDIDATE_FIELDS:
                name_fields[f] = form.get(f"n_{f}", [""])[0]
            next_code = code
            if code and verdict in ("valid", "anomaly", "unclear"):
                with db.connect() as conn:
                    save_entries_and_review(conn, code, verdict, note,
                                            num_fields, name_fields)
                    nxt = next_pending_code(conn, dept or None, code)
                    if nxt:
                        next_code = nxt
            loc = f"/station?code={urllib.parse.quote(next_code)}"
            if dept:
                loc += f"&dept={urllib.parse.quote(dept)}"
            self.send_response(303)
            self.send_header("Location", loc)
            self.end_headers()
            return
        if path != "/verdict":
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        length = int(self.headers.get("Content-Length", 0))
        form = urllib.parse.parse_qs(self.rfile.read(length).decode("utf-8"))
        code = form.get("code", [""])[0]
        verdict = form.get("verdict", [""])[0]
        note = form.get("note", [""])[0]
        dept = form.get("dept", [""])[0]
        if code and verdict in ("real", "ocr_error", "unclear"):
            with db.connect() as conn:
                save_verdict(conn, code, verdict, note)
        # Redirect back to the same table (PRG pattern).
        loc = f"/?code={urllib.parse.quote(code)}"
        if dept:
            loc += f"&dept={urllib.parse.quote(dept)}"
        self.send_response(303)
        self.send_header("Location", loc)
        self.end_headers()

    def log_message(self, *_):  # quiet console
        pass


def main():
    global DEPT_FILTER
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--dept", help="Restrict the queue to one department code")
    args = ap.parse_args()
    DEPT_FILTER = args.dept
    RUNNER.dept = args.dept   # dashboard-triggered runs match the dashboard filter

    db.init_db()
    server = ThreadingHTTPServer((HOST, args.port), Handler)
    print(f"Review dashboard at http://{HOST}:{args.port}  (Ctrl+C to stop)")
    if args.dept:
        print(f"Filtered to department {args.dept}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
