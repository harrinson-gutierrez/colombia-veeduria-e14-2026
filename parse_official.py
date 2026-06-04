"""Parse the third-party PRE-vs-ESC analysis sheet into per-table records.

Source: a public Google Sheet by an external analyst comparing the official
preconteo (PRE) and escrutinio (ESC) vote counts per polling table, plus
integrity flags (envelope state, recount, erasures). NOT an official source --
shown in the app as "external analysis, to verify".

The sheet has one row PER CANDIDATE; we collapse to one record per table using
the table-level columns (which repeat across a table's candidate rows).

Output: data/official/official_by_mesa.csv with a mesa_code that matches the
index used by the app (dept-mun-zone-station-table).

    python parse_official.py
"""
import csv
import os

SRC = os.path.join("data", "official", "pre_esc_analysis.csv")
OUT = os.path.join("data", "official", "official_by_mesa.csv")


def col_index(header: list[str], name: str) -> int | None:
    for i, h in enumerate(header):
        if h.strip() == name:
            return i
    return None


def first_token(s: str) -> str:
    """Leading code: '16 BOGOTA D.C.' -> '16'; '001-BOGOTA. D.C.' -> '001'.
    The leading code is the run of leading digits, whatever separator follows."""
    s = (s or "").strip()
    digits = ""
    for ch in s:
        if ch.isdigit():
            digits += ch
        else:
            break
    return digits or s


def strip_zeros(s: str) -> str:
    """'000003' -> '3' (the app stores table_number without left padding... or
    with? -> we normalise both sides to the integer string)."""
    s = (s or "").strip()
    return str(int(s)) if s.isdigit() else s


def to_int(s):
    s = (s or "").strip().replace(".", "").replace(",", "")
    try:
        return int(s)
    except ValueError:
        return None


def main() -> None:
    if not os.path.exists(SRC):
        raise SystemExit(f"Missing {SRC}. Download the sheet as CSV there first.")
    with open(SRC, encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    header = rows[0]

    ci = {name: col_index(header, name) for name in (
        "DEPARTAMENTO", "MUNICIPIO", "ZZ", "PP", "MESA",
        "Votos Mesa PRE", "Votos Mesa ESC", "Dif. Mesa Neta",
        "Estado del sobre", "Recontada Jurados",
        "Tacahaduras y/o Enmendaduras", "Excluida")}

    by_mesa: dict[str, dict] = {}
    for r in rows[1:]:
        if len(r) <= max(c for c in ci.values() if c is not None):
            continue
        dept = first_token(r[ci["DEPARTAMENTO"]])
        mun = first_token(r[ci["MUNICIPIO"]])
        zone = (r[ci["ZZ"]] or "").strip()
        station = (r[ci["PP"]] or "").strip()
        table = strip_zeros(r[ci["MESA"]])
        if not (dept and zone and station and table):
            continue
        mesa_code = f"{dept}-{mun}-{zone}-{station}-{table}"
        if mesa_code in by_mesa:
            continue  # table-level data repeats; keep the first
        pre = to_int(r[ci["Votos Mesa PRE"]])
        esc = to_int(r[ci["Votos Mesa ESC"]])
        diff = to_int(r[ci["Dif. Mesa Neta"]])
        by_mesa[mesa_code] = {
            "mesa_code": mesa_code,
            "department_code": dept, "municipality_code": mun,
            "zone_code": zone, "station_code": station, "table_number": table,
            "votos_pre": pre, "votos_esc": esc, "dif_neta": diff,
            "estado_sobre": (r[ci["Estado del sobre"]] or "").strip(),
            "recontada_jurados": (r[ci["Recontada Jurados"]] or "").strip(),
            "tachaduras": (r[ci["Tacahaduras y/o Enmendaduras"]] or "").strip(),
            "excluida": (r[ci["Excluida"]] or "").strip(),
        }

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fields = ["mesa_code", "department_code", "municipality_code", "zone_code",
              "station_code", "table_number", "votos_pre", "votos_esc",
              "dif_neta", "estado_sobre", "recontada_jurados", "tachaduras",
              "excluida"]
    with open(OUT, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(by_mesa.values())

    n = len(by_mesa)
    with_diff = sum(1 for v in by_mesa.values() if v["dif_neta"] not in (None, 0))
    print(f"[OK] {OUT}: {n:,} tables")
    print(f"     with PRE/ESC net difference (priority): {with_diff:,}")


if __name__ == "__main__":
    main()
