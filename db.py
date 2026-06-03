"""Tally-form audit pipeline management (SQLite, standard library only).

One row per polling table. Each stage tracks its own status, so the pipeline
is resumable and idempotent: every run processes only what is pending or failed.

Statuses (per stage):
    pending   -> not attempted yet
    ok        -> success
    failed    -> recoverable failure (will be retried)
    not_pdf   -> the server did not return a PDF (form not published / invalid URL)
    skipped   -> not applicable

Tables:
    polling_tables -> identity + URL + local path + per-stage status + counters
    votes          -> results read from the tally form per table/candidate (OCR stage)
    validations    -> internal validation findings per polling table (stage 3)
"""
import os
import sqlite3
from contextlib import contextmanager

import config

DB_PATH = f"{config.DATA_DIR}/pipeline.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS polling_tables (
    transmission_code  TEXT PRIMARY KEY,
    status_group       TEXT,
    status             TEXT,
    department_code    TEXT,
    department_name    TEXT,
    municipality_code  TEXT,
    zone_code          TEXT,
    station_code       TEXT,
    station_name       TEXT,
    table_number       TEXT,
    expected_name      TEXT,
    pdf_url            TEXT,
    local_path         TEXT,
    -- stage 1: download
    download_status    TEXT DEFAULT 'pending',
    download_attempts  INTEGER DEFAULT 0,
    download_error     TEXT,
    file_bytes         INTEGER,
    -- stage 2: OCR
    ocr_status         TEXT DEFAULT 'pending',
    ocr_attempts       INTEGER DEFAULT 0,
    ocr_error          TEXT,
    ocr_text           TEXT,
    -- stage 3: validation
    validation_status  TEXT DEFAULT 'pending',
    flagged            INTEGER DEFAULT 0,
    updated_at         TEXT
);

CREATE INDEX IF NOT EXISTS idx_download ON polling_tables(download_status);
CREATE INDEX IF NOT EXISTS idx_ocr      ON polling_tables(ocr_status);
CREATE INDEX IF NOT EXISTS idx_val      ON polling_tables(validation_status);
CREATE INDEX IF NOT EXISTS idx_dept     ON polling_tables(department_code);
CREATE INDEX IF NOT EXISTS idx_flagged  ON polling_tables(flagged);

CREATE TABLE IF NOT EXISTS votes (
    transmission_code TEXT,
    label   TEXT,          -- name/label of the row read
    value   INTEGER,       -- vote read by OCR
    raw     TEXT,          -- raw OCR text for auditing
    PRIMARY KEY (transmission_code, label),
    FOREIGN KEY (transmission_code) REFERENCES polling_tables(transmission_code)
);

CREATE TABLE IF NOT EXISTS validations (
    transmission_code TEXT,
    check_name TEXT,       -- e.g. 'total_sum', 'voter_bound'
    severity   TEXT,       -- 'info' | 'warn' | 'flag'
    expected   INTEGER,
    got        INTEGER,
    detail     TEXT,
    PRIMARY KEY (transmission_code, check_name),
    FOREIGN KEY (transmission_code) REFERENCES polling_tables(transmission_code)
);

CREATE TABLE IF NOT EXISTS reviews (
    transmission_code TEXT PRIMARY KEY,
    verdict   TEXT,        -- 'real' | 'ocr_error' | 'unclear'
    note      TEXT,        -- optional reviewer note
    reviewed_at TEXT,
    FOREIGN KEY (transmission_code) REFERENCES polling_tables(transmission_code)
);

-- Numbers a human confirmed while reading the tally sheet (the review station).
-- ocr_value = what OCR guessed (may be NULL); entered_value = what the reviewer
-- typed after looking at the PDF. The sum check runs on entered_value.
CREATE TABLE IF NOT EXISTS entries (
    transmission_code TEXT,
    field   TEXT,          -- candidate name | 'blank' | 'null' | 'unmarked' | 'total'
    ocr_value     INTEGER,
    entered_value INTEGER,
    PRIMARY KEY (transmission_code, field),
    FOREIGN KEY (transmission_code) REFERENCES polling_tables(transmission_code)
);
"""


@contextmanager
def connect():
    os.makedirs(config.DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")       # read/write concurrency
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def counts_by(conn: sqlite3.Connection, column: str) -> dict[str, int]:
    rows = conn.execute(
        f"SELECT {column} AS k, COUNT(*) AS n FROM polling_tables GROUP BY {column}"
    ).fetchall()
    return {r["k"]: r["n"] for r in rows}


if __name__ == "__main__":
    init_db()
    with connect() as conn:
        total = conn.execute("SELECT COUNT(*) FROM polling_tables").fetchone()[0]
    print(f"[OK] Database ready at {DB_PATH}  (polling tables: {total:,})")
