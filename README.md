# colombia-veeduria-e14-2026

**🇪🇸 Español** · [🇬🇧 English](README.en.md)

### 🟢 App en vivo: **https://colombia-veeduria-e14-2026.onrender.com**

> Ábrela en el navegador, sin instalar nada. Cualquiera puede revisar las actas,
> escribir los números que ve y sumar su reporte al consenso ciudadano.

[![App en vivo](https://img.shields.io/badge/App%20en%20vivo-Abrir-10b981?logo=render&logoColor=white)](https://colombia-veeduria-e14-2026.onrender.com)
[![Licencia: MIT](https://img.shields.io/badge/Licencia-MIT-yellow.svg)](LICENSE)
![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)
![País](https://img.shields.io/badge/Colombia-E14%202026-yellow?labelColor=blue)

**Herramienta de auditoría del formulario E14 — elecciones presidenciales de Colombia 2026.**

Descarga las actas E14 escaneadas que publica la **Registraduría** en su sitio
oficial de resultados (`divulgacione14presidente.registraduria.gov.co`), lee los
conteos manuscritos con un modelo de visión **local**, verifica que las sumas
reportadas cuadren, y permite que una persona revise cada acta marcada contra el
PDF original. El objetivo es hacer la auditoría de los resultados publicados
**transparente y reproducible** — todo en tu propia máquina, sin subir nada.

> **Marcar una mesa NO es declarar fraude.** La visión y el OCR leen mal la
> letra manuscrita, así que las discrepancias suelen ser errores de lectura.
> Cada hallazgo enlaza al PDF original para que una persona lo verifique. Ver la
> nota al final.

**Inicio rápido:** instala las herramientas (ver [INSTALL_OCR.md](INSTALL_OCR.md)),
luego sigue [Flujo del pipeline](#flujo-del-pipeline). Para revisar actas en el
navegador, ve a [Estación de revisión](#estación-de-revisión-con-persona-en-el-ciclo).

## Capturas

**Dashboard de análisis oficial** — cruza el preconteo (PRE) con el escrutinio
oficial (ESC) de las actas ya publicadas y resalta lo que merece revisión. Las
métricas y el gráfico se calculan en vivo sobre las mesas cargadas:

![Resumen del análisis](docs/dashboard-resumen.png)

**Lista de anomalías** — mesas donde el escrutinio difiere del preconteo,
graduadas por gravedad y filtrables. Una diferencia es una señal para mirar el
acta, no una acusación:

![Lista de anomalías](docs/dashboard-anomalias.png)

**Calidad del proceso** y **tabla filtrable de todas las mesas** completan el
tablero (alertas de sobre/reconteo/tachaduras y exploración paginada):

![Calidad del proceso](docs/dashboard-calidad.png)
![Todas las mesas](docs/dashboard-tabla.png)

**Navegador jerárquico** — recorre como en el sitio oficial: Departamento →
Municipio → Zona → Puesto → mesas, con el avance por nivel (`X/Y verificadas`)
y etiquetas de estado:

![Navegador jerárquico](docs/browse.png)

**Estación de revisión** — abre una mesa para verificarla: el PDF a la
izquierda, los números (leídos automáticamente por el modelo de visión local,
que tú confirmas) a la derecha, incluyendo un recorte ampliado de la banda de
blanco/nulos/no-marcados/total:

![Estación de revisión](docs/station.png)

La primera fuente soportada es el **formulario E14** que publica la Registraduría
de Colombia. La arquitectura es agnóstica a la fuente: cualquier jurisdicción que
publique actas escaneadas puede agregarse como una fuente nueva.

Todo corre localmente. Una base de datos SQLite (`data/pipeline.db`) es la única
fuente de verdad, los PDF viven en `data/forms`, y los reportes se escriben en
`data/reports/`. No se sube nada.

## Dos formas de usarlo

| | **Pipeline local** (flujo principal del repo) | **App web pública** (`public_server.py`) |
|---|---|---|
| Para | un auditor con un PC capaz | cualquiera, sin instalar |
| Visión | Ollama local lee los números solo | ninguna — la persona escribe lo que ve |
| Instalar | Python + Tesseract + Poppler + Ollama | nada (el visitante solo abre una URL) |
| Almacenamiento | SQLite + PDF descargados en disco | veredictos en el navegador del visitante; reportes y consenso en Supabase (opcional) |
| PDF | descargados y cacheados | traídos por proxy bajo demanda desde la Registraduría |
| Colaboración | un solo auditor | **consenso entre varias personas** (una mesa se confirma cuando ≥3 coinciden) |
| Análisis oficial | el mismo **dashboard en vivo** (resumen, anomalías, calidad, tabla), compartido | **dashboard en vivo** (resumen, anomalías, calidad, tabla) |
| Hosting | tu máquina | cualquier host Python gratis (ej. Render) |

> El **dashboard de datos oficiales** es el mismo en las dos apps: vive en
> `dashboard_views.py` (una sola fuente de verdad) y se activa en ambas cuando
> hay Supabase configurado.

### Consenso colaborativo y dashboard de datos oficiales (opcional, Supabase)

La app pública funciona sola sin configurar nada: cualquiera abre una mesa,
escribe los números y la app cuadra la suma localmente. Si además configuras un
proyecto de **Supabase** (gratis), se activan dos capacidades:

- **Consenso entre personas.** Quien quiera sumar su lectura entra con su correo
  (enlace mágico, sin contraseña) y envía su reporte. Una mesa queda
  **confirmada por consenso** cuando **≥3 personas coinciden** de forma
  independiente. Nadie puede borrar reportes ajenos; el navegador solo lee el
  consenso público y escribe el reporte propio (RLS de Supabase lo restringe).
- **Dashboard de datos oficiales.** A partir de un análisis ciudadano del cruce
  PRE/ESC (cargado en la tabla `official_data`), cuatro vistas de solo lectura
  resumen el panorama: `/resumen` (métricas + gráfico por circunscripción),
  `/anomalias` (discrepancias reales graduadas por gravedad), `/calidad`
  (alertas de sobre, reconteo, tachaduras, exclusión) y `/tabla` (todas las
  mesas, filtrable y paginada). Las cifras se calculan en el servidor con vistas
  de Postgres y se leen desde el navegador con la **publishable key** (anónima,
  solo lectura) — nunca la `service_role`. Estas cuatro vistas viven en
  `dashboard_views.py` y aparecen **tanto en la app pública como en la estación
  de revisión local** (sección «Análisis oficial» del menú lateral), idénticas
  en ambas.

Configúralo copiando `.env.example` a `.env` y poniendo tu `SUPABASE_URL` y la
`SUPABASE_KEY` **publishable**. Si no hay `.env`, la app simplemente desactiva el
consenso y el dashboard y sigue funcionando en modo local.

### Correr la app web pública

> **Ya está desplegada y abierta al público:**
> **https://colombia-veeduria-e14-2026.onrender.com** — no necesitas correrla tú
> para usarla. Estos pasos son solo si quieres levantar tu propia instancia.

Cero instalación para los visitantes. Lee un índice pequeño y versionado
(`data/public_index.csv.gz`), trae cada PDF oficial por proxy bajo demanda, y
guarda los números y veredictos del visitante en su propio navegador
(`localStorage`).

```bash
python build_public_index.py     # una vez: construye data/public_index.csv.gz
python public_server.py          # sirve http://localhost:8080
```

**Desplegar gratis en Render:** sube este repo, crea un servicio tipo *Blueprint*
en render.com — lee el archivo `render.yaml`. Sin GPU. El blueprint **no** corre
`pip install` (la app pública es stdlib pura), así que arranca limpio. Si quieres
consenso y dashboard, Render te pedirá `SUPABASE_URL` y `SUPABASE_KEY`
(publishable) al desplegar — no quedan en el repo. Después, en Supabase
→ *Authentication → URL Configuration*, añade tu URL de Render a *Site URL* y
*Redirect URLs* para que el enlace mágico funcione en producción. En el plan
gratis la app "duerme" tras inactividad (la primera visita tarda ~30 s en
despertar).

## Estructura del sitio fuente (confirmada empíricamente)

- **Metadatos**: archivos JSON estáticos servidos en `/assets/temis/divipol_json/`.
  El archivo maestro es `allTransmissionCodes.json` (~36 MB): un nodo por mesa
  con su `expectedName` (el nombre del PDF) y sus coordenadas de ubicación.
- **PDF del acta**, patrón de URL **confirmado 12/12** observando el tráfico real:

  ```
  /assets/temis/pdf/{dpto}/{mun}/{zona:3dígitos}/{codigoPuesto}/{numeroMesa}/PRE/{expectedName}
  ```

  Detalle clave: la **zona va con relleno a 3 dígitos** (`09` -> `009`); el
  código de puesto y el número de mesa van tal cual.
- **Protección anti-bot**: el sitio fuente está detrás de un CDN/WAF. Un
  navegador real siempre pasa; un cliente Python simple puede necesitar las
  cookies del navegador (ver paso 2.5).
- **Universo**: ~121.592 mesas (estado `11` = publicada; `3` = un subconjunto
  especial).

## Flujo del pipeline

El pipeline corre en tres etapas, todas localmente contra la base de datos
SQLite. El orquestador las corre en un bucle reanudable; también puedes correr
cada etapa por separado.

```bash
# 1. Preparar los metadatos y cargar el índice en SQLite
python inspect_meta.py          # descarga los JSON de metadatos
python discover.py              # construye data/index.csv (una fila por mesa)
python import_index.py          # carga el índice en data/pipeline.db
python check_access.py          # ¿pasa Python simple el WAF? te dice qué camino usar

# 2. Correr todo el pipeline para un departamento (bucle reanudable)
python agent.py --dept 16       # descarga -> ocr -> valida, en bucle hasta terminar

# ...o correr las etapas por separado:
python download.py --status 11              # Etapa 1: descarga reanudable
python download.py --status 11 --dept 16    # o filtrar por departamento
python ocr.py --status 11                   # Etapa 2: OCR de los PDF descargados
python validate.py --dept 16                # Etapa 3: cuadre de votos, marca discrepancias

# Si el WAF bloquea a Python simple, usa el camino de descarga por navegador:
pip install --user playwright && python -m playwright install chromium
python browser_download.py --status 11

# 3. Revisar el avance y generar el reporte local
python status.py                # reporta el avance del pipeline por etapa
python verify_mapping.py        # verifica el mapeo índice -> URL -> ruta local
python report.py --dept 16      # escribe data/reports/summary.html (ábrelo en un navegador)
```

Empieza siempre con `--limit 20` para validar antes de descargar todo.

Los PDF descargados se escriben en `data/forms`. El reporte local se escribe en
`data/reports/summary.html` (bilingüe EN/ES).

## Etapas

| Etapa | Nombre | Qué hace |
|---|---|---|
| 1 | Descarga | Trae los PDF de las actas; reanudable; valida los bytes mágicos `%PDF` |
| 2 | Lectura (OCR / visión) | Lee los números manuscritos de cada PDF y los guarda |
| 3 | Validación | Verifica que las sumas de votos cuadren; marca las discrepancias |

## Estación de revisión (con persona en el ciclo)

Una marca nunca es la última palabra — una persona confirma cada una contra el
PDF original. La estación de revisión es una app web local para eso:

```bash
python review_server.py --dept 16     # sirve http://127.0.0.1:8765
```

- **Navega como en el sitio oficial**: Departamento → Municipio → Zona → Puesto →
  mesas. Cada tarjeta muestra tu avance (`X/Y verificadas`) y etiquetas por estado.
- **Abre cualquier mesa**: su PDF se descarga bajo demanda si falta, y el modelo
  de visión local lee los números solo para pre-llenar el formulario (una
  propuesta que tú confirmas).
- **Veredicto**: marca cada mesa Verificada / Anomalía / Dudosa. Los veredictos se
  guardan en la tabla local `reviews`; nada sale de la máquina.
- **Dashboard de datos oficiales**: si hay Supabase configurado, el menú lateral
  muestra la sección «Análisis oficial» (Resumen, Anomalías, Calidad del proceso,
  Todas las mesas) — las mismas cuatro vistas de la app pública, para priorizar
  qué mesas revisar primero.

### Leer la letra manuscrita (visión local)

Tesseract lee los rótulos impresos pero no los conteos manuscritos. Para esos,
`vision.py` llama a un modelo de visión **local** vía Ollama (`qwen2.5vl`),
enteramente en la máquina. La banda de blanco/nulos/no-marcados se lee de un
recorte ajustado que incluye los rótulos (`crop_totals.py`) — leerla de la hoja
completa devuelve 0. Cada número es una propuesta que el revisor confirma contra
el PDF; la visión lee mal la letra, así que su salida es un punto de partida, no
un veredicto.

## Archivos

| Archivo | Qué hace |
|---|---|
| `config.py` | URLs, headers, y `pdf_url(node)` que implementa el patrón confirmado |
| `db.py` | Esquema SQLite y ayudantes (`polling_tables`, `findings`) para `data/pipeline.db` |
| `inspect_meta.py` | Descarga e inspecciona los archivos JSON de metadatos |
| `discover.py` | Genera `data/index.csv` (mesa -> URL del acta -> ruta local) |
| `import_index.py` | Carga `data/index.csv` en la base de datos SQLite |
| `verify_mapping.py` | Verifica el mapeo índice -> URL -> ruta local |
| `check_access.py` | Diagnostica si Python simple pasa el WAF; soporta `cookies.txt` |
| `download.py` | Descargador de la Etapa 1: reanudable, concurrente, valida `%PDF` |
| `ocr.py` | Corredor de OCR de la Etapa 2 sobre los PDF descargados |
| `validate.py` | Cuadre de votos de la Etapa 3; marca discrepancias para revisión manual |
| `agent.py` | Orquesta las Etapas 1–3 en un bucle reanudable |
| `status.py` | Reporta el avance del pipeline por etapa desde la base de datos |
| `report.py` | Genera el reporte local en `data/reports/summary.html` |
| `browser_download.py` | Camino de descarga alternativo usando Playwright (un navegador real) |
| `vision.py` | Lector de visión local (Ollama) para números manuscritos + banda de totales |
| `crop_totals.py` | Recorta la banda rotulada de blanco/nulos/no-marcados/total para la visión |
| `candidates.py` | Lista maestra fija de candidatos + emparejamiento difuso de nombres |
| `runner.py` | Corredor del pipeline en proceso usado por la estación de revisión |
| `review_server.py` | App web local: navegador jerárquico + estación de revisión + dashboard de datos oficiales |
| `public_server.py` | App web pública: navegador + entrada manual + consenso (Supabase) + dashboard de datos oficiales |
| `dashboard_views.py` | Las cuatro vistas del dashboard (resumen, anomalías, calidad, tabla), compartidas por ambas apps |
| `build_public_index.py` | Construye `data/public_index.csv.gz` para la app web pública |
| `parse_official.py` | Convierte el análisis PRE/ESC de terceros en `data/official/official_by_mesa.csv.gz` |
| `load_official.py` | Carga (bulk) `official_data` en Supabase con la `service_role` key (solo local) |
| `consolidate.py` | Agrega los resultados confirmados de las mesas revisadas |

## Principios de diseño

- **Solo local**: la base de datos, los PDF y los reportes se quedan en tu
  máquina. No se sube nada; el único tráfico de salida es traer los PDF públicos.
- **Reanudable**: omite los PDF ya descargados y válidos (bytes mágicos `%PDF`);
  el estado por etapa en SQLite te deja parar y reanudar en cualquier momento.
- **Respetuoso**: concurrencia limitada y pausas. Es infraestructura electoral
  pública; el pipeline nunca debe sobrecargarla.
- **Auditable**: `data/index.csv` enlaza cada PDF con su
  departamento/municipio/zona/puesto/mesa, su código de transmisión, estado, y la
  URL original de la fuente.
- **Robusto**: el servidor responde `200 + text/html` (la cáscara de la SPA) para
  rutas inexistentes, así que los archivos se validan por **bytes mágicos**, nunca
  por el código de estado HTTP.

## Importante: las actas marcadas necesitan revisión manual, no son "fraude confirmado"

Una acta marcada significa **"necesita revisión manual"** — **no** es prueba de
fraude. El OCR y la digitalización producen errores que parecen anomalías; muchas
discrepancias vienen de la etapa de lectura, no de las actas mismas. Cada hallazgo
enlaza al PDF original para que una persona lo verifique directamente. Esta
disciplina es lo que hace el trabajo creíble en lugar de descartable.
