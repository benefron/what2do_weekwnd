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
# Per-batch cap. A ~15-item batch with the full schema runs ~$0.10-0.40 via the
# subscription CLI; the CLI exits non-zero (→ Copilot fallback) if a call would
# exceed this, so keep some headroom.
ENRICH_MAX_BUDGET_USD = "1.50"
ENRICH_BATCH_SIZE = 15

# Sonnet re-checks only the records Haiku flagged low-confidence or where a
# rule check disagreed (e.g. "€" in the text but price_type == free).
VERIFY_MODEL = "claude-sonnet-5"
VERIFY_MAX_BUDGET_USD = "1.00"
VERIFY_EFFORT = "low"

# A Claude web-search pass that finds big-name touring acts the local agendas
# under-cover: internationally known music concerts, touring musicals, and
# large-scale family shows anywhere in Belgium. Adults-of-interest is fine here
# (a big concert you'd travel for), unlike the kid-only agenda scrape.
CLAUDE_SEARCH_ENABLED = True
CLAUDE_SEARCH_MODEL = "claude-sonnet-5"
CLAUDE_SEARCH_MAX_BUDGET_USD = "1.00"
CLAUDE_SEARCH_EFFORT = "medium"

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
# v1, free, no key: scrape the server-rendered UiT agenda listing pages for
# event UUIDs, then hydrate each via the PUBLIC UiTdatabank read endpoint
# https://io.uitdatabank.be/events/<uuid> (full JSON-LD: name, description,
# typicalAgeRange, priceInfo, calendar, terms, location.geo, languages).
# The same database is behind leuven.be, the library, tourism and most
# museums, so this one path covers the bulk of what we want.
#
# The optional UiTdatabank *Search* API (uitdatabank_client.py) needs a paid
# publiq key; flip it on by adding creds to secrets.local.json. It is a
# strict upgrade (server-side region/age/date filtering) but not required.
UITDATABANK_ENABLED = bool(_secrets.get("publiq_client_id"))
UITDATABANK_CLIENT_ID = _secrets.get("publiq_client_id")
UITDATABANK_CLIENT_SECRET = _secrets.get("publiq_client_secret")

UIT_READ_ENDPOINT = "https://io.uitdatabank.be/events/{uuid}"

# Agenda listing pages to scrape for event UUIDs. Events are date-sorted
# (soonest first), so the first pages cover the weekend window. Bump
# UIT_AGENDA_MAX_PAGES for a longer horizon at the cost of more hydration
# requests + a bigger enrichment bill on the first run.
UIT_AGENDA_MAX_PAGES = 25  # default; per-source override below
# Order matters: fetch_uit_agenda() skips uuids already seen from an earlier
# source, so the narrow region feeds come first and keep their own label rather
# than being swallowed by the all-Flanders scrape.
#
# Slug trap: provinces take a "provincie-" prefix, a bare name is the
# *municipality* (/agenda/alle/antwerpen is the city, not the province).
# Brussels-Capital takes no prefix. "voor-kinderen" is a path segment as well as
# a query param; the path form is used so list_agenda_uuids can append ?page=N.
UIT_AGENDA_SOURCES = {
    "uitinbrussel": {
        "label": "UiT in Brussel",
        "list_url": "https://www.uitinvlaanderen.be/agenda/alle/brussels-hoofdstedelijk-gewest/voor-kinderen",
        "max_pages": 20,
        "first_page": 1,
    },
    "uitinvlaamsbrabant": {
        "label": "UiT in Vlaams-Brabant",
        "list_url": "https://www.uitinvlaanderen.be/agenda/alle/provincie-vlaams-brabant/voor-kinderen",
        "max_pages": 20,
        "first_page": 1,
    },
    "uitinleuven": {
        "label": "UiT in Leuven",
        "list_url": "https://www.uitinleuven.be/agenda",
        "max_pages": 25,
        "first_page": 0,
    },
    # All-Flanders agenda — same platform, same /agenda/e/<slug>/<uuid> links,
    # same ?page=N pagination. Page 0 is JS-hydrated (few links) so start at 1.
    # Volume is high; the kid prefilter + the distance prefilter trim it.
    "uitinvlaanderen": {
        "label": "UiT in Vlaanderen",
        "list_url": "https://www.uitinvlaanderen.be/agenda",
        "max_pages": 40,
        "first_page": 1,
    },
}

