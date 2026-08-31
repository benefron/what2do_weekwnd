"""Claude enrichment: classify each activity (category, feature tags, age fit,
language + French flag, price, English blurb, special-event flag).

Bulk pass on Haiku, batched ~25/call. A skip-unchanged cache keyed by
id + hash(title+description+date) keeps steady-state runs to a handful of
calls. Records Haiku flags low-confidence, or where a cheap rule check
disagrees, get a second pass on Sonnet.
"""
import hashlib
import json
import logging

import config
import llm_runner

log = logging.getLogger(__name__)

_ENRICH_SCHEMA = json.loads((config.PROMPTS_DIR / "enrich_schema.json").read_text())
_VERIFY_SCHEMA = json.loads((config.PROMPTS_DIR / "verify_schema.json").read_text())

_LLM_FIELDS = (
    "category", "feature_tags", "age_min", "age_max", "fits_4yo", "fits_8yo",
    "primary_language", "french_required", "language_note", "price_type",
    "price_min_eur", "price_max_eur", "blurb_en", "is_special_event",
    "is_recurring_class", "confidence",
)

_INPUT_FIELDS = (
    "id", "title_nl", "description_nl", "venue_name", "city", "date_start",
    "date_kind", "price_type", "price_min_eur", "price_max_eur", "price_note_nl",
    "age_min", "age_max", "raw_language",
)


def _content_hash(act: dict) -> str:
    basis = "|".join(str(act.get(k) or "") for k in ("title_nl", "description_nl", "date_start"))
    return hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def _load_cache() -> dict:
    if config.ENRICHMENT_CACHE_JSON.exists():
        try:
            return json.loads(config.ENRICHMENT_CACHE_JSON.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    config.ENRICHMENT_CACHE_JSON.write_text(json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True))


def _default_fields(act: dict) -> dict:
    return {
        "category": "other",
        "feature_tags": [],
        "age_min": act.get("age_min") if act.get("age_min") is not None else 0,
        "age_max": act.get("age_max") if act.get("age_max") is not None else 12,
        "fits_4yo": True,
        "fits_8yo": True,
        "primary_language": act.get("raw_language") or "nl",
        "french_required": False,
        "language_note": None,
        "price_type": act.get("price_type", "unknown"),
        "price_min_eur": act.get("price_min_eur"),
        "price_max_eur": act.get("price_max_eur"),
        "blurb_en": (act.get("title_nl") or "")[:160],
        "is_special_event": act.get("date_kind") != "permanent",
        "is_recurring_class": False,
        "confidence": "low",
    }


def _rule_conflict(act: dict, fields: dict) -> bool:
    text = f"{act.get('title_nl','')} {act.get('description_nl','')} {act.get('price_note_nl','')}".lower()
    if fields.get("price_type") == "free" and ("€" in text or "eur" in text or "betalen" in text):
        return True
    if fields.get("category") not in config.CATEGORY_VOCAB:
        return True
    if any(t not in config.FEATURE_TAG_VOCAB for t in fields.get("feature_tags", [])):
        return True
    return False


def _run_batches(instructions_path, schema, model, budget, copilot_model, effort, payloads, tag):
    """payloads: list of dicts to classify. Returns {id: fields}."""
    results: dict[str, dict] = {}
    instructions = instructions_path.read_text()
    config.STATE_DIR.mkdir(parents=True, exist_ok=True)
    for i in range(0, len(payloads), config.ENRICH_BATCH_SIZE):
        chunk = payloads[i : i + config.ENRICH_BATCH_SIZE]
        scratch = config.STATE_DIR / f"{tag}_batch_{i // config.ENRICH_BATCH_SIZE}.json"
        scratch.write_text(json.dumps({"activities": chunk}, ensure_ascii=False, indent=2))
        try:
            structured = llm_runner.run_with_schema(
                instructions=instructions,
                input_path=scratch,
                schema=schema,
                claude_model=model,
                claude_max_budget=budget,
                copilot_fallback_model=copilot_model,
                claude_effort=effort,
            )
            for entry in structured.get("activities", []):
                if entry.get("id"):
                    results[entry["id"]] = entry
        except Exception as exc:  # noqa: BLE001 - degrade this batch, keep going
            log.warning("%s batch %d failed: %s", tag, i // config.ENRICH_BATCH_SIZE, exc)
    return results


def enrich_all(activities: list[dict]) -> dict:
    """Mutates activities in place, adding the LLM fields. Returns run stats."""
    cache = _load_cache()
    to_classify: list[dict] = []
    by_id = {a["id"]: a for a in activities}

    for act in activities:
        h = _content_hash(act)
        cached = cache.get(act["id"])
        if cached and cached.get("hash") == h and all(f in cached for f in ("category", "blurb_en")):
            for f in _LLM_FIELDS:
                act[f] = cached.get(f, _default_fields(act)[f])
            act["enrichment_model"] = cached.get("model", "cache")
        elif all(k in act for k in ("category", "blurb_en")):
            # manual override already carried classification through
            act.setdefault("confidence", "high")
            act["enrichment_model"] = "manual"
        else:
            to_classify.append({k: act.get(k) for k in _INPUT_FIELDS})

    stats = {"classified": len(to_classify), "verified": 0, "batches": 0}

    if to_classify:
        stats["batches"] = (len(to_classify) + config.ENRICH_BATCH_SIZE - 1) // config.ENRICH_BATCH_SIZE
        fields_by_id = _run_batches(
            config.PROMPTS_DIR / "enrich_instructions.txt", _ENRICH_SCHEMA,
            config.ENRICH_MODEL, config.ENRICH_MAX_BUDGET_USD,
            config.COPILOT_FALLBACK_ENRICH_MODEL, None, to_classify, "enrich",
        )

        # apply + collect records needing a Sonnet second pass
        needs_verify: list[dict] = []
        for act in activities:
            f = fields_by_id.get(act["id"])
            if f is None:
                if "category" not in act:
                    act.update(_default_fields(act))
                    act["enrichment_model"] = "degraded"
                continue
            for k in _LLM_FIELDS:
                act[k] = f.get(k, _default_fields(act).get(k))
            act["enrichment_model"] = config.ENRICH_MODEL
            if act.get("confidence") == "low" or _rule_conflict(act, f):
                merged = {**{k: act.get(k) for k in _INPUT_FIELDS}, **{k: act.get(k) for k in _LLM_FIELDS}}
                needs_verify.append(merged)

        if needs_verify:
            stats["verified"] = len(needs_verify)
            verified_by_id = _run_batches(
                config.PROMPTS_DIR / "verify_instructions.txt", _VERIFY_SCHEMA,
                config.VERIFY_MODEL, config.VERIFY_MAX_BUDGET_USD,
                config.COPILOT_FALLBACK_VERIFY_MODEL, config.VERIFY_EFFORT, needs_verify, "verify",
            )
            for act in activities:
                v = verified_by_id.get(act["id"])
                if v:
                    for k in _LLM_FIELDS:
                        act[k] = v.get(k, act.get(k))
                    act["enrichment_model"] = f"{config.ENRICH_MODEL}+verify"

        # write cache for everything we just classified
        for act in activities:
            if act.get("enrichment_model", "").startswith(config.ENRICH_MODEL):
                cache[act["id"]] = {
                    "hash": _content_hash(act),
                    "model": act["enrichment_model"],
                    **{k: act.get(k) for k in _LLM_FIELDS},
                }
        _save_cache(cache)

    # final safety net
    for act in activities:
        if "category" not in act:
            act.update(_default_fields(act))
            act["enrichment_model"] = "degraded"

    log.info("enrich: %s", stats)
    return stats
