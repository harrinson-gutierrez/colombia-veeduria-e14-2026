"""Shared official-data dashboard views (summary, anomalies, quality, table).

Both the public app (`public_server.py`) and the local review station
(`review_server.py`) render these four read-only views. Keeping them here means
one source of truth: each server reads its own Supabase config and wraps the
returned body in its own shell (sidebar/topbar differ between the two apps).

Each `*_body(url, key)` returns a `(title, html)` tuple. The html is the inner
content (a `<div class="wrap">…</div>` plus its `<script>`), ready to drop into
either shell. `DASHBOARD_CSS` holds the styles those views need; a server that
already includes them (the public app does) can skip re-injecting.

Data is read in the browser via PostgREST with the publishable key (anon,
SELECT-only on the aggregated views). Rows are built with createElement /
textContent, never innerHTML on dynamic data, so a surprising DB value cannot
inject markup.
"""
import json


# CSS for the dashboard views. The public shell already defines the base tokens
# (--brand, --card, .metric, .cta, …); this block adds the analysis-specific
# pieces. The review shell injects this whole block (it lacks them otherwise).
DASHBOARD_CSS = """
 .wrap{padding:1.4rem 1.6rem;max-width:1180px}
 .lead{color:var(--soft);font-size:.95rem;max-width:70ch;margin:.1rem 0 1.3rem}
 .panelcard{background:var(--card);border:1px solid var(--line);border-radius:16px;
   box-shadow:var(--shadow);padding:1.2rem 1.3rem;margin-bottom:1.3rem}
 .panelcard h3{font-family:'Fraunces',serif;font-weight:600;font-size:1.15rem;margin:0 0 .2rem;letter-spacing:-.01em}
 .panelcard .sub{font-size:.82rem;color:var(--soft);margin-bottom:1rem}
 .metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:1rem}
 .metric{background:var(--card);border:1px solid var(--line);border-radius:16px;padding:1.1rem 1.2rem;box-shadow:var(--shadow)}
 .metric .n{font-family:'Fraunces',serif;font-size:2rem;font-weight:600;line-height:1}
 .metric .k{font-size:.78rem;color:var(--soft);margin-top:.4rem}
 .metric.brand .n{color:var(--brand-d)} .metric.flag .n{color:var(--flag)}
 table.data{width:100%;border-collapse:collapse;font-size:.88rem;font-variant-numeric:tabular-nums}
 table.data th{text-align:left;font-weight:600;color:var(--soft);font-size:.72rem;text-transform:uppercase;
   letter-spacing:.05em;padding:.5rem .6rem;border-bottom:2px solid var(--line);white-space:nowrap}
 table.data th.num,table.data td.num{text-align:right}
 table.data td{padding:.55rem .6rem;border-bottom:1px solid var(--line)}
 table.data tr:hover td{background:#f8fafb}
 table.data a{color:var(--brand-d);text-decoration:none;font-weight:600}
 table.data a:hover{text-decoration:underline}
 .sev{display:inline-block;padding:.12rem .5rem;border-radius:99px;font-size:.7rem;font-weight:700;
   text-transform:uppercase;letter-spacing:.03em}
 .sev.alta{background:#fdeaea;color:var(--bad)} .sev.media{background:var(--flag-soft);color:#b45309}
 .sev.baja{background:#eef1f3;color:var(--soft)}
 .pill{display:inline-block;padding:.12rem .5rem;border-radius:99px;font-size:.72rem;font-weight:600;
   background:var(--flag-soft);color:#b45309;margin:0 .25rem .25rem 0}
 .pill.bad{background:#fdeaea;color:var(--bad)}
 .filters{display:flex;gap:.6rem;flex-wrap:wrap;align-items:center;margin-bottom:1rem}
 .filters input,.filters select{padding:.5rem .7rem;border:1px solid #d3dae0;border-radius:9px;font:inherit;background:#fff}
 .filters input{min-width:220px}
 .chartrow{display:flex;align-items:center;gap:.7rem;margin:.35rem 0}
 .chartrow .nm{width:130px;flex:none;font-size:.85rem;font-weight:600}
 .chartrow .track{flex:1;background:#eef1f3;border-radius:99px;height:22px;overflow:hidden;position:relative}
 .chartrow .fill{height:100%;background:linear-gradient(90deg,var(--brand),var(--brand-d));border-radius:99px;
   min-width:2px;transition:width .6s cubic-bezier(.2,.8,.2,1)}
 .chartrow .val{width:64px;flex:none;text-align:right;font-variant-numeric:tabular-nums;font-size:.85rem;color:var(--soft)}
 .muted{color:var(--soft)} .center{text-align:center}
 .loadingrow td{padding:1.4rem;text-align:center;color:var(--soft)}
 .cta{display:inline-flex;align-items:center;gap:.5rem;background:var(--brand-d);color:#fff;
   text-decoration:none;padding:.85rem 1.5rem;border-radius:12px;font-weight:600;box-shadow:var(--shadow)}
 .cta:hover{filter:brightness(1.08)}
"""


