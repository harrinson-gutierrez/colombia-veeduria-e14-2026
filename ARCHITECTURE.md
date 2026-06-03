# Arquitectura — e14-colombia-audit

El **pipeline local** corre en una sola máquina. No hay nube, ni sincronización,
ni servicio público. Una base de datos SQLite es la única fuente de verdad, los
PDF descargados viven en disco, y los reportes se generan localmente.

(Aparte está la **app web pública** `public_server.py`, que sí se puede
desplegar; ver la sección al final.)

```
┌──────────────────────────── UNA MÁQUINA (solo local) ─────────────────────────────┐
│                                                                                    │
│   agent.py (bucle reanudable)                                                      │
│     ├─ Etapa 1  download.py   trae PDFs      ──┐                                    │
│     ├─ Etapa 2  ocr.py        OCR / visión   │   SQLite (data/pipeline.db)          │
│     └─ Etapa 3  validate.py   cuadre de votos ─┘   única fuente de verdad           │
│                          │                                                          │
│                          ▼                                                          │
│   PDFs en disco: data/forms/                                                        │
│                          │                                                          │
│                          ▼                                                          │
│   report.py  ─────────►  data/reports/summary.html  (reporte local, EN/ES)         │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────┘
```

Todo se queda en la máquina: los PDF, la base de datos y los reportes. No se
sube nada a ningún lado.

## Modelo de datos

`data/pipeline.db` guarda una fila por mesa con un **estado por etapa**:

- `download_status`  — pending / ok / failed / not_pdf
- `ocr_status`       — pending / ok / failed
- `validation_status` — pending / ok / flagged / failed

Cada etapa solo avanza su propio campo de estado, así que el progreso de una
etapa nunca sobreescribe el de otra. Los hallazgos (discrepancias y confianza de
lectura) se guardan junto a la mesa y siempre referencian el PDF original en
`data/forms/`.

## Bucle del agente

```
cada ciclo:
  1. download  lote de mesas pending/failed              -> marca ok/failed/not_pdf
  2. ocr       lote donde download_status = ok            -> guarda lectura, marca ok/failed
  3. validate  lote donde ocr_status = ok                -> cuadre de votos, marca flagged
  4. dormir N segundos  (respetuoso con el servidor fuente)
```

Idempotente: cada paso solo toma trabajo pendiente. Matar y reiniciar el agente
reanuda desde el estado en SQLite sin perder ni repetir progreso. Los reportes se
producen bajo demanda con `report.py` contra la misma base de datos.

## Cortesía con la fuente

El descargador usa concurrencia limitada y pausas entre peticiones. Los datos
vienen de infraestructura electoral pública publicada por la Registraduría de
Colombia; el pipeline nunca debe sobrecargarla. Los PDF ya descargados y válidos
se omiten, así que volver a correr no agrega carga innecesaria.

## Privacidad

Solo local por diseño. El pipeline no sube nada, no llama a ningún servicio
externo propio, y no "telefonea a casa". El único tráfico de salida es traer los
PDF públicos de la fuente. Todos los resultados — la base de datos, los PDF y los
reportes generados — se quedan en la máquina del operador. Solo el operador
decide si comparte algún reporte y cómo.

## Honestidad y seguridad de los datos (no negociable)

- Una mesa `flagged` significa **"necesita revisión manual"**, NO "fraude".
- Cada hallazgo guarda la confianza de lectura y enlaza al PDF original.
- Los reportes deben etiquetar las cifras como *lecturas automáticas sujetas a verificación*.
- El OCR de letra manuscrita falla; muchas discrepancias vendrán de la lectura,
  no de las actas mismas.

## App web pública (`public_server.py`)

Camino alterno para revisión ciudadana sin instalar nada (ver el README). No usa
visión, GPU ni base de datos: lee un índice comprimido versionado, trae cada PDF
por proxy bajo demanda desde la Registraduría, y guarda los veredictos del
visitante en su propio navegador (`localStorage`). Se despliega gratis (ej.
Render) porque usa solo la librería estándar de Python.

## Estado de implementación

- [x] Etapa 1 Descarga (SQLite, reanudable, reintentable)
- [x] Gestión de SQLite (db / import_index / status)
- [x] Etapa 2 Lectura: OCR (hoja completa) + visión local (Ollama qwen2.5vl)
- [x] Etapa 3 validate.py: cuadre de votos, marca discrepancias
- [x] Bucle orquestador agent.py
- [x] Generador de reporte local report.py
- [x] Estación de revisión (review_server.py): navegador jerárquico + veredictos
- [x] App web pública (public_server.py): revisión ciudadana sin instalar
