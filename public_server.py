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

import dashboard_views


_HERE = os.path.dirname(os.path.abspath(__file__))


def _load_dotenv(path: str = None) -> None:
    """Load KEY=VALUE from .env (next to this script, so it works regardless of
    the working directory) into os.environ without overriding what is set.
    Keeps Supabase keys out of the global shell and out of git."""
    path = path or os.path.join(_HERE, ".env")
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

PORT = int(os.environ.get("PORT", "8080"))
INDEX_GZ = os.path.join(_HERE, "data", "public_index.csv.gz")
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


def mesa_code(dept, mun, zone, station, table) -> str:
    """Canonical table id shared with Supabase (reports + official_data).
    The table number is stripped of leading zeros so it matches the parsed
    official sheet ('003' -> '3'). MUST stay identical on both sides."""
    t = str(int(table)) if str(table).isdigit() else str(table)
    return "-".join([dept, mun, zone, station, t])


# ---- consensus (Supabase magic-link login + cross-confirmation) -----------
SUPABASE_URL = os.environ.get("SUPABASE_URL") or None
SUPABASE_KEY = os.environ.get("SUPABASE_KEY") or None


def _consensus_block(code: str) -> str:
    """Login + submit report + show consensus + official data, rendered into the
    in-page panels (#authpanel, #officialpanel, #consensusline). No floating
    bars. Returns a notice panel when Supabase is not configured."""
    import json as _json
    if not (SUPABASE_URL and SUPABASE_KEY):
        return ("<script>var a=document.getElementById('authpanel');"
                "if(a)a.textContent='Modo local: el consenso compartido está "
                "desactivado (no hay Supabase configurado).';</script>")
    url, key, mesa = _json.dumps(SUPABASE_URL), _json.dumps(SUPABASE_KEY), _json.dumps(code or "")
    return f"""
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>
<script>
const SB = window.supabase.createClient({url}, {key});
const MESA = {mesa};
const authEl = document.getElementById('authpanel');
const offEl  = document.getElementById('officialpanel');
const clineEl = document.getElementById('consensusline');
function el(t,p,...k){{const n=document.createElement(t);Object.assign(n,p||{{}});for(const c of k)n.append(c);return n;}}

async function refreshAuth(){{
  authEl.textContent='';
  const {{data}} = await SB.auth.getUser();
  if (data&&data.user){{
    const out=el('button',{{textContent:'salir',className:'linkbtn'}});
    out.onclick=async()=>{{await SB.auth.signOut();refreshAuth();}};
    authEl.append(el('span',{{className:'ok'}},'✓ Sesión: '),
      el('b',{{textContent:data.user.email}}),' ',out);
  }} else {{
    authEl.append(el('div',{{className:'authtitle',
      textContent:'Para sumar tu reporte al consenso, entra con tu correo:'}}));
    const inp=el('input',{{type:'email',placeholder:'tu@correo.com',className:'authinput'}});
    const btn=el('button',{{textContent:'Enviar enlace',className:'authbtn'}});
    btn.onclick=async ()=>{{const email=inp.value.trim();if(!email)return;
      btn.disabled=true;
      const {{error}}=await SB.auth.signInWithOtp({{email,options:{{emailRedirectTo:location.href}}}});
      authEl.textContent=error?('Error: '+error.message):
        '📧 Te enviamos un enlace a '+email+'. Ábrelo para entrar (revisa spam).';}};
    const row=el('div',{{className:'authrow'}},inp,btn);
    authEl.append(row);
  }}
}}

async function showConsensus(){{
  if(!MESA)return;
  const {{data}}=await SB.from('consensus').select('*').eq('mesa_code',MESA);
  clineEl.textContent='';
  if(data&&data.length){{const c=data[0];
    const map={{confirmed:['✅ CONFIRMADA por consenso','ok'],
      disputed:['⚠️ EN DISPUTA','bad'],pending:['⏳ pendiente de más reportes','warn']}};
    const m=map[c.status]||[c.status,''];
    clineEl.append(el('span',{{className:'cbadge '+m[1],textContent:m[0]}}),
      ' '+c.n_reports+' reporte(s), '+c.agreeing_count+' coinciden');
  }} else clineEl.textContent='Aún sin reportes de otras personas para esta mesa.';
}}

window.__submitReport = async function(verdict){{
  const {{data}}=await SB.auth.getUser();
  if(!data||!data.user){{ clineEl.textContent='Entra con tu correo (arriba) para enviar tu reporte.'; return; }}
  if(!MESA)return;
  const c={{}}; let blank=null,nullv=null,unmarked=null,total=null;
  document.querySelectorAll('.numin').forEach(i=>{{
    const f=i.dataset.f, v=i.value===''?null:parseInt(i.value,10); if(v===null)return;
    if(f&&f[0]==='c'&&f.length<=3) c[f]=v;
    else if(f==='blank')blank=v; else if(f==='null')nullv=v;
    else if(f==='unmarked')unmarked=v; else if(f==='total')total=v;
  }});
  const row={{mesa_code:MESA,user_id:data.user.id,verdict,candidates:c,blank,null_votes:nullv,unmarked,total}};
  const {{error}}=await SB.from('reports').upsert(row,{{onConflict:'mesa_code,user_id'}});
  if(error)clineEl.textContent='No se pudo enviar: '+error.message; else showConsensus();
}};

async function showOfficial(){{
  if(!MESA)return;
  const {{data}}=await SB.from('official_data').select('*').eq('mesa_code',MESA);
  offEl.textContent='';
  if(!data||!data.length)return;
  const o=data[0];
  const box=el('div',{{className:'official'}});
  box.append(el('div',{{className:'offtitle',textContent:'Análisis externo (a verificar)'}}));
  box.append(el('div',{{}},'Preconteo (PRE): '+(o.votos_pre==null?'—':o.votos_pre)+
    '  ·  Escrutinio (ESC): '+(o.votos_esc==null?'—':o.votos_esc)));
  if(o.dif_neta!=null&&o.dif_neta!==0)
    box.append(el('div',{{className:'offdiff',textContent:'⚠ Diferencia PRE/ESC: '+o.dif_neta}}));
  const flags=[];
  if(o.estado_sobre&&o.estado_sobre.toUpperCase()!=='BUENO') flags.push('sobre: '+o.estado_sobre);
  if(o.recontada_jurados&&o.recontada_jurados.toUpperCase()==='SI') flags.push('recontada por jurados');
  if(o.tachaduras&&o.tachaduras.toUpperCase()==='SI') flags.push('tachaduras/enmendaduras');
  if(o.excluida&&o.excluida.toUpperCase()==='SI') flags.push('EXCLUIDA');
  if(flags.length) box.append(el('div',{{className:'offflags',textContent:'Alertas: '+flags.join(' · ')}}));
  box.append(el('div',{{className:'offsrc',textContent:'Fuente: análisis ciudadano de terceros. Compáralo con el acta.'}}));
  offEl.append(box);
}}

refreshAuth(); showConsensus(); showOfficial();
</script>"""