def available(url, key) -> bool:
    return bool(url and key)


def unavailable_body(title: str) -> tuple:
    """Inner HTML shown when Supabase is not configured."""
    return title, (
        '<div class="wrap"><div class="panelcard">'
        '<h3>Análisis no disponible</h3><div class="sub">No hay una conexión a '
        'Supabase configurada, así que las vistas de análisis oficial están '
        'desactivadas. Configura <code>SUPABASE_URL</code> y <code>SUPABASE_KEY</code> '
        'en tu <code>.env</code>.</div></div></div>')


def sb_head(url, key) -> str:
    """Expose the read-only Supabase endpoint + small DOM helpers to the page.
    Parameterized by (url, key) so each server passes its own config."""
    return f"""<script>
const SB_URL={json.dumps(url)}, SB_KEY={json.dumps(key)};
async function sbGet(path){{
  const r=await fetch(SB_URL+'/rest/v1/'+path,
    {{headers:{{apikey:SB_KEY, Authorization:'Bearer '+SB_KEY}}}});
  if(!r.ok) throw new Error('REST '+r.status);
  return r.json();
}}
function deptName(c){{return 'Circ. '+c;}}
function mesaHref(r){{
  return '/browse?dept='+encodeURIComponent(r.department_code)+
    '&mun='+encodeURIComponent(r.municipality_code)+
    '&zone='+encodeURIComponent(r.zone_code)+
    '&station='+encodeURIComponent(r.station_code);
}}
function el(t,p){{const n=document.createElement(t);if(p)for(const k in p){{
  if(k==='class')n.className=p[k]; else n[k]=p[k];}}
  for(let i=2;i<arguments.length;i++){{const c=arguments[i];
    if(c==null)continue; n.append(c.nodeType?c:document.createTextNode(String(c)));}}
  return n;}}
function td(text,cls){{return el('td',cls?{{class:cls}}:null,text==null?'—':text);}}
function tdNode(node,cls){{const c=el('td',cls?{{class:cls}}:null);c.append(node);return c;}}
function linkCell(href,label){{return tdNode(el('a',{{href:href}},label));}}
function sevBadge(s){{return el('span',{{class:'sev '+s}},s);}}
function emptyRow(cols,msg){{return el('tr',{{class:'loadingrow'}},el('td',{{colSpan:cols}},msg));}}
function setNum(id,v){{const e=document.getElementById(id);
  if(e)e.textContent=Number(v).toLocaleString('es-CO');}}
function errRow(tbId,cols,e){{document.getElementById(tbId).replaceChildren(
  emptyRow(cols,'No se pudo cargar ('+e.message+').'));}}
</script>"""


