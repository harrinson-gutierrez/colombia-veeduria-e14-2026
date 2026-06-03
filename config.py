"""Central configuration for vote-verify (tally-form audit pipeline)."""

import os

BASE = "https://divulgacione14presidente.registraduria.gov.co"
META_BASE = f"{BASE}/assets/temis/divipol_json"
PDF_BASE = f"{BASE}/assets/temis/pdf"

# E14 PDF URL pattern, confirmed empirically against live traffic:
#   /assets/temis/pdf/{dept}/{mun}/{zone:3}/{stationCode}/{tableNumber}/{acronym}/{expectedName}
# Key detail: the zone is left-padded to 3 digits; stationCode and tableNumber as-is.
ACRONYM = "PRE"  # PRESIDENT


def pdf_url(node: dict) -> str:
    zone3 = str(node["idZoneCode"]).zfill(3)
    return (
        f"{PDF_BASE}/{node['idDepartmentCode']}/{node['municipalityCode']}/"
        f"{zone3}/{node['standCode']}/{node['numberStand']}/{ACRONYM}/"
        f"{node['expectedName']}"
    )


# Static metadata JSON files served by the source site.
META_FILES = [
    "allDepartments",
    "allCorporations",
    "departmentsTree",
    "allTransmissionCodes",
    "CorpIndexAndMap",
    "allMviewGetProgressByDepartmentAndCorporations",
    "allMviewGetProgressByMunicipalityAndCorporations",
    "allMviewGetProgressByCorporations",
]

# Browser-like headers. No Authorization/apikey: only Akamai cookies.
HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "es-CO,es;q=0.9,en-US;q=0.8,en;q=0.7",
    "referer": f"{BASE}/home",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
    ),
}

# Politeness toward public infrastructure. "Moderate" profile: low and humanlike
# to avoid anti-bot blocking on a fixed IP over long runs.
MAX_CONCURRENCY = 2              # simultaneous downloads
REQUEST_DELAY_MIN = 0.8         # per-request pause, lower bound (seconds)
REQUEST_DELAY_MAX = 1.5         # per-request pause, upper bound (randomized)
REQUEST_DELAY_SECONDS = 0.8     # fallback fixed delay (kept for compatibility)
TIMEOUT_SECONDS = 60
MAX_RETRIES = 4

# Anti-block behavior. When the source returns 403/429 (rate limit / challenge),
# back off for an increasing wait, then resume (the pipeline is resumable). If
# blocking persists past BLOCK_MAX_BACKOFFS, stop and surface a 'blocked' state.
BLOCK_BACKOFF_BASE = 30         # seconds; doubles each consecutive block
BLOCK_BACKOFF_CAP = 600         # max single wait (10 min)
BLOCK_MAX_BACKOFFS = 5          # give up & signal blocked after this many in a row
COOLDOWN_EVERY = 50            # take a longer rest every N successful downloads
COOLDOWN_SECONDS = 15

DATA_DIR = "data"
META_DIR = f"{DATA_DIR}/meta"
PDF_DIR = f"{DATA_DIR}/forms"
INDEX_CSV = f"{DATA_DIR}/index.csv"

# OCR (stage 2). By default both tools are taken from the system PATH. If they
# are not on PATH, point to them with environment variables (no need to edit
# this file):
#   TESSERACT_CMD=/path/to/tesseract   POPPLER_PATH=/path/to/poppler/bin
# On Windows a typical value is:
#   TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
TESSERACT_CMD = os.environ.get("TESSERACT_CMD") or None
POPPLER_PATH = os.environ.get("POPPLER_PATH") or None
OCR_LANG = "spa"       # source forms are in Spanish; OCR language model
OCR_DPI = 300
# Directory holding <lang>.traineddata. Bundled in the project so no system-wide
# language install is needed. Leave None to use Tesseract's default tessdata.
TESSDATA_DIR = f"{DATA_DIR}/tessdata"
