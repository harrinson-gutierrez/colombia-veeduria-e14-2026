"""Public web app for citizen review of Colombia's E14 presidential tally sheets.

Zero install for the user: they open a URL, browse Department -> Municipality ->
Zone -> Station -> table, see the official PDF, type the numbers they read, and
the app checks the vote sum. No vision model, no Tesseract, no local database:

- Index: loaded in memory from data/public_index.csv.gz (committed, ~6 MB).
- PDFs: proxied on demand from the Registraduria (nothing stored server-side).
- Verdicts + typed numbers: saved in the visitor's browser (localStorage).

Standard library only, so it runs on any free Python host.

    python public_server.py            # serves http://0.0.0.0:$PORT (default 8080)
"""
import csv
import gzip
import html
import json
import os
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "8080"))
INDEX_GZ = os.path.join("data", "public_index.csv.gz")
SOURCE_HOST = "https://divulgacione14presidente.registraduria.gov.co"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36")

# Fixed presidential ballot (first round, 2026). Order = ballot order.
CANDIDATES = [
    "IVÁN CEPEDA CASTRO", "CLAUDIA LÓPEZ", "RAÚL SANTIAGO BOTERO JARAMILLO",
    "ABELARDO DE LA ESPRIELLA", "ÓSCAR MAURICIO LIZCANO ARANGO",
    "MIGUEL URIBE LONDOÑO", "SONDRA MACOLLINS GARVIN PINTO",
    "ROY LEONARDO BARRERAS MONTEALEGRE", "CARLOS EDUARDO CAICEDO",
    "GUSTAVO MATAMOROS CAMACHO", "PALOMA VALENCIA LASERNA",
    "SERGIO FAJARDO VALDERRAMA", "LUIS GILBERTO MURILLO URRUTIA",
]

# ---- in-memory index ------------------------------------------------------
# ROWS: list of dicts. INDEX: nested dict for fast hierarchical lookups.
ROWS: list[dict] = []


def load_index() -> None:
    if not os.path.exists(INDEX_GZ):
        raise SystemExit(f"Missing {INDEX_GZ}. Run: python build_public_index.py")
    with gzip.open(INDEX_GZ, "rt", encoding="utf-8") as fh:
        ROWS.extend(csv.DictReader(fh))
    print(f"[i] loaded {len(ROWS):,} polling tables")


def esc(x) -> str:
    return html.escape(str(x if x is not None else ""))


def _distinct(level: str, sel: dict) -> list[tuple[str, str]]:
    """Distinct (code, name) at `level` under the current selection `sel`."""
    seen: dict[str, str] = {}
    for r in ROWS:
        if sel.get("dept") and r["department_code"] != sel["dept"]:
            continue
        if sel.get("mun") and r["municipality_code"] != sel["mun"]:
            continue
        if sel.get("zone") and r["zone_code"] != sel["zone"]:
            continue
        if sel.get("station") and r["station_code"] != sel["station"]:
            continue
        if level == "dept":
            seen.setdefault(r["department_code"], r["department_name"])
        elif level == "mun":
            seen.setdefault(r["municipality_code"], "")
        elif level == "zone":
            seen.setdefault(r["zone_code"], "")
        elif level == "station":
            seen.setdefault(r["station_code"], "")
    return sorted(seen.items())


def _tables(sel: dict) -> list[dict]:
    out = []
    for r in ROWS:
        if (r["department_code"] == sel["dept"] and
                r["municipality_code"] == sel["mun"] and
                r["zone_code"] == sel["zone"] and
                r["station_code"] == sel["station"]):
            out.append(r)
    out.sort(key=lambda r: (len(r["table_number"]), r["table_number"]))
    return out


