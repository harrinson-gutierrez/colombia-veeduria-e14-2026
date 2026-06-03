"""Step 2: build the index of every polling table with its tally-form URL.

Reads data/meta/allTransmissionCodes.json (downloaded by inspect_meta.py) and
writes data/index.csv with one row per polling table, including the PDF URL and
the destination local path. Standard library only.

    python discover.py
"""
import csv
import json
import os

import config


def load_nodes() -> list[dict]:
    path = f"{config.META_DIR}/allTransmissionCodes.json"
    if not os.path.exists(path):
        raise SystemExit(
            f"Missing {path}. Run first: python inspect_meta.py"
        )
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    nodes = []
    for group in obj["data"].values():
        if isinstance(group, dict) and group.get("nodes"):
            nodes.extend(group["nodes"])
    return nodes


def load_department_names() -> dict[str, str]:
    path = f"{config.META_DIR}/allDepartments.json"
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        obj = json.load(fh)
    return {
        d["idDepartmentCode"]: d["departmentName"]
        for d in obj["data"]["allDepartments"]["nodes"]
    }


def local_path(node: dict) -> str:
    zone3 = str(node["idZoneCode"]).zfill(3)
    return os.path.join(
        config.PDF_DIR,
        node["idDepartmentCode"],
        node["municipalityCode"],
        zone3,
        node["standCode"],
        f"{node['numberStand']}_{node['expectedName']}",
    )


def main() -> None:
    nodes = load_nodes()
    dept_names = load_department_names()
    os.makedirs(config.DATA_DIR, exist_ok=True)

    with open(config.INDEX_CSV, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow([
            "transmission_code", "status", "department_code", "department_name",
            "municipality_code", "zone_code", "station_code", "table_number",
            "expected_name", "pdf_url", "local_path",
        ])
        for n in nodes:
            writer.writerow([
                n.get("idTransmissionCode", ""),
                n.get("idTransmissionCodeStatus", ""),
                n["idDepartmentCode"],
                dept_names.get(n["idDepartmentCode"], ""),
                n["municipalityCode"],
                n["idZoneCode"],
                n["standCode"],
                n["numberStand"],
                n["expectedName"],
                config.pdf_url(n),
                local_path(n),
            ])

    print(f"[OK] Index written: {config.INDEX_CSV}")
    print(f"     Total polling tables: {len(nodes):,}")
    by_status: dict = {}
    for n in nodes:
        s = n.get("idTransmissionCodeStatus")
        by_status[s] = by_status.get(s, 0) + 1
    print(f"     By status: {by_status}")


if __name__ == "__main__":
    main()