# ---- HTML pages -----------------------------------------------------------
def _shell(title: str, body: str, active: str = "", note: bool = True) -> str:
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Inter+Tight:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
 :root{{
   --bg:#0c1116; --panel:#11181f; --rail:#0a0f14;
   --card:#ffffff; --ink:#0e1419; --soft:#5d6b78; --line:#eceff2;
   --brand:#10b981; --brand-d:#059669; --brand-soft:#e7f7f1;
   --flag:#d97706; --flag-soft:#fff7ed; --bad:#dc2626; --good:#10b981;
   --shadow:0 1px 2px rgba(13,20,25,.04), 0 12px 28px -16px rgba(13,20,25,.25);
 }}
 *{{box-sizing:border-box}}
 body{{margin:0;font-family:'Inter Tight',system-ui,sans-serif;background:#f6f8f9;
   color:var(--ink);-webkit-font-smoothing:antialiased;line-height:1.5}}
 a{{color:var(--brand-d)}}
 .app{{display:flex;min-height:100vh}}
 /* sidebar */
 .rail{{width:248px;flex:none;background:var(--rail);color:#cdd6df;padding:1.3rem 1rem;
   display:flex;flex-direction:column;gap:.3rem;position:sticky;top:0;height:100vh}}
 .rail .brand{{font-family:'Fraunces',serif;font-weight:600;font-size:1.45rem;color:#fff;
   padding:.2rem .6rem 1rem;letter-spacing:-.01em}}
 .rail .brand .dot{{color:var(--brand)}}
 .rail .sect{{font-size:.68rem;text-transform:uppercase;letter-spacing:.08em;color:#5f6e7b;
   padding:1rem .7rem .35rem}}
 .rail a{{display:flex;align-items:center;gap:.6rem;color:#cdd6df;text-decoration:none;
   padding:.55rem .7rem;border-radius:9px;font-size:.92rem;transition:background .12s}}
 .rail a:hover{{background:#18222c;color:#fff}}
 .rail a.on{{background:var(--brand);color:#06231a;font-weight:600}}
 .rail .spacer{{flex:1}}
 .rail .foot{{font-size:.72rem;color:#566472;padding:.6rem .7rem;border-top:1px solid #1b2630}}
 /* main */
 .main{{flex:1;min-width:0;display:flex;flex-direction:column}}
 .topbar{{background:#fff;border-bottom:1px solid var(--line);padding:1rem 1.6rem;
   display:flex;align-items:center;gap:1rem}}
 .topbar h1{{font-family:'Fraunces',serif;font-weight:600;font-size:1.35rem;margin:0;letter-spacing:-.01em}}
 .topbar .meta{{margin-left:auto;font-size:.8rem;color:var(--soft)}}
 .note{{background:var(--flag-soft);border-bottom:1px solid #fde9cf;padding:.7rem 1.6rem;font-size:.85rem;color:#92400e}}
 .crumbs{{padding:.85rem 1.6rem;font-size:.85rem;background:#fff;border-bottom:1px solid var(--line);color:var(--soft)}}
 .crumbs a{{text-decoration:none}} .crumbs a:hover{{text-decoration:underline}} .csep{{color:#c4ccd4;margin:0 .25rem}}
 h2{{font-family:'Fraunces',serif;font-weight:600;margin:1.5rem 1.6rem .3rem;font-size:1.45rem;letter-spacing:-.01em}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:1rem;padding:1.2rem 1.6rem}}
 .card{{display:block;background:var(--card);border:1px solid var(--line);border-radius:16px;
   padding:1.1rem 1.2rem;text-decoration:none;color:var(--ink);box-shadow:var(--shadow);
   transition:transform .15s ease, box-shadow .15s ease;animation:rise .4s ease both}}
 .card:hover{{transform:translateY(-3px);box-shadow:0 6px 12px rgba(13,20,25,.06),0 22px 40px -18px rgba(13,20,25,.35)}}
 .card .lbl{{font-weight:600;font-size:1.05rem}} .card .st{{font-size:.8rem;color:var(--soft);margin-top:.5rem}}
 .card.done{{border-left:4px solid var(--good)}} .card.anom{{border-left:4px solid var(--bad)}}
 .card.todo{{border-left:4px solid #e1e6ea}}
 .card.priority{{border:1px solid #fcd9a8;background:linear-gradient(180deg,#fffbf4,#fff)}}
 .card.priority .state{{color:var(--flag);font-weight:600}}
 .bar{{height:6px;background:#eef1f3;border-radius:99px;overflow:hidden;margin:.55rem 0 .1rem}}
 .barfill{{height:100%;background:var(--brand);border-radius:99px}}
 @keyframes rise{{from{{opacity:0;transform:translateY(10px)}}to{{opacity:1;transform:none}}}}
 .station{{display:flex;gap:1.2rem;padding:1.2rem 1.6rem;align-items:flex-start;flex-wrap:wrap}}
 .pdfpane{{flex:1 1 520px;background:var(--card);border:1px solid var(--line);border-radius:16px;
   overflow:hidden;display:flex;flex-direction:column;box-shadow:var(--shadow)}}
 .pdfbar{{display:flex;justify-content:space-between;align-items:center;padding:.75rem 1rem;
   background:var(--panel);color:#eef3f7;font-size:.85rem;font-weight:500}} .pdfbar a{{color:#6ee7b7}}
 .pdfframe{{width:100%;height:80vh;border:0;background:#2a3138}}
 .pdffallback{{padding:.6rem 1rem;font-size:.8rem;color:var(--soft);border-top:1px solid var(--line)}}
 .form{{flex:1 1 360px;background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1.3rem;box-shadow:var(--shadow)}}
 .form h2{{margin:.4rem 0 .7rem;font-size:1.2rem}}
 .panel{{background:var(--brand-soft);border:1px solid #c7eee0;border-radius:12px;padding:.8rem .9rem;font-size:.85rem;margin-bottom:.8rem}}
 .panel .ok{{color:var(--brand-d);font-weight:600}}
 .authtitle{{margin-bottom:.55rem;color:#3f5a52;font-weight:500}}
 .authrow{{display:flex;gap:.45rem}}
 .authinput{{flex:1;padding:.6rem .7rem;border:1px solid #b6e0d0;border-radius:9px;font:inherit;background:#fff}}
 .authbtn{{border:0;border-radius:9px;background:var(--brand-d);color:#fff;padding:.6rem 1rem;cursor:pointer;font-weight:600;font:inherit}}
 .authbtn:disabled{{opacity:.5}}
 .linkbtn{{border:0;background:none;color:var(--brand-d);cursor:pointer;text-decoration:underline;font:inherit;font-size:.85rem}}
 .official{{background:var(--flag-soft);border:1px solid #fcd9a8;border-radius:12px;padding:.75rem .9rem;font-size:.82rem;color:#92400e;margin-bottom:.8rem}}
 .offtitle{{font-weight:700;margin-bottom:.35rem;letter-spacing:.04em;text-transform:uppercase;font-size:.68rem}}
 .offdiff{{font-weight:700;color:var(--bad);margin-top:.3rem}}
 .offflags{{margin-top:.3rem;color:#a8540c}} .offsrc{{margin-top:.4rem;font-size:.72rem;opacity:.65}}
 table.nums{{width:100%;border-collapse:collapse}}
 table.nums td{{padding:.45rem .3rem;border-bottom:1px solid var(--line)}}
 table.nums .nm{{font-size:.9rem}}
 .numin{{width:84px;padding:.45rem;border:1px solid #d3dae0;border-radius:9px;text-align:right;font:inherit;
   font-variant-numeric:tabular-nums}}
 .numin:focus{{outline:2px solid var(--brand);border-color:var(--brand)}}
 .grphdr{{font-size:.68rem;text-transform:uppercase;letter-spacing:.07em;color:#9aa6b0;padding-top:.9rem;font-weight:600}}
 .sumchk{{margin:.9rem 0;padding:.7rem .8rem;border-radius:12px;font-size:.9rem;background:#f1f4f6;font-variant-numeric:tabular-nums}}
 .sumchk.ok{{background:var(--brand-soft);color:var(--brand-d);font-weight:600}}
 .sumchk.bad{{background:#fdeaea;color:var(--bad);font-weight:600}}
 .verdicts{{display:flex;gap:.55rem;margin-top:.8rem}}
 .verdicts button{{flex:1;border:0;border-radius:11px;padding:.75rem;color:#fff;font-weight:600;cursor:pointer;font:inherit;transition:filter .12s}}
 .verdicts button:hover{{filter:brightness(1.08)}}
 .v-valid{{background:var(--good)}} .v-anomaly{{background:var(--bad)}} .v-unclear{{background:var(--flag)}}
 .saved{{font-size:.82rem;color:var(--brand-d);margin-top:.7rem;min-height:1.1rem}}
 .cline{{font-size:.85rem;margin-top:.7rem;min-height:1.2rem;color:var(--soft)}}
 .cbadge{{font-weight:700}} .cbadge.ok{{color:var(--brand-d)}} .cbadge.bad{{color:var(--bad)}} .cbadge.warn{{color:var(--flag)}}
 /* hero / metrics */
 .hero{{padding:3rem 1.6rem;max-width:760px}}
 .hero h1{{font-family:'Fraunces',serif;font-weight:600;font-size:2.8rem;line-height:1.04;letter-spacing:-.02em;margin:.2rem 0 .9rem}}
 .hero p{{color:var(--soft);font-size:1.08rem;max-width:60ch}}
 .cta{{display:inline-flex;align-items:center;gap:.5rem;margin-top:1.6rem;background:var(--brand-d);color:#fff;
   text-decoration:none;padding:.85rem 1.5rem;border-radius:12px;font-weight:600;box-shadow:var(--shadow)}}
 .cta:hover{{filter:brightness(1.08)}}
 .metrics{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem;padding:0 1.6rem 1.6rem;max-width:1100px}}
 .metric{{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1.1rem 1.2rem;box-shadow:var(--shadow)}}
 .metric .n{{font-family:'Fraunces',serif;font-size:2rem;font-weight:600;line-height:1}}
 .metric .k{{font-size:.78rem;color:var(--soft);margin-top:.4rem}}
 .metric.brand .n{{color:var(--brand-d)}} .metric.flag .n{{color:var(--flag)}}
 /* analysis views: section, table, chart, filters, badges */
 .wrap{{padding:1.4rem 1.6rem;max-width:1180px}}
 .lead{{color:var(--soft);font-size:.95rem;max-width:70ch;margin:.1rem 0 1.3rem}}
 .panelcard{{background:var(--card);border:1px solid var(--line);border-radius:16px;
   box-shadow:var(--shadow);padding:1.2rem 1.3rem;margin-bottom:1.3rem}}
 .panelcard h3{{font-family:'Fraunces',serif;font-weight:600;font-size:1.15rem;margin:0 0 .2rem;letter-spacing:-.01em}}
 .panelcard .sub{{font-size:.82rem;color:var(--soft);margin-bottom:1rem}}
 table.data{{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}}
 table.data th{{text-align:left;font-weight:600;color:var(--soft);font-size:.72rem;text-transform:uppercase;
   letter-spacing:.05em;padding:.5rem .6rem;border-bottom:2px solid var(--line);white-space:nowrap}}
 table.data th.num,table.data td.num{{text-align:right}}
 table.data td{{padding:.55rem .6rem;border-bottom:1px solid var(--line)}}
 table.data tr:hover td{{background:#f8fafb}}
 table.data a{{color:var(--brand-d);text-decoration:none;font-weight:600}}
 table.data a:hover{{text-decoration:underline}}
 .sev{{display:inline-block;padding:.12rem .5rem;border-radius:99px;font-size:.7rem;font-weight:700;
   text-transform:uppercase;letter-spacing:.03em}}
 .sev.alta{{background:#fdeaea;color:var(--bad)}} .sev.media{{background:var(--flag-soft);color:#b45309}}
 .sev.baja{{background:#eef1f3;color:var(--soft)}}
 .pill{{display:inline-block;padding:.12rem .5rem;border-radius:99px;font-size:.72rem;font-weight:600;
   background:var(--flag-soft);color:#b45309;margin:0 .25rem .25rem 0}}
 .pill.bad{{background:#fdeaea;color:var(--bad)}}
 .filters{{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem}}
 .filters input,.filters select{{padding:.5rem .7rem;border:1px solid #d3dae0;border-radius:9px;font:inherit;background:#fff}}
 .filters input{{min-width:220px}}
 .chartrow{{display:flex;align-items:center;gap:.7rem;margin:.35rem 0}}
 .chartrow .nm{{width:130px;flex:none;font-size:.85rem;font-weight:600}}
 .chartrow .track{{flex:1;background:#eef1f3;border-radius:99px;height:22px;overflow:hidden;position:relative}}
 .chartrow .fill{{height:100%;background:linear-gradient(90deg,var(--brand),var(--brand-d));border-radius:99px;
   min-width:2px;transition:width .6s cubic-bezier(.2,.8,.2,1)}}
 .chartrow .val{{width:64px;flex:none;text-align:right;font-variant-numeric:tabular-nums;font-size:.85rem;color:var(--soft)}}
 .muted{{color:var(--soft)}} .center{{text-align:center}}
 .loadingrow td{{padding:1.4rem;text-align:center;color:var(--soft)}}
 .seg{{display:inline-flex;border:1px solid var(--line);border-radius:10px;overflow:hidden}}
 .seg button{{border:0;background:#fff;padding:.45rem .8rem;cursor:pointer;font:inherit;font-size:.82rem;color:var(--soft)}}
 .seg button.on{{background:var(--brand);color:#06231a;font-weight:600}}
 @media(max-width:720px){{.rail{{display:none}} .station{{flex-direction:column}}}}
</style></head><body>
<div class="app">
 <nav class="rail">
   <div class="brand">Veeduría E14<span class="dot">.</span></div>
   <a href="/" data-nav="/">Inicio</a>
   <a href="/browse" data-nav="/browse">Navegar mesas</a>
   <div class="sect">Análisis oficial</div>
   <a href="/resumen" data-nav="/resumen">Resumen</a>
   <a href="/anomalias" data-nav="/anomalias">Anomalías</a>
   <a href="/calidad" data-nav="/calidad">Calidad del proceso</a>
   <a href="/tabla" data-nav="/tabla">Todas las mesas</a>
   <div class="sect">Acerca de</div>
   <a href="https://github.com/harrinson-gutierrez/colombia-veeduria-e14-2026" target="_blank" rel="noopener">Código abierto</a>
   <div class="spacer"></div>
   <div class="foot">Auditoría ciudadana · Colombia 2026.<br>Tus datos quedan en tu navegador.</div>
 </nav>
 <div class="main">
  <div class="topbar"><h1>{esc(title)}</h1>
    <span class="meta">Actas E14 presidenciales</span></div>
  {'<div class="note"><b>Una diferencia o una alerta NO es fraude.</b> Es una señal de que la mesa merece revisión humana. Contrasta siempre con el acta original.</div>' if note else ''}
  {body}
 </div>
</div>
<script>
 (function(){{var p={json.dumps('')}||location.pathname;
   document.querySelectorAll('.rail a[data-nav]').forEach(function(a){{
     if(a.getAttribute('data-nav')===location.pathname)a.classList.add('on');}});}})();
</script>
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
        code = mesa_code(r["department_code"], r["municipality_code"],
                         r["zone_code"], r["station_code"], r["table_number"])
        cards.append(
            f'<a class="card todo" data-code="{esc(code)}" '
            f'href="/mesa?{urllib.parse.urlencode(dict(sel, table=r["table_number"]))}">'
            f'<div class="lbl">Mesa {esc(r["table_number"])}</div>'
            f'<div class="st state">Pendiente</div></a>')
    body = (f'<div class="crumbs">{crumb_html}</div><h2>Mesas</h2>'
            f'<div class="grid">' + "".join(cards) + "</div>" + _tables_js())
    return _shell("Mesas", body)


def _same_table(a, b) -> bool:
    """Match table numbers tolerant of leading zeros ('1' == '001')."""
    if a == b:
        return True
    return a.isdigit() and b.isdigit() and int(a) == int(b)


def mesa_page(sel: dict) -> str:
    rows = _tables({k: sel[k] for k in ("dept", "mun", "zone", "station")})
    want = sel.get("table") or ""
    row = next((r for r in rows if _same_table(r["table_number"], want)), None)
    if not row:
        return _shell("Mesa", '<p style="padding:1.1rem">Mesa no encontrada.</p>')
    code = mesa_code(sel["dept"], sel["mun"], sel["zone"], sel["station"], sel["table"])
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

    src_url = row["pdf_url"]                       # direct official PDF link
    back = "/browse?" + urllib.parse.urlencode({k: sel[k] for k in ('dept','mun','zone','station')})
    body = f"""
<div class="crumbs"><a href="{esc(back)}">&lsaquo; Volver a las mesas</a>
  &middot; {esc(sel['dept'])}/{esc(sel['mun'])} &middot; zona {esc(sel['zone'])}
  &middot; puesto {esc(sel['station'])} &middot; mesa {esc(sel['table'])}</div>
<div class="station">
  <section class="pdfpane">
    <div class="pdfbar">
      <span>Acta E14 oficial</span>
      <a href="{esc(src_url)}" target="_blank" rel="noopener">Abrir en pestaña nueva &#8599;</a>
    </div>
    <div id="pdfwrap" style="position:relative;flex:1">
      <div id="pdfmsg" style="padding:1.2rem;font-size:.9rem;color:var(--soft)">
        Cargando el acta… (puede tardar unos segundos)</div>
      <iframe class="pdfframe" id="pdfframe" title="Acta E14" style="display:none"></iframe>
    </div>
    <div class="pdffallback">¿No se ve el acta?
      <a href="{esc(src_url)}" target="_blank" rel="noopener">ábrela directo en una pestaña &#8599;</a></div>
  </section>
  <section class="form">
    <!-- login / consensus panel goes at the TOP so it is always visible -->
    <div id="authpanel" class="panel"></div>
    <div id="officialpanel"></div>

    <h2 style="margin:.4rem 0 .5rem">Escribe lo que ves en el acta</h2>
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
    <div id="consensusline" class="cline"></div>
  </section>
</div>
<script>
// Load the acta through our proxy. We probe with fetch so we can show a clear
// message if the source times out / blocks (the iframe alone would just sit
// blank). On success, point the iframe at the same (now-warm) proxy URL.
(function(){{
  const PROXY={json.dumps(pdf_proxy)}, SRC={json.dumps(src_url)};
  const msg=document.getElementById('pdfmsg'), frame=document.getElementById('pdfframe');
  // GET (not HEAD): the server caches the fetched PDF, so the iframe's own
  // request is then served instantly from cache — no double download.
  fetch(PROXY).then(r=>{{
    if(r.ok){{ frame.src=PROXY; frame.style.display='block'; msg.style.display='none'; }}
    else throw new Error('proxy '+r.status);
  }}).catch(()=>{{
    msg.innerHTML='No pudimos cargar el acta aquí (la fuente oficial respondió '+
      'lento o bloqueó la descarga). <a href="'+SRC+'" target="_blank" rel="noopener">'+
      'Ábrela directo en una pestaña nueva ↗</a> y escribe los números igual.';
  }});
}})();
const CODE={json.dumps(code)};
const KEY='e14_'+CODE;
const inputs=[...document.querySelectorAll('.numin')];
try{{const s=JSON.parse(localStorage.getItem(KEY)||'{{}}');
  inputs.forEach(i=>{{ if(s.vals&&s.vals[i.dataset.f]!=null) i.value=s.vals[i.dataset.f]; }});
  if(s.verdict) document.getElementById('saved').textContent='Tu marca local: '+s.verdict;
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
  else{{ box.className='sumchk bad'; box.textContent='No cuadra: suma '+comp+' vs total '+total+' (dif '+(comp-total)+')'; }}
}}
inputs.forEach(i=>i.addEventListener('input',()=>{{recompute();save();}}));
function vals(){{const o={{}};inputs.forEach(i=>{{if(i.value!=='')o[i.dataset.f]=parseInt(i.value,10);}});return o;}}
function save(verdict){{
  const cur=JSON.parse(localStorage.getItem(KEY)||'{{}}');
  localStorage.setItem(KEY,JSON.stringify({{vals:vals(),verdict:verdict||cur.verdict||null,t:CODE}}));
}}
document.querySelectorAll('.verdicts button').forEach(b=>b.onclick=()=>{{
  save(b.dataset.v);
  document.getElementById('saved').textContent='Tu marca local: '+b.dataset.v;
  if (window.__submitReport) window.__submitReport(b.dataset.v);
}});
recompute();
</script>
{_consensus_block(code)}"""
    return _shell("Mesa " + sel["table"], body)


# JS that reads each card's saved state from localStorage and colours it.
_PROG_JS = """<script>
document.querySelectorAll('.card .prog').forEach(()=>{});
</script>"""

def _tables_js() -> str:
    # Optional Supabase read to flag tables with a PRE/ESC discrepancy (priority).
    sb = ""
    if SUPABASE_URL and SUPABASE_KEY:
        import json as _json
        sb = f"""
  // Flag tables that the external analysis marks as priority (PRE/ESC mismatch).
  try{{
    const codes=[...document.querySelectorAll('.card[data-code]')].map(c=>c.dataset.code);
    if(codes.length){{
      const inList=encodeURIComponent('("'+codes.join('","')+'")');
      const r=await fetch({_json.dumps(SUPABASE_URL)}+'/rest/v1/official_data?select=mesa_code,dif_neta'+
        '&priority=eq.true&mesa_code=in.'+inList,
        {{headers:{{apikey:{_json.dumps(SUPABASE_KEY)}}}}});
      const prio=await r.json();
      for(const p of (prio||[])){{
        const c=document.querySelector('.card[data-code="'+p.mesa_code+'"]');
        if(c){{ c.classList.add('priority');
          const st=c.querySelector('.state');
          if(st) st.textContent='⚠ revisar (dif. PRE/ESC '+p.dif_neta+')'; }}
      }}
    }}
  }}catch(e){{}}
"""
    return f"""<script>
(async function(){{
  for(const c of document.querySelectorAll('.card[data-code]')){{
    try{{const s=JSON.parse(localStorage.getItem('e14_'+c.dataset.code)||'null');
      if(s&&s.verdict){{
        const st=c.querySelector('.state');
        const map={{valid:['done','Verificada'],anomaly:['anom','Anomalía'],unclear:['todo','Dudosa']}};
        const m=map[s.verdict]||['todo','Pendiente'];
        c.classList.remove('todo');c.classList.add(m[0]);
        if(st) st.textContent=m[1];
      }}
    }}catch(e){{}}
  }}{sb}
}})();
</script>"""

def home_page() -> str:
    n_tables = len(ROWS)
    n_depts = len({r["department_code"] for r in ROWS})
    n_muns = len({(r["department_code"], r["municipality_code"]) for r in ROWS})
    return f"""<div class="hero">
  <h1>Verifica las actas E14, mesa por mesa.</h1>
  <p>Abre el acta oficial escaneada de cada mesa, escribe los números que ves, y
  la app comprueba que la suma cuadre. Cuando varias personas coinciden de forma
  independiente, la mesa queda <b>confirmada por consenso</b>.</p>
  <p style="font-size:.92rem;margin-top:.7rem;color:var(--soft)">Tu trabajo queda en
  tu navegador; solo se comparte cuando envías tu reporte al consenso (con tu correo).</p>
  <a class="cta" href="/browse">Empezar a revisar →</a>
</div>
<div class="metrics">
  <div class="metric brand"><div class="n">{n_tables:,}</div><div class="k">Mesas publicadas para revisar</div></div>
  <div class="metric"><div class="n">{n_depts}</div><div class="k">Departamentos</div></div>
  <div class="metric"><div class="n">{n_muns:,}</div><div class="k">Municipios</div></div>
  <div class="metric flag"><div class="n">3</div><div class="k">Coincidencias para confirmar una mesa</div></div>
</div>"""


# ---- analysis dashboard (shared with the review station) ------------------
# The four official-data views live in dashboard_views.py so the public app and
# the local review station render the exact same thing. Here we just wrap the
# returned body in this app's shell.
def resumen_page() -> str:
    title, body = dashboard_views.resumen_body(SUPABASE_URL, SUPABASE_KEY)
    return _shell(title, body, note=False)


def anomalias_page() -> str:
    title, body = dashboard_views.anomalias_body(SUPABASE_URL, SUPABASE_KEY)
    return _shell(title, body, note=False)


def calidad_page() -> str:
    title, body = dashboard_views.calidad_body(SUPABASE_URL, SUPABASE_KEY)
    return _shell(title, body, note=False)


def tabla_page() -> str:
    title, body = dashboard_views.tabla_body(SUPABASE_URL, SUPABASE_KEY)
    return _shell(title, body, note=False)


# ---- on-demand PDF proxy --------------------------------------------------
PDF_MAGIC = b"%PDF-"


# From a datacenter (e.g. Render) the Registraduria is much slower to respond
# than from a residential Colombian IP, so the default read timeout is generous
# and overridable. Set PDF_TIMEOUT (seconds) to tune it on the host.
PDF_TIMEOUT = float(os.environ.get("PDF_TIMEOUT", "45"))
PDF_TRIES = int(os.environ.get("PDF_TRIES", "2"))

# Small in-memory LRU cache: a fetched acta is reused for the iframe's own
# request (the page probes with HEAD first) and for the next visitor on the
# same instance. Bounded so Render Free's small RAM is never overwhelmed.
import collections  # noqa: E402
_PDF_CACHE: "collections.OrderedDict[str, bytes]" = collections.OrderedDict()
_PDF_CACHE_MAX = int(os.environ.get("PDF_CACHE_MAX", "40"))


def fetch_pdf(url: str) -> bytes | None:
    """Fetch one tally PDF from the source. Only allow the official host.
    Caches successes in memory, retries on timeout (the source is slow/flaky
    from datacenter IPs), and logs each attempt so failures show in the logs."""
    if not url.startswith(SOURCE_HOST + "/"):
        print(f"[pdf] rejected non-source url: {url[:60]}", flush=True)
        return None
    cached = _PDF_CACHE.get(url)
    if cached is not None:
        _PDF_CACHE.move_to_end(url)
        return cached
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": SOURCE_HOST + "/home",
        "Accept": "application/pdf,*/*",
    })
    for attempt in range(1, PDF_TRIES + 1):
        try:
            with urllib.request.urlopen(req, timeout=PDF_TIMEOUT) as r:
                data = r.read()
                ctype = r.headers.get("Content-Type", "?")
            head = data[:5]
            ok = head == PDF_MAGIC
            print(f"[pdf] {'OK' if ok else 'NOT-PDF'} try={attempt} {len(data)}b "
                  f"ctype={ctype} head={head!r} url=...{url[-40:]}", flush=True)
            if ok:
                _PDF_CACHE[url] = data
                _PDF_CACHE.move_to_end(url)
                while len(_PDF_CACHE) > _PDF_CACHE_MAX:
                    _PDF_CACHE.popitem(last=False)
                return data
            return None  # got a response but not a PDF (block page); don't retry
        except Exception as exc:  # noqa: BLE001
            print(f"[pdf] ERROR try={attempt}/{PDF_TRIES} {type(exc).__name__}: "
                  f"{exc} url=...{url[-40:]}", flush=True)
    return None


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, ctype, data, cache=False):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if ctype == "application/pdf":
            # Serve inline (so iframes render it) and from our own origin, which
            # strips the source's X-Frame-Options so the embed is allowed.
            self.send_header("Content-Disposition", "inline")
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
                                  _shell("Inicio", home_page()).encode("utf-8"))
            if parsed.path == "/browse":
                return self._send(200, "text/html; charset=utf-8",
                                  browse_page(sel).encode("utf-8"))
            if parsed.path == "/mesa":
                return self._send(200, "text/html; charset=utf-8",
                                  mesa_page(sel).encode("utf-8"))
            if parsed.path == "/resumen":
                return self._send(200, "text/html; charset=utf-8",
                                  resumen_page().encode("utf-8"))
            if parsed.path == "/anomalias":
                return self._send(200, "text/html; charset=utf-8",
                                  anomalias_page().encode("utf-8"))
            if parsed.path == "/calidad":
                return self._send(200, "text/html; charset=utf-8",
                                  calidad_page().encode("utf-8"))
            if parsed.path == "/tabla":
                return self._send(200, "text/html; charset=utf-8",
                                  tabla_page().encode("utf-8"))
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

