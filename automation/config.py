"""Central configuration for the what2do_weekwnd pipeline.

Mirrors the israel-news-digest layout: all tuning constants, source lists,
model IDs and paths live here so the stage modules stay logic-only.
"""
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOMATION_DIR = Path(__file__).resolve().parent
SECRETS_LOCAL_JSON = AUTOMATION_DIR / "secrets.local.json"

DATA_DIR = REPO_ROOT / "data"
ARCHIVE_DIR = DATA_DIR / "archive"
STATE_DIR = AUTOMATION_DIR / "state"
LOGS_DIR = AUTOMATION_DIR / "logs"
PROMPTS_DIR = AUTOMATION_DIR / "prompts"
CACHE_DIR = AUTOMATION_DIR / "cache"

LATEST_JSON = DATA_DIR / "latest.json"
MANUAL_OVERRIDES_JSON = DATA_DIR / "manual_overrides.json"
ENRICHMENT_CACHE_JSON = DATA_DIR / "enrichment_cache.json"
GEOCODE_CACHE_JSON = CACHE_DIR / "geocode.json"

FRONTEND_DATA_DIR = REPO_ROOT / "frontend" / "public" / "data"

LAST_RUN_STATE = STATE_DIR / "last_run.json"
RUN_LOCK = STATE_DIR / "run.lock"

# ── secrets (gitignored) ────────────────────────────────────────────────────
def _load_secrets() -> dict:
    if SECRETS_LOCAL_JSON.exists():
        return json.loads(SECRETS_LOCAL_JSON.read_text())
    return {}


_secrets = _load_secrets()

# ── geography ───────────────────────────────────────────────────────────────
# Grote Markt, Leuven. Every activity's distance_km is haversine from here.
LEUVEN_CENTER = (50.8798, 4.7005)
# Widest ring we ever surface; the frontend slider defaults to 50 km.
MAX_DISTANCE_KM = 200

# ── time window ─────────────────────────────────────────────────────────────
# How far ahead to pull events each run. The dashboard is weekend-focused but
# a ~3 month horizon lets people plan school-holiday trips and lets the
# enrichment cache amortise across weeks.
WINDOW_WEEKS = 13

# ── run guards ──────────────────────────────────────────────────────────────
# Weekly schedule (Monday 07:30). 20h keeps a wake-catchup fire on Tuesday
# from re-running, while still allowing a manual --force.
MIN_HOURS_BETWEEN_RUNS = 20
LOCK_STALE_SECONDS = 2 * 60 * 60

# ── LLM (Claude CLI, --safe-mode; Copilot API fallback) ─────────────────────
ENRICH_MODEL = "claude-haiku-4-5-20251001"
ENRICH_MAX_BUDGET_USD = "0.50"
ENRICH_BATCH_SIZE = 25

# Sonnet re-checks only the records Haiku flagged low-confidence or where a
# rule check disagreed (e.g. "€" in the text but price_type == free).
VERIFY_MODEL = "claude-sonnet-5"
VERIFY_MAX_BUDGET_USD = "0.30"
VERIFY_EFFORT = "low"

COPILOT_API_BASE = "https://api.githubcopilot.com"
COPILOT_INTEGRATION_ID = "vscode-chat"
COPILOT_FALLBACK_ENRICH_MODEL = "claude-sonnet-5"
COPILOT_FALLBACK_VERIFY_MODEL = "claude-sonnet-5"

# ── controlled vocabularies (kept in sync with prompts/enrich_schema.json
#    and frontend/src/types.ts) ──────────────────────────────────────────────
CATEGORY_VOCAB = [
    "festival",
    "kermis_carnaval",
    "theatre_puppetry",
    "music_concert",
    "museum_exhibition",
    "museum_workshop",
    "library_workshop",
    "nature_farm",
    "zoo_animal_park",
    "sports_active",
    "market_food",
    "parade_seasonal",
    "film",
    "storytelling",
    "playground_indoor",
    "guided_tour",
    "holiday_camp",
    "other",
]

FEATURE_TAG_VOCAB = [
    "face_painting",
    "archery",
    "bouncy_castle",
    "puppet_show",
    "kermis",
    "carnaval",
    "food",
    "stories",
    "costumes",
    "animals",
    "crafts",
    "music",
    "science",
    "water_play",
    "dance",
    "magic",
    "treasure_hunt",
    "fireworks",
    "parade",
    "train_ride",
    "pumpkin_picking",
    "christmas_market",
    "easter",
    "halloween",
]

