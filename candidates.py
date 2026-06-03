# -*- coding: utf-8 -*-
"""Master candidate list for the presidential ballot (first round, May 2026).

Fixed and identical for every polling table: the ballot is the same nationwide.
Consolidated from repeated local vision reads of real tally sheets, taking the
most frequent/cleanest spelling of each name (OCR/vision misreads discarded).

The vision pre-fill matches its per-table reads to this list by fuzzy name match
and fills the votes; the reviewer confirms each number against the PDF.
"""

MASTER_CANDIDATES = [
    "IVÁN CEPEDA CASTRO",
    "CLAUDIA LÓPEZ",
    "RAÚL SANTIAGO BOTERO JARAMILLO",
    "ABELARDO DE LA ESPRIELLA",
    "ÓSCAR MAURICIO LIZCANO ARANGO",
    "MIGUEL URIBE LONDOÑO",
    "SONDRA MACOLLINS GARVIN PINTO",
    "ROY LEONARDO BARRERAS MONTEALEGRE",
    "CARLOS EDUARDO CAICEDO",
    "GUSTAVO MATAMOROS CAMACHO",
    "PALOMA VALENCIA LASERNA",
    "SERGIO FAJARDO VALDERRAMA",
    "LUIS GILBERTO MURILLO URRUTIA",
]

# Stable field key per candidate: candidate_1 .. candidate_13 (order = ballot).
CANDIDATE_FIELDS = [f"candidate_{i}" for i in range(1, len(MASTER_CANDIDATES) + 1)]


def field_for_index(i: int) -> str:
    return f"candidate_{i + 1}"


def _norm(s: str) -> str:
    """Loose normalization for fuzzy matching vision names to the master list."""
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return "".join(ch for ch in s.lower() if ch.isalnum() or ch == " ").strip()


def match_index(name: str) -> int | None:
    """Best master-list index for a (possibly misspelled) vision name, or None.

    Matches on shared word tokens; tolerant of OCR errors in a single token.
    """
    if not name:
        return None
    target = set(_norm(name).split())
    if not target:
        return None
    best, best_score = None, 0.0
    for idx, master in enumerate(MASTER_CANDIDATES):
        mtok = set(_norm(master).split())
        if not mtok:
            continue
        overlap = len(target & mtok)
        score = overlap / max(len(mtok), 1)
        if score > best_score:
            best, best_score = idx, score
    # Require at least one full shared surname-ish token.
    return best if best_score >= 0.34 else None
