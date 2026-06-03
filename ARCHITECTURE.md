# Architecture — vote-verify

A local-only pipeline that runs on a single machine. There is no cloud, no
sync, and no public service. A SQLite database is the single source of truth,
the downloaded PDFs live on disk, and reports are generated locally.

```
┌──────────────────────────── ONE MACHINE (local-only) ─────────────────────────────┐
│                                                                                    │
│   agent.py (resumable loop)                                                        │
│     ├─ Stage 1  download.py   fetch PDFs    ──┐                                     │
│     ├─ Stage 2  ocr.py        OCR           │   SQLite (data/pipeline.db)           │
│     └─ Stage 3  validate.py   vote-sum check ─┘   single source of truth            │
│                          │                                                          │
│                          ▼                                                          │
│   PDFs on disk: data/forms/                                                         │
│                          │                                                          │
│                          ▼                                                          │
│   report.py  ─────────►  data/reports/summary.html  (local report, EN/ES)          │
│                                                                                    │
└────────────────────────────────────────────────────────────────────────────────┘
```

Everything stays on the machine: the PDFs, the database, and the reports.
Nothing is uploaded anywhere.

## Data model

`data/pipeline.db` holds one row per polling table with a **per-stage status**:

- `download_status`  — pending / ok / failed / not_pdf
- `ocr_status`       — pending / ok / failed
- `validate_status`  — pending / ok / flagged / failed

Each stage only advances its own status field, so progress for one stage never
overwrites another. Findings (mismatches and OCR confidence) are stored
alongside the polling table and always reference the original PDF in
`data/forms/`.

## Agent loop

```
each cycle:
  1. download  batch of pending/failed polling tables   -> mark ok/failed/not_pdf
  2. ocr       batch where download_status = ok          -> store text, mark ok/failed
  3. validate  batch where ocr_status = ok               -> vote-sum check, mark flagged
  4. sleep N seconds  (respectful to the source server)
```

Idempotent: each step only picks up pending work. Killing and restarting the
agent resumes from the SQLite status without losing or repeating progress.
Reports are produced on demand by `report.py` against the same database.

## Politeness toward the source

The downloader uses limited concurrency and pauses between requests. The data
comes from public election infrastructure published by Colombia's
Registraduría; the pipeline must never overload it. Already-downloaded, valid
PDFs are skipped, so re-runs add no unnecessary load.

## Privacy

Local-only by design. The pipeline does not upload anything, does not call any
external service of its own, and does not phone home. The only outbound traffic
is fetching the public PDFs from the source. All results — the database, the
PDFs, and the generated reports — stay on the operator's machine. The operator
alone decides whether and how to share any generated report.

## Data honesty and safety (non-negotiable)

- A `flagged` polling table means **"needs manual review"**, NOT "fraud".
- Every finding stores the OCR confidence and links to the original PDF.
- Reports must label figures as *automated readings subject to verification*.
- Handwriting OCR fails; many mismatches will come from the OCR, not from the
  tally sheets themselves.

## Implementation status

- [x] Stage 1 Download (SQLite, resumable, retryable)
- [x] SQLite management (db / import_index / status)
- [x] Stage 2 OCR built (full page) — needs Tesseract installed
- [x] Stage 3 validate.py skeleton built — needs real-layout tuning
- [x] agent.py orchestrator loop
- [x] report.py local report generator
