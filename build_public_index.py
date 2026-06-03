"""Build a MINIMAL, compressed index for the public web app.

The public server needs only enough to render the browser and build each PDF
URL: department / municipality / zone / station / table + the PDF URL. It does
NOT need local paths or per-PDF storage (PDFs are proxied on demand).

Writes data/public_index.csv.gz (a few MB, safe to commit and deploy). Streams
straight to gzip so the uncompressed 39 MB file never touches disk.

    python build_public_index.py
"""
import csv
import gzip
import io
import json
import os

import config

OUT = os.path.join(config.DATA_DIR, "public_index.csv.gz")
HEADER = [
    "department_code", "department_name", "municipality_code",
    "zone_code", "station_code", "table_number", "pdf_url",
]


def load_nodes():
    path = f"{config.META_DIR}/allTransmissionCodes.json"
    if not os.path.exists(path):
        raise SystemExit(f"Missing {path}. Run: python inspect_meta.py")
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    for group in obj["data"].values():
        if isinstance(group, dict) and group.get("nodes"):
            yield from group["nodes"]


def dept_names():
    path = f"{config.META_DIR}/allDepartments.json"
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    return {d["idDepartmentCode"]: d["departmentName"]
            for d in obj["data"]["allDepartments"]["nodes"]}


def main():
    names = dept_names()
    os.makedirs(config.DATA_DIR, exist_ok=True)
    n = 0
    # status 11 = published; that is what the public can actually open.
    with gzip.open(OUT, "wt", encoding="utf-8", newline="") as gz:
        w = csv.writer(gz)
        w.writerow(HEADER)
        for node in load_nodes():
            if node.get("idTransmissionCodeStatus") != 11:
                continue
            w.writerow([
                node["idDepartmentCode"],
                names.get(node["idDepartmentCode"], ""),
                node["municipalityCode"],
                node["idZoneCode"],
                node["standCode"],
                node["numberStand"],
                config.pdf_url(node),
            ])
            n += 1
    size = os.path.getsize(OUT)
    print(f"[OK] {OUT}: {n:,} rows, {size/1e6:.1f} MB compressed")


if __name__ == "__main__":
    main()