def resumen_body(url, key) -> tuple:
    if not available(url, key):
        return unavailable_body("Resumen")
    body = f"""<div class="wrap">
  <p class="lead">Una foto del cruce entre el <b>preconteo (PRE)</b> y el
   <b>escrutinio oficial (ESC)</b> de las actas E14 ya publicadas. Las diferencias
   son puntos de partida para revisión ciudadana, no conclusiones.</p>
  <div class="metrics" style="margin-bottom:1.3rem">
    <div class="metric brand"><div class="n" id="m_total">—</div><div class="k">Mesas con datos oficiales</div></div>
    <div class="metric"><div class="n" id="m_esc">—</div><div class="k">Ya escrutadas (ESC)</div></div>
    <div class="metric"><div class="n" id="m_ok">—</div><div class="k">Cuadran PRE = ESC</div></div>
    <div class="metric flag"><div class="n" id="m_dif">—</div><div class="k">Con diferencia PRE ≠ ESC</div></div>
    <div class="metric"><div class="n" id="m_alta">—</div><div class="k">Diferencias altas (≥50)</div></div>
    <div class="metric"><div class="n" id="m_pend">—</div><div class="k">Aún sin escrutinio</div></div>
    <div class="metric"><div class="n" id="m_qual">—</div><div class="k">Alertas de proceso</div></div>
    <div class="metric"><div class="n" id="m_dep">—</div><div class="k">Circunscripciones</div></div>
  </div>
  <div class="panelcard">
    <h3>Diferencias por circunscripción</h3>
    <div class="sub">Mesas con discrepancia real (ESC distinto de PRE), por código oficial de
     circunscripción de la Registraduría. Ordenado de mayor a menor.</div>
    <div id="chart"><div class="muted">Cargando…</div></div>
  </div>
</div>
{sb_head(url, key)}
<script>
(async function(){{
  try{{
    const s=(await sbGet('v_summary?select=*'))[0];
    setNum('m_total',s.total_mesas); setNum('m_esc',s.con_escrutinio); setNum('m_ok',s.cuadradas);
    setNum('m_dif',s.con_discrepancia); setNum('m_alta',s.disc_alta); setNum('m_pend',s.sin_escrutinio);
    setNum('m_qual',(s.sobre_no_bueno+s.recontadas+s.con_tachaduras+s.excluidas));
    setNum('m_dep',s.departamentos);
    const deps=await sbGet('v_by_department?select=*&discrepancias=gt.0&order=discrepancias.desc&limit=15');
    const c=document.getElementById('chart'); c.replaceChildren();
    if(!deps.length){{c.append(el('div',{{class:'muted'}},'Sin discrepancias registradas.'));return;}}
    const max=Math.max(1,...deps.map(d=>d.discrepancias));
    for(const d of deps){{
      const fill=el('div',{{class:'fill'}}); fill.style.width='0';
      const row=el('div',{{class:'chartrow'}},
        el('div',{{class:'nm'}},deptName(d.department_code)),
        el('div',{{class:'track'}},fill),
        el('div',{{class:'val'}},d.discrepancias));
      c.append(row);
      requestAnimationFrame(()=>{{fill.style.width=(100*d.discrepancias/max)+'%';}});
    }}
  }}catch(e){{document.getElementById('chart').replaceChildren(
    el('div',{{class:'muted'}},'No se pudo cargar el análisis ('+e.message+').'));}}
}})();
</script>"""
    return "Resumen del análisis", body


