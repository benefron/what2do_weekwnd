"""UiTdatabank Search API v3 client — DISABLED in v1.

Most Leuven/Flanders agendas (leuven.be, the library, tourism, most museums)
are front-ends over one database, UiTdatabank, run by publiq vzw. It exposes a
clean JSON-LD events API, but *consuming* it needs a publiq platform plan
(~€125/yr Basic; free tiers exist for non-commercial / UiTnetwerk use — email
vragen@publiq.be).

To enable:
  1. Register a client at https://platform.publiq.be (test creds are immediate).
  2. Add to automation/secrets.local.json:
       "publiq_client_id":     "...",
       "publiq_client_secret": "..."
  3. config.UITDATABANK_ENABLED flips true automatically; sources.fetch_all()
     will call fetch_events() and treat it as the primary source.

Docs: https://docs.publiq.be/docs/uitdatabank/search-api/introduction
"""
import logging
from datetime import datetime, timedelta, timezone

import httpx

import config

log = logging.getLogger(__name__)

_TOKEN_URL = "https://account.uitid.be/realms/uitid/protocol/openid-connect/token"
_SEARCH_URL = "https://search.uitdatabank.be/events/"
_PAGE_SIZE = 50


def _access_token() -> str:
    resp = httpx.post(
        _TOKEN_URL,
        data={
            "grant_type": "client_credentials",
            "client_id": config.UITDATABANK_CLIENT_ID,
            "client_secret": config.UITDATABANK_CLIENT_SECRET,
            "audience": "https://api.publiq.be",
        },
        timeout=config.REQUEST_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def fetch_events() -> list[dict]:
    """Returns a list of JSON-LD Event dicts (embed=true) for the coming window,
    within MAX_DISTANCE_KM of Leuven, audienceType=everyone.
    """
    token = _access_token()
    now = datetime.now(timezone.utc)
    end = now + timedelta(weeks=config.WINDOW_WEEKS)
    lat, lng = config.LEUVEN_CENTER
    params = {
        "embed": "true",
        "audienceType": "everyone",
        "coordinates": f"{lat},{lng}",
        "distance": f"{config.MAX_DISTANCE_KM}km",
        "dateRange[start]": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "dateRange[end]": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "q": "(kinderen OR gezin OR familie OR jeugd OR kleuters OR kids)",
        "limit": _PAGE_SIZE,
    }
    headers = {"Authorization": f"Bearer {token}"}

    events: list[dict] = []
    start = 0
    while True:
        params["start"] = start
        resp = httpx.get(_SEARCH_URL, params=params, headers=headers, timeout=config.REQUEST_TIMEOUT_SECONDS)
        resp.raise_for_status()
        data = resp.json()
        members = data.get("member") or data.get("hydra:member") or []
        events.extend(members)
        total = data.get("totalItems") or data.get("hydra:totalItems") or 0
        start += _PAGE_SIZE
        if start >= total or not members:
            break
    log.info("uitdatabank: fetched %d events", len(events))
    return events
