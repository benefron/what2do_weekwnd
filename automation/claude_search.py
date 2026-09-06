"""Claude web-search pass for the big-name events the local agendas miss:
internationally known music concerts, touring musicals, and large-scale family
shows anywhere in Belgium, in the coming ~3 months.

Returns records shaped like a partial Activity with classification fields
already filled — they bypass the enrichment step (like manual overrides).
"""
import json
import logging
from datetime import datetime, timedelta, timezone

import config
import llm_runner

log = logging.getLogger(__name__)

_SCHEMA = {
    "type": "object",
    "required": ["events"],
    "properties": {
        "events": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "title", "description_en", "venue_name", "city", "date_start",
                    "url", "category", "audience", "family_relevant",
                ],
                "properties": {
                    "title": {"type": "string"},
                    "description_en": {"type": "string", "maxLength": 400},
                    "venue_name": {"type": "string"},
                    "city": {"type": "string"},
                    "date_start": {"type": "string", "description": "ISO date or datetime"},
                    "date_end": {"type": ["string", "null"]},
                    "url": {"type": "string"},
                    "category": {
                        "type": "string",
                        "enum": [
                            "music_concert", "theatre_puppetry", "festival",
                            "kermis_carnaval", "parade_seasonal", "guided_tour",
                            "film", "other",
                        ],
                    },
                    "audience": {
                        "type": "string",
                        "enum": ["kids", "family", "teens_adults", "adults"],
                    },
                    "family_relevant": {"type": "boolean"},
                    "price_min_eur": {"type": ["number", "null"]},
                    "price_max_eur": {"type": ["number", "null"]},
                    "primary_language": {"type": "string", "enum": ["nl", "fr", "en", "multi"]},
                    "french_required": {"type": "boolean"},
                    "language_free": {"type": "boolean"},
                    "notes_en": {"type": ["string", "null"]},
                },
            },
        }
    },
}


def _prompt() -> str:
    end = (datetime.now(timezone.utc) + timedelta(weeks=config.WINDOW_WEEKS)).date().isoformat()
    today = datetime.now(timezone.utc).date().isoformat()
    return f"""Use web search to find notable DATED events happening ANYWHERE IN
BELGIUM — all provinces, Flanders AND Wallonia AND Brussels — between {today} and
{end}. These fill the gaps the Flemish city agendas miss. Cover these buckets:

1. Knight / medieval / historical festivals, castle and chateau events, Roman
   and Viking days, jousting and re-enactment weekends.
2. Events, themed weekends and seasonal happenings at provincial and public
   recreation domains (Chevetogne, Bokrijk, Huizingen, Kessel-Lo, Puyenbroeck,
   De Nekker, Bois des Reves, etc.) and at zoos / animal parks.
3. Concerts by internationally known music artists / bands (Sportpaleis, Lotto
   Arena, ING Arena / Forest National, AB, Vorst Nationaal, etc.).
4. Touring musicals and large-scale theatre (note the language).
5. Big festivals or city events with a strong children's programme, incl. in
   Ghent, Antwerp, Brussels, Liege, Namur, Bruges.
6. Major touring children's / family shows.
7. Seasonal: Halloween and Christmas events at parks and domains, winter markets
   with a kids offer, light festivals.

For each event set:
- audience: "kids" (young children), "family" (works for the whole family),
  "teens_adults" (older kids and up), or "adults" (an adult night out).
- family_relevant: true if this is either (a) genuinely doable with young
  children, or (b) a big-name concert/musical worth a trip even though it is an
  adults' outing. false only for things with no broad appeal.
- Prefer the official venue or ticketing URL.
- Give real, verifiable dates. Do not invent events. If you are unsure an event
  is real or the date is right, omit it.

Return 15-40 events, matching the JSON schema exactly."""


def fetch_events() -> list[dict]:
    try:
        structured = llm_runner.run_search_with_schema(
            _prompt(),
            _SCHEMA,
            config.CLAUDE_SEARCH_MODEL,
            config.CLAUDE_SEARCH_MAX_BUDGET_USD,
            config.CLAUDE_SEARCH_EFFORT,
        )
    except Exception as exc:  # noqa: BLE001
        log.warning("claude_search: %s", exc)
        return []
    events = structured.get("events", [])
    log.info("claude_search: %d events", len(events))
    return events