# ---- HTML pages -----------------------------------------------------------
def _shell(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title><style>
 *{{box-sizing:border-box}} body{{margin:0;font-family:system-ui,Segoe UI,Roboto,sans-serif;background:#f4f6f9;color:#1c2230}}
 header{{background:#0d47a1;color:#fff;padding:.7rem 1.1rem}} header a{{color:#cfe0ff;text-decoration:none}}
 header b{{font-size:1.05rem}} .sub{{font-size:.78rem;color:#cfe0ff}}
 .crumbs{{padding:.6rem 1.1rem;font-size:.9rem;background:#fff;border-bottom:1px solid #e2e5ea}}
 .crumbs a{{color:#0d47a1;text-decoration:none}} .csep{{color:#aaa;margin:0 .15rem}}
 .note{{background:#fff8e1;border-bottom:1px solid #ffe082;padding:.5rem 1.1rem;font-size:.85rem}}
 h2{{margin:1rem 1.1rem .3rem;font-size:1.05rem}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:.7rem;padding:1rem 1.1rem}}
 .card{{display:block;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:.75rem .9rem;
   text-decoration:none;color:#1c2230;box-shadow:0 1px 3px rgba(0,0,0,.04)}}
 .card:hover{{border-color:#0d47a1;box-shadow:0 3px 10px rgba(13,71,161,.12)}}
 .card .lbl{{font-weight:600}} .card .st{{font-size:.78rem;color:#5b6472;margin-top:.3rem}}
 .card.done{{border-left:5px solid #2e7d32}} .card.anom{{border-left:5px solid #c62828}}
 .card.todo{{border-left:5px solid #cbd2da}}
 .station{{display:flex;gap:1rem;padding:1rem 1.1rem;flex-wrap:wrap}}
 .pdfpane{{flex:1 1 540px;min-height:70vh;background:#fff;border:1px solid #e2e5ea;border-radius:10px;overflow:hidden}}
 .pdfpane embed{{width:100%;height:78vh;border:0}}
 .form{{flex:1 1 340px;background:#fff;border:1px solid #e2e5ea;border-radius:10px;padding:1rem}}
 table.nums{{width:100%;border-collapse:collapse}} table.nums td{{padding:.25rem .3rem;border-bottom:1px solid #f0f2f5}}
 table.nums .nm{{font-size:.85rem}} .numin{{width:90px;padding:.3rem;border:1px solid #cfd4db;border-radius:5px;text-align:right}}
 .grphdr{{font-size:.75rem;text-transform:uppercase;color:#7a828c;padding-top:.6rem}}
 .sumchk{{margin:.7rem 0;padding:.5rem .6rem;border-radius:6px;font-size:.9rem;background:#eef2f7}}
 .sumchk.ok{{background:#e8f5e9;color:#1b5e20}} .sumchk.bad{{background:#fdecea;color:#b71c1c}}
 .verdicts{{display:flex;gap:.5rem;margin-top:.6rem;flex-wrap:wrap}}
 .verdicts button{{flex:1;border:0;border-radius:8px;padding:.6rem;color:#fff;font-weight:600;cursor:pointer}}
 .v-valid{{background:#2e7d32}} .v-anomaly{{background:#c62828}} .v-unclear{{background:#ef6c00}}
 .saved{{font-size:.82rem;color:#2e7d32;margin-top:.5rem;min-height:1.1rem}}
</style></head><body>
<header><b>Verifica el E14 &middot; Colombia 2026</b>
  &nbsp; <a href="/">Inicio</a>
  <div class="sub">Auditoría ciudadana de las actas presidenciales &middot; los datos quedan en tu navegador</div>
</header>
<div class="note"><b>Marcar una mesa como anomalía NO es declarar fraude.</b> Es
  señalar que merece revisión. Lee el acta original y decide tú.</div>
{body}
</body></html>"""


def browse_page(sel: dict) -> str:
    crumb_url = lambda **kw: "/browse?" + urllib.parse.urlencode(  # noqa: E731
        {k: v for k, v in {**sel, **kw}.items() if v})
    crumbs = ['<a href="/browse">Inicio</a>']
    if sel.get("dept"):
        crumbs.append(f'<a href="{esc(crumb_url(mun="", zone="", station=""))}">'
                      f'{esc(sel["dept"])}</a>')
    if sel.get("mun"):
        crumbs.append(f'<a href="{esc(crumb_url(zone="", station=""))}">Mun {esc(sel["mun"])}</a>')
    if sel.get("zone"):
        crumbs.append(f'<a href="{esc(crumb_url(station=""))}">Zona {esc(sel["zone"])}</a>')
    if sel.get("station"):
        crumbs.append(f'Puesto {esc(sel["station"])}')
    crumb_html = ' <span class="csep">&rsaquo;</span> '.join(crumbs)

    if not sel.get("dept"):
        level, child, heading = "dept", "dept", "Departamentos"
    elif not sel.get("mun"):
        level, child, heading = "mun", "mun", "Municipios"
    elif not sel.get("zone"):
        level, child, heading = "zone", "zone", "Zonas"
    elif not sel.get("station"):
        level, child, heading = "station", "station", "Puestos"
    else:
        return _tables_page(sel, crumb_html)

    cards = []
    for code, name in _distinct(level, sel):
        label = {"dept": f"{code} {name}", "mun": f"Municipio {code}",
                 "zone": f"Zona {code}", "station": f"Puesto {code}"}[level]
        href = crumb_url(**{child: code})
        cards.append(f'<a class="card" href="{esc(href)}" data-level="{level}" '
                     f'data-code="{esc(code)}"><div class="lbl">{esc(label)}</div>'
                     f'<div class="st prog"></div></a>')
    body = (f'<div class="crumbs">{crumb_html}</div><h2>{heading}</h2>'
            f'<div class="grid">' + "".join(cards) + "</div>" + _PROG_JS)
    return _shell(heading, body)


def _tables_page(sel: dict, crumb_html: str) -> str:
    cards = []
    for r in _tables(sel):
        code = "-".join([r["department_code"], r["municipality_code"],
                         r["zone_code"], r["station_code"], r["table_number"]])
        cards.append(
            f'<a class="card todo" data-code="{esc(code)}" '
            f'href="/mesa?{urllib.parse.urlencode(dict(sel, table=r["table_number"]))}">'
            f'<div class="lbl">Mesa {esc(r["table_number"])}</div>'
            f'<div class="st state">Pendiente</div></a>')
    body = (f'<div class="crumbs">{crumb_html}</div><h2>Mesas</h2>'
            f'<div class="grid">' + "".join(cards) + "</div>" + _TABLES_JS)
    return _shell("Mesas", body)


def mesa_page(sel: dict) -> str:
    rows = _tables({k: sel[k] for k in ("dept", "mun", "zone", "station")})
    row = next((r for r in rows if r["table_number"] == sel.get("table")), None)
    if not row:
        return _shell("Mesa", '<p style="padding:1.1rem">Mesa no encontrada.</p>')
    code = "-".join([sel["dept"], sel["mun"], sel["zone"], sel["station"], sel["table"]])
    pdf_proxy = "/pdf?u=" + urllib.parse.quote(row["pdf_url"], safe="")

    num_rows = []
    for i, name in enumerate(CANDIDATES):
        num_rows.append(
            f'<tr><td class="nm">{esc(name)}</td>'
            f'<td><input class="numin" data-f="c{i}" type="number" inputmode="numeric" min="0"></td></tr>')
    for f, lbl in [("blank", "Votos en blanco"), ("null", "Votos nulos"),
                   ("unmarked", "Votos no marcados"), ("total", "Total reportado")]:
        num_rows.append(
            f'<tr><td class="nm">{esc(lbl)}</td>'
            f'<td><input class="numin" data-f="{f}" type="number" inputmode="numeric" min="0"></td></tr>')

    body = f"""
<div class="crumbs"><a href="/browse?{urllib.parse.urlencode({k: sel[k] for k in ('dept','mun','zone','station')})}">
  &lsaquo; Volver a las mesas</a> &middot; {esc(sel['dept'])}/{esc(sel['mun'])}/zona {esc(sel['zone'])}/puesto {esc(sel['station'])}/mesa {esc(sel['table'])}</div>
<div class="station">
  <section class="pdfpane"><embed src="{pdf_proxy}" type="application/pdf"></section>
  <section class="form">
    <h2 style="margin:.2rem 0 .6rem">Escribe lo que ves en el acta</h2>
    <table class="nums"><tr class="grphdr"><td colspan="2">Candidatos</td></tr>
      {''.join(num_rows[:len(CANDIDATES)])}
      <tr class="grphdr"><td colspan="2">Otros</td></tr>
      {''.join(num_rows[len(CANDIDATES):])}
    </table>
    <div id="sumchk" class="sumchk">Escribe los números para cuadrar la suma.</div>
    <div class="verdicts">
      <button class="v-valid" data-v="valid">Verificada</button>
      <button class="v-anomaly" data-v="anomaly">Anomalía</button>
      <button class="v-unclear" data-v="unclear">Dudosa</button>
    </div>
    <div id="saved" class="saved"></div>
  </section>
</div>
<script>
const CODE={json.dumps(code)};
const KEY='e14_'+CODE;
const inputs=[...document.querySelectorAll('.numin')];
// restore saved values
try{{const s=JSON.parse(localStorage.getItem(KEY)||'{{}}');
  inputs.forEach(i=>{{ if(s.vals&&s.vals[i.dataset.f]!=null) i.value=s.vals[i.dataset.f]; }});
  if(s.verdict) document.getElementById('saved').textContent='Guardado: '+s.verdict;
}}catch(e){{}}
function recompute(){{
  let comp=0, total=null, any=false;
  for(const i of inputs){{
    const v=i.value===''?null:parseInt(i.value,10);
    if(i.dataset.f==='total'){{ total=v; }}
    else if(v!=null){{ comp+=v; any=true; }}
  }}
  const box=document.getElementById('sumchk');
  if(total==null||!any){{ box.className='sumchk'; box.textContent='Escribe los números para cuadrar la suma.'; return; }}
  if(comp===total){{ box.className='sumchk ok'; box.textContent='Cuadra: '+comp+' = '+total+' ✓'; }}
  else{{ box.className='sumchk bad'; box.textContent='No cuadra: suma '+comp+' vs total '+total+' (diferencia '+(comp-total)+')'; }}
}}
inputs.forEach(i=>i.addEventListener('input',()=>{{recompute();save();}}));
function vals(){{const o={{}};inputs.forEach(i=>{{if(i.value!=='')o[i.dataset.f]=parseInt(i.value,10);}});return o;}}
function save(verdict){{
  const cur=JSON.parse(localStorage.getItem(KEY)||'{{}}');
  const obj={{vals:vals(),verdict:verdict||cur.verdict||null,t:CODE}};
  localStorage.setItem(KEY,JSON.stringify(obj));
}}
document.querySelectorAll('.verdicts button').forEach(b=>b.onclick=()=>{{
  save(b.dataset.v);
  document.getElementById('saved').textContent='Guardado: '+b.dataset.v+'. Puedes volver a las mesas.';
}});
recompute();
</script>"""
    return _shell("Mesa " + sel["table"], body)


# JS that reads each card's saved state from localStorage and colours it.
_PROG_JS = """<script>
document.querySelectorAll('.card .prog').forEach(()=>{});
</script>"""

_TABLES_JS = """<script>
for(const c of document.querySelectorAll('.card[data-code]')){
  try{const s=JSON.parse(localStorage.getItem('e14_'+c.dataset.code)||'null');
    if(s&&s.verdict){
      const st=c.querySelector('.state');
      const map={valid:['done','Verificada'],anomaly:['anom','Anomalía'],unclear:['todo','Dudosa']};
      const m=map[s.verdict]||['todo','Pendiente'];
      c.classList.remove('todo');c.classList.add(m[0]);
      if(st) st.textContent=m[1];
    }
  }catch(e){}
}
</script>"""

HOME = """<div class="grid" style="grid-template-columns:1fr;max-width:680px">
<a class="card" href="/browse"><div class="lbl">Empezar a revisar &rarr;</div>
<div class="st">Navega Departamento &rarr; Municipio &rarr; Zona &rarr; Puesto &rarr; mesa,
abre el acta E14 oficial y escribe los números que ves. La app comprueba que la
suma cuadre. Tu progreso se guarda en tu navegador.</div></a></div>"""


# ---- on-demand PDF proxy --------------------------------------------------
PDF_MAGIC = b"%PDF-"


def fetch_pdf(url: str) -> bytes | None:
    """Fetch one tally PDF from the source. Only allow the official host."""
    if not url.startswith(SOURCE_HOST + "/"):
        return None
    req = urllib.request.Request(url, headers={"User-Agent": UA,
                                               "Referer": SOURCE_HOST + "/home"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
    except Exception:  # noqa: BLE001
        return None
    return data if data[:5] == PDF_MAGIC else None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, data, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if cache:
            self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        qs = {k: v[0] for k, v in urllib.parse.parse_qs(parsed.query).items()}
        sel = {k: qs.get(k) for k in ("dept", "mun", "zone", "station", "table")}
        try:
            if parsed.path == "/":
                return self._send(200, "text/html; charset=utf-8",
                                  _shell("Verifica el E14", HOME).encode("utf-8"))
            if parsed.path == "/browse":
                return self._send(200, "text/html; charset=utf-8",
                                  browse_page(sel).encode("utf-8"))
            if parsed.path == "/mesa":
                return self._send(200, "text/html; charset=utf-8",
                                  mesa_page(sel).encode("utf-8"))
            if parsed.path == "/pdf":
                data = fetch_pdf(qs.get("u", ""))
                if not data:
                    return self._send(404, "text/plain; charset=utf-8",
                                      "acta no disponible".encode("utf-8"))
                return self._send(200, "application/pdf", data, cache=True)
            if parsed.path == "/health":
                return self._send(200, "text/plain", b"ok")
            return self._send(404, "text/plain; charset=utf-8", b"not found")
        except BrokenPipeError:
            pass
        except Exception as exc:  # noqa: BLE001 - never crash the server
            self._send(500, "text/plain; charset=utf-8", str(exc)[:200].encode())

    def log_message(self, *a):  # quieter logs
        pass


def main():
    load_index()
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"[i] public server on http://0.0.0.0:{PORT}  (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()