def anomalias_body(url, key) -> tuple:
    if not available(url, key):
        return unavailable_body("Anomalías")
    body = f"""<div class="wrap">
  <p class="lead">Mesas donde el <b>escrutinio oficial difiere del preconteo</b>.
   Ordenadas por el tamaño de la diferencia. Recuerda: una diferencia puede tener
   explicaciones legítimas (reconteo, corrección de jurados). Es una señal para
   <b>mirar el acta</b>, no una acusación.</p>
  <div class="panelcard">
    <div class="filters">
      <input id="q" type="search" placeholder="Filtrar por código de mesa…">
      <select id="sev"><option value="">Toda gravedad</option>
        <option value="alta">Alta (≥50)</option><option value="media">Media (10–49)</option>
        <option value="baja">Baja (1–9)</option></select>
      <span class="muted" id="count"></span>
    </div>
    <table class="data"><thead><tr>
      <th>Mesa</th><th>Circ.</th><th class="num">PRE</th><th class="num">ESC</th>
      <th class="num">Diferencia</th><th>Gravedad</th><th></th>
    </tr></thead><tbody id="tb"></tbody></table>
  </div>
</div>
{sb_head(url, key)}
<script>
let ROWS=[];
function render(){{
  const q=document.getElementById('q').value.trim().toLowerCase();
  const sev=document.getElementById('sev').value;
  const tb=document.getElementById('tb');
  const f=ROWS.filter(r=>(!q||r.mesa_code.toLowerCase().includes(q))&&(!sev||r.severidad===sev));
  document.getElementById('count').textContent=f.length+' de '+ROWS.length+' mesas';
  tb.replaceChildren();
  if(!f.length){{tb.append(emptyRow(7,'Sin resultados.'));return;}}
  for(const r of f.slice(0,500)){{
    const dif=el('b',null,(r.dif_neta>0?'+':'')+r.dif_neta);
    tb.append(el('tr',null,
      td(r.mesa_code), td(deptName(r.department_code)),
      td(r.votos_pre,'num'), td(r.votos_esc,'num'),
      tdNode(dif,'num'), tdNode(sevBadge(r.severidad)),
      linkCell(mesaHref(r),'ver acta →')));
  }}
  if(f.length>500) tb.append(emptyRow(7,'Mostrando las primeras 500 de '+f.length+'.'));
}}
(async function(){{
  try{{ ROWS=await sbGet('v_anomalies?select=*&order=dif_abs.desc'); render(); }}
  catch(e){{ errRow('tb',7,e); }}
}})();
document.getElementById('q').addEventListener('input',render);
document.getElementById('sev').addEventListener('change',render);
</script>"""
    return "Lista de anomalías", body


def calidad_body(url, key) -> tuple:
    if not available(url, key):
        return unavailable_body("Calidad del proceso")
    body = f"""<div class="wrap">
  <p class="lead">Señales del <b>proceso</b> de cada mesa registradas en el análisis
   oficial: estado del sobre, reconteo por jurados, tachaduras o enmendaduras, y
   mesas excluidas. No miden votos: miden cómo se manejó el acta.</p>
  <div class="metrics" style="margin-bottom:1.3rem">
    <div class="metric"><div class="n" id="q_sobre">—</div><div class="k">Sobre no “BUENO”</div></div>
    <div class="metric"><div class="n" id="q_recon">—</div><div class="k">Recontadas por jurados</div></div>
    <div class="metric flag"><div class="n" id="q_tach">—</div><div class="k">Con tachaduras</div></div>
    <div class="metric"><div class="n" id="q_excl">—</div><div class="k">Excluidas</div></div>
    <div class="metric"><div class="n" id="q_pend">—</div><div class="k">Aún sin escrutinio</div></div>
  </div>
  <div class="panelcard">
    <h3>Mesas con alguna alerta de proceso</h3>
    <div class="sub">Cada etiqueta indica una condición a contrastar con el acta original.</div>
    <table class="data"><thead><tr>
      <th>Mesa</th><th>Circ.</th><th>Alertas</th><th></th>
    </tr></thead><tbody id="tb"></tbody></table>
  </div>
</div>
{sb_head(url, key)}
<script>
function up(x){{return (x||'').toString().toUpperCase();}}
function pill(text,bad){{return el('span',{{class:bad?'pill bad':'pill'}},text);}}
(async function(){{
  try{{
    const s=(await sbGet('v_summary?select=*'))[0];
    setNum('q_sobre',s.sobre_no_bueno); setNum('q_recon',s.recontadas);
    setNum('q_tach',s.con_tachaduras); setNum('q_excl',s.excluidas); setNum('q_pend',s.sin_escrutinio);
    const rows=await sbGet('v_quality?select=*&order=department_code.asc&limit=1000');
    const tb=document.getElementById('tb'); tb.replaceChildren();
    if(!rows.length){{tb.append(emptyRow(4,'Sin alertas de proceso registradas.'));return;}}
    for(const r of rows){{
      const cell=el('td');
      if(up(r.estado_sobre)&&up(r.estado_sobre)!=='BUENO')cell.append(pill('sobre: '+r.estado_sobre));
      if(up(r.recontada_jurados)==='SI')cell.append(pill('recontada'));
      if(up(r.tachaduras)==='SI')cell.append(pill('tachaduras'));
      if(up(r.excluida)==='SI')cell.append(pill('EXCLUIDA',true));
      tb.append(el('tr',null,
        td(r.mesa_code), td(deptName(r.department_code)), cell,
        linkCell(mesaHref(r),'ver acta →')));
    }}
  }}catch(e){{ errRow('tb',4,e); }}
}})();
</script>"""
    return "Calidad del proceso", body


