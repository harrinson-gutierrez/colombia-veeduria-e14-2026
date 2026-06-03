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


# Tokens that are too short/common to identify a candidate on their own.
_STOPWORDS = {"de", "la", "el", "los", "del", "y", "san"}


def _tokens(name: str) -> set[str]:
    return {t for t in _norm(name).split() if len(t) >= 3 and t not in _STOPWORDS}


# token -> set of master indexes that contain it. Built once.
_TOKEN_OWNERS: dict[str, set[int]] = {}
for _i, _m in enumerate(MASTER_CANDIDATES):
    for _t in _tokens(_m):
        _TOKEN_OWNERS.setdefault(_t, set()).add(_i)


def match_index(name: str) -> int | None:
    """Best master-list index for a (possibly misspelled/truncated) vision name.

    Strategy:
    1. If any read token is DISTINCTIVE (belongs to exactly one candidate), trust
       it — this maps a truncated name like "OSCAR" to Óscar Lizcano, which the
       old proportional score rejected (1 shared word / 4 = 0.25 < threshold).
    2. Otherwise fall back to the proportional overlap score.
    """
    if not name:
        return None
    target = _tokens(name)
    if not target:
        return None

    # 1. Distinctive single-owner token -> confident match.
    owners: dict[int, int] = {}
    for t in target:
        own = _TOKEN_OWNERS.get(t)
        if own and len(own) == 1:
            idx = next(iter(own))
            owners[idx] = owners.get(idx, 0) + 1
    if owners:
        # The candidate hit by the most distinctive tokens wins.
        return max(owners, key=owners.get)

    # 2. Fallback: proportional overlap against each master name.
    best, best_score = None, 0.0
    for idx, master in enumerate(MASTER_CANDIDATES):
        mtok = _tokens(master)
        if not mtok:
            continue
        score = len(target & mtok) / max(len(mtok), 1)
        if score > best_score:
            best, best_score = idx, score
    return best if best_score >= 0.34 else None