# Kid-relevance prefilter (normalize.py) — an event survives to the Claude
# step only if its UiTdatabank typicalAgeRange lower bound is <= this, OR it
# carries a family/kids term/label, OR its text matches a kid keyword.
PREFILTER_MAX_AGE_MIN = 12
KID_KEYWORDS = [
    # Dutch
    "kinder", "kleuter", "peuter", "gezin", "familie", "jeugd", "baby",
    "kids", "kind ", "voor kinderen", "4+", "6+", "3+", "kindvriendelijk",
    "springkasteel", "poppentheater", "schmink", "knutsel", "verhaal",
    "sprookje", "workshop voor kinderen", "kinderboerderij", "speeltuin",
    # French. A French-language event with no typicalAgeRange is dropped by
    # _is_kid_relevant unless it matches here, so any francophone source needs
    # these. Specific phrases only: a bare "atelier" or "spectacle" would let
    # adult programming through.
    "enfant", "pour enfants", "jeune public", "en famille", "familial",
    "bébé", "bebe", "tout-petits", "dès 3 ans", "dès 4 ans", "dès 5 ans",
    "dès 6 ans", "à partir de 3", "à partir de 4", "à partir de 6",
    "plaine de jeux", "aire de jeux", "conte", "marionnette", "bricolage",
    "grimage", "château gonflable", "ferme pédagogique", "kermesse",
    # English
    "children", "for kids", "family friendly",
]

# Accept-Language header per source language (see sources._headers).
ACCEPT_LANGUAGE = {
    "nl": "nl-BE,nl;q=0.9,en;q=0.6",
    "fr": "fr-BE,fr;q=0.9,en;q=0.6",
    "en": "en;q=0.9,nl-BE;q=0.6,fr-BE;q=0.6",
}

# OpenDataSoft v2.1 portals — public, no auth, structured dated events. One
# fetcher serves all of them, so a new portal is a config entry.
#
# UNVERIFIED: dataset ids come from the portals' catalogue pages but the field
# names below could not be checked from the dev environment (every .be host is
# blocked there). `scripts/probe_source.sh --ods <key>` prints the real field
# names; a mismatch shows up as 0 records and the source is skipped, not fatal.
ODS_SOURCES = {
    "odwb_wallonie": {
        "label": "Événements en Wallonie",
        "base": "https://www.odwb.be",
        "dataset": "evenements-en-wallonie",
        "default_language": "fr",
        "max_records": 600,
    },
    "odwb_letsgocity": {
        "label": "Letsgocity Wallonie",
        "base": "https://www.odwb.be",
        "dataset": "letsgocity-evenements-des-communes-en-wallonie",
        "default_language": "fr",
        "max_records": 400,
    },
    # "Agenda du jour" — may only carry today's activities, which is useless for
    # a Monday run that needs the coming weekend. Off until the probe confirms a
    # forward horizon.
    "opendata_brussels": {
        "label": "Agenda Ville de Bruxelles",
        "base": "https://opendata.brussels.be",
        "dataset": "agenda",
        "default_language": "fr",
        "max_records": 200,
        "enabled": False,
    },
}

# RSS/Atom feeds. feedparser is already a dependency; classification is left
# entirely to enrich.py, so a feed only needs to yield a title, a link and a date.
FEED_SOURCES: dict[str, dict] = {}

# Direct venue scrapers — disabled in v1 (each Belgian venue site renders its
# calendar with client-side JS, so a plain fetch yields no JSON-LD). Add real
# scrapers here later, or rely on the fact that these venues publish to UiT.
SCRAPER_SOURCES: dict[str, dict] = {}