def tabla_body(url, key) -> tuple:
    if not available(url, key):
        return unavailable_body("Todas las mesas")
    body = f"""<div class="wrap">
  <p class="lead">Todas las mesas con datos oficiales (PRE/ESC). Filtra por código
   o estado para explorar libremente. La tabla carga por páginas.</p>
  <div class="panelcard">
    <div class="filters">
      <input id="q" type="search" placeholder="Código de mesa (ej. 01-001-27)…">
      <select id="estado">
        <option value="">Todos los estados</option>
        <option value="dif">Con diferencia PRE≠ESC</option>
        <option value="ok">Cuadran PRE=ESC</option>
        <option value="pend">Sin escrutinio</option>
      </select>
      <span class="muted" id="count"></span>
    </div>
    <table class="data"><thead><tr>
      <th>Mesa</th><th class="num">PRE</th><th class="num">ESC</th>
      <th class="num">Dif.</th><th>Estado</th><th></th>
    </tr></thead><tbody id="tb"></tbody></table>
    <div class="center" style="margin-top:1rem">
      <button id="more" class="cta" style="margin:0;display:none">Cargar más</button>
    </div>
  </div>
</div>
{sb_head(url, key)}
<script>
const PAGE=200; let offset=0, done=false, busy=false;
function estadoClause(){{
  const v=document.getElementById('estado').value;
  if(v==='dif')  return '&votos_esc=not.is.null&dif_neta=neq.0';
  if(v==='ok')   return '&votos_esc=not.is.null&dif_neta=eq.0';
  if(v==='pend') return '&votos_esc=is.null';
  return '';
}}
function qClause(){{
  const q=document.getElementById('q').value.trim();
  return q? '&mesa_code=ilike.'+encodeURIComponent('*'+q+'*') : '';
}}
function badge(r){{
  if(r.votos_esc==null) return sevBadge('baja');
  if(r.dif_neta===0){{const s=el('span',{{class:'sev'}},'cuadra');
    s.style.background='var(--brand-soft)';s.style.color='var(--brand-d)';return s;}}
  const lvl=Math.abs(r.dif_neta)>=50?'alta':(Math.abs(r.dif_neta)>=10?'media':'baja');
  return el('span',{{class:'sev '+lvl}},'dif. '+r.dif_neta);
}}
async function load(reset){{
  if(busy)return; busy=true;
  const tb=document.getElementById('tb');
  if(reset){{offset=0;done=false;tb.replaceChildren(emptyRow(6,'Cargando…'));}}
  try{{
    const path='official_data?select=mesa_code,department_code,municipality_code,zone_code,station_code,votos_pre,votos_esc,dif_neta'+
      qClause()+estadoClause()+'&order=mesa_code.asc&limit='+PAGE+'&offset='+offset;
    const rows=await sbGet(path);
    if(reset)tb.replaceChildren();
    if(reset&&!rows.length){{tb.append(emptyRow(6,'Sin resultados.'));}}
    for(const r of rows){{
      tb.append(el('tr',null,
        td(r.mesa_code), td(r.votos_pre,'num'), td(r.votos_esc,'num'),
        td(r.dif_neta==null?null:(r.dif_neta>0?'+':'')+r.dif_neta,'num'),
        tdNode(badge(r)), linkCell(mesaHref(r),'ver →')));
    }}
    offset+=rows.length; done=rows.length<PAGE;
    document.getElementById('more').style.display=done?'none':'inline-flex';
    document.getElementById('count').textContent=offset+' mesas cargadas'+(done?' (fin)':'');
  }}catch(e){{ errRow('tb',6,e); }}
  busy=false;
}}
let t=null;
document.getElementById('q').addEventListener('input',()=>{{clearTimeout(t);t=setTimeout(()=>load(true),300);}});
document.getElementById('estado').addEventListener('change',()=>load(true));
document.getElementById('more').addEventListener('click',()=>load(false));
load(true);
</script>"""
    return "Todas las mesas", body
