"""Load the parsed PRE/ESC analysis into Supabase (one-time admin task).

Reads data/official/official_by_mesa.csv (produced by parse_official.py) and
bulk-upserts it into the public.official_data table via the Supabase REST API.

This needs the SERVICE ROLE key (admin), which must NEVER be committed or shipped
in the app. Pass it as an environment variable and run locally:

    SUPABASE_URL=https://<project>.supabase.co \
    SUPABASE_SERVICE_KEY=<service_role key from the Supabase dashboard> \
    python load_official.py

Standard library only.
"""
import csv
import gzip
import json
import os
import urllib.request


def _load_dotenv(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()
URL = os.environ.get("SUPABASE_URL")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY")
SRC = os.path.join("data", "official", "official_by_mesa.csv")
SRC_GZ = SRC + ".gz"
BATCH = 500


def _open_src():
    """Prefer the committed .gz; fall back to the plain CSV if present."""
    if os.path.exists(SRC_GZ):
        return gzip.open(SRC_GZ, "rt", encoding="utf-8")
    if os.path.exists(SRC):
        return open(SRC, encoding="utf-8")
    raise SystemExit(f"Missing {SRC_GZ} (run parse_official.py first).")


def rows_from_csv():
    with _open_src() as fh:
        for r in csv.DictReader(fh):
            yield {
                "mesa_code": r["mesa_code"],
                "department_code": r["department_code"] or None,
                "municipality_code": r["municipality_code"] or None,
                "zone_code": r["zone_code"] or None,
                "station_code": r["station_code"] or None,
                "table_number": r["table_number"] or None,
                "votos_pre": _int(r["votos_pre"]),
                "votos_esc": _int(r["votos_esc"]),
                "dif_neta": _int(r["dif_neta"]),
                "estado_sobre": r["estado_sobre"] or None,
                "recontada_jurados": r["recontada_jurados"] or None,
                "tachaduras": r["tachaduras"] or None,
                "excluida": r["excluida"] or None,
            }


def _int(v):
    v = (v or "").strip()
    try:
        return int(v)
    except ValueError:
        return None


def post_batch(batch):
    body = json.dumps(batch).encode("utf-8")
    req = urllib.request.Request(
        f"{URL}/rest/v1/official_data?on_conflict=mesa_code",
        data=body, method="POST",
        headers={
            "apikey": SERVICE_KEY,
            "Authorization": f"Bearer {SERVICE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "resolution=merge-duplicates,return=minimal",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return r.status


def main():
    if not (URL and SERVICE_KEY):
        raise SystemExit("Set SUPABASE_URL and SUPABASE_SERVICE_KEY (service role).")
    if not os.path.exists(SRC):
        raise SystemExit(f"Missing {SRC}. Run: python parse_official.py")
    batch, total = [], 0
    for row in rows_from_csv():
        batch.append(row)
        if len(batch) >= BATCH:
            post_batch(batch); total += len(batch)
            print(f"  loaded {total:,}", flush=True)
            batch = []
    if batch:
        post_batch(batch); total += len(batch)
    print(f"[OK] loaded {total:,} rows into official_data")


if __name__ == "__main__":
    main()
