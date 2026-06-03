"""Loads allTransmissionCodes.json -> SQLite 'polling_tables' table (idempotent).

Reuses config.pdf_url() and discover.local_path()/load_department_names() for the
local path and names. Re-running does not duplicate rows or overwrite the status of
stages already advanced (INSERT OR IGNORE): it only inserts the new polling tables.

    python import_index.py
"""
import json

import config
import db
import discover

TRANSMISSION = f"{config.META_DIR}/allTransmissionCodes.json"


def load_nodes_with_group() -> list[tuple[str, dict]]:
    """Returns (status_group, node), preserving which group each node came from."""
    with open(TRANSMISSION, encoding="utf-8") as fh:
        data = json.load(fh)["data"]
    out: list[tuple[str, dict]] = []
    for group_key, group in data.items():
        if isinstance(group, dict) and group.get("nodes"):
            for node in group["nodes"]:
                out.append((group_key, node))
    return out


def main() -> None:
    db.init_db()
    dept = discover.load_department_names()
    pairs = load_nodes_with_group()

    rows = []
    for group_key, n in pairs:
        rows.append((
            n["idTransmissionCode"],
            group_key,
            n.get("idTransmissionCodeStatus", ""),
            n["idDepartmentCode"],
            dept.get(n["idDepartmentCode"], ""),
            n["municipalityCode"],
            n["idZoneCode"],
            n["standCode"],
            "",  # station_name: the tree is not loaded here; optional in the future
            n["numberStand"],
            n["expectedName"],
            config.pdf_url(n),
            discover.local_path(n),
        ))

    with db.connect() as conn:
        before = conn.execute("SELECT COUNT(*) FROM polling_tables").fetchone()[0]
        conn.executemany(
            """
            INSERT OR IGNORE INTO polling_tables (
                transmission_code, status_group, status,
                department_code, department_name, municipality_code,
                zone_code, station_code, station_name, table_number,
                expected_name, pdf_url, local_path
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            rows,
        )
        after = conn.execute("SELECT COUNT(*) FROM polling_tables").fetchone()[0]

    print(f"[OK] Imported {after - before:,} new polling tables (total: {after:,})")


if __name__ == "__main__":
    main()