# ── Flemish school holidays (Vlaamse Gemeenschap), inclusive date ranges.
#    Source: onderwijs.vlaanderen.be / publicholidays.be. Update yearly. ─────
SCHOOL_HOLIDAYS = [
    {"name": "zomervakantie", "start": "2026-07-01", "end": "2026-08-31"},
    {"name": "herfstvakantie", "start": "2026-10-26", "end": "2026-11-01"},
    {"name": "kerstvakantie", "start": "2026-12-21", "end": "2027-01-04"},
    {"name": "krokusvakantie", "start": "2027-02-15", "end": "2027-02-21"},
    {"name": "paasvakantie", "start": "2027-03-29", "end": "2027-04-11"},
    {"name": "zomervakantie", "start": "2027-07-01", "end": "2027-08-31"},
]

# ── HTTP ────────────────────────────────────────────────────────────────────
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "nl-BE,nl;q=0.9,en;q=0.6",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}
REQUEST_TIMEOUT_SECONDS = 20

# Nominatim: 1 req/s max, descriptive UA required by their usage policy.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_USER_AGENT = "what2do-weekwnd/1.0 (benefron@gmail.com)"
NOMINATIM_MIN_INTERVAL_SECONDS = 1.1

# ── sources ─────────────────────────────────────────────────────────────────
# v1 is free-only: UiTinVlaanderen agenda RSS (same DB behind leuven.be,
# the library, tourism and most museums) + a handful of venue scrapers that
# parse <script type="application/ld+json"> Event blocks. A UiTdatabank
# Search API client (uitdatabank_client.py) can drop in later as the primary
# source — flip UITDATABANK_ENABLED and add creds to secrets.local.json.
UITDATABANK_ENABLED = bool(_secrets.get("publiq_client_id"))
UITDATABANK_CLIENT_ID = _secrets.get("publiq_client_id")
UITDATABANK_CLIENT_SECRET = _secrets.get("publiq_client_secret")

# RSS feeds — VERIFY LIVE with scripts/verify_sources.sh before trusting these
# unattended (URL shapes drift; comment mirrors the news-digest discipline).
# The UiTinVlaanderen agenda exposes an RSS alternate for any search; we scope
# it to Leuven and Vlaams-Brabant. If a URL 404s, open the agenda page in a
# browser, apply the region filter, and copy its "RSS" link here.
RSS_SOURCES = {
    "uitinvlaanderen_leuven": {
        "label": "UiT in Vlaanderen — Leuven",
        "rss": [
            "https://www.uitinvlaanderen.be/agenda/search/rss?keyword=&city=Leuven",
            "https://www.uitinvlaanderen.be/agenda/f/search.rss?q=Leuven",
        ],
        "scrape_fallback_url": "https://www.uitinleuven.be/agenda",
    },
    "uitinvlaanderen_vlaams_brabant": {
        "label": "UiT in Vlaanderen — Vlaams-Brabant",
        "rss": [
            "https://www.uitinvlaanderen.be/agenda/search/rss?keyword=kinderen&province=Vlaams-Brabant",
        ],
        "scrape_fallback_url": "https://www.uitinvlaanderen.be/agenda",
    },
}

# Venue scrapers — each function in sources.py handles one site. A dead
# scraper logs and is skipped; it never aborts the run.
SCRAPER_SOURCES = {
    "mleuven": {
        "label": "M Leuven",
        "url": "https://www.mleuven.be/nl/programma",
    },
    "leuven_kinderen": {
        "label": "Stad Leuven — agenda voor kinderen",
        "url": "https://www.leuven.be/agenda-voor-kinderen",
    },
    "visitleuven": {
        "label": "Visit Leuven",
        "url": "https://www.visitleuven.be/nl/agenda",
    },
    "toerisme_vlaams_brabant": {
        "label": "Toerisme Vlaams-Brabant — kidsagenda",
        "url": "https://www.toerismevlaamsbrabant.be/thema/kinderen/kidsagenda",
    },
    "technopolis": {
        "label": "Technopolis",
        "url": "https://www.technopolis.be/nl/",
    },
    "planckendael": {
        "label": "Planckendael",
        "url": "https://www.planckendael.be/nl/kalender",
    },
    "zoo_antwerpen": {
        "label": "ZOO Antwerpen",
        "url": "https://www.zooantwerpen.be/nl/kalender",
    },
    "natuurwetenschappen": {
        "label": "Museum voor Natuurwetenschappen",
        "url": "https://www.naturalsciences.be/nl/visit/agenda",
    },
    "bokrijk": {
        "label": "Bokrijk",
        "url": "https://www.bokrijk.be/nl/agenda",
    },
}
