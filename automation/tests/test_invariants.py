"""Cross-file invariants that nothing else enforces.

CLAUDE.md warns that the controlled vocabularies are duplicated across config.py,
two JSON schemas and two TypeScript files, and that "changing any vocab means
editing every copy". Nothing checked that. These are the cheapest tests in the
suite and the ones most likely to catch a real mistake, because every failure
mode here is silent: an unlisted field is dropped by publish without comment, a
field missing from _default_fields raises KeyError only on a cache hit, and a
vocab that drifts between config and the schema makes Claude emit values the
frontend cannot label.
"""
import json
import re
from pathlib import Path

import pytest

import build_places
import config
import enrich
import places
import publish

REPO = Path(__file__).resolve().parents[2]
PROMPTS = REPO / "automation" / "prompts"
FRONTEND = REPO / "frontend" / "src"


def schema_items(name):
    data = json.loads((PROMPTS / name).read_text())
    return data["properties"]["activities"]["items"]


# ── enrich field plumbing ───────────────────────────────────────────────────
def test_every_llm_field_has_a_default():
    """A cache hit does cached.get(f, _default_fields(act)[f]); a field without
    a default raises KeyError the first time an old cache entry is reused."""
    defaults = enrich._default_fields({"date_kind": "single"})
    missing = [f for f in enrich._LLM_FIELDS if f not in defaults]
    assert not missing, f"_default_fields is missing: {missing}"


# Fields the LLM sets that are deliberately consumed server-side and never
# shipped. run_weekly drops every record with family_relevant false, so the
# survivors are all true and the field carries no information downstream.
_SERVER_SIDE_ONLY = {"family_relevant"}


def test_every_llm_field_is_published():
    """publish silently drops anything not in _PUBLISHED_FIELDS, so a new field
    can look wired up end to end and still never reach the frontend."""
    missing = [
        f for f in enrich._LLM_FIELDS
        if f not in publish._PUBLISHED_FIELDS and f not in _SERVER_SIDE_ONLY
    ]
    assert not missing, f"_PUBLISHED_FIELDS is missing: {missing}"


def test_server_side_only_fields_really_are_unpublished():
    """Guards the exception above: if one of these starts being published, the
    allowlist is stale and should shrink rather than silently over-permit."""
    for field in _SERVER_SIDE_ONLY:
        assert field not in publish._PUBLISHED_FIELDS, (
            f"{field} is published now — remove it from _SERVER_SIDE_ONLY"
        )


def test_every_llm_field_is_in_the_enrich_schema():
    props = schema_items("enrich_schema.json")["properties"]
    missing = [f for f in enrich._LLM_FIELDS if f not in props]
    assert not missing, f"enrich_schema.json is missing: {missing}"


def test_indoor_outdoor_is_wired_end_to_end():
    """The rainy-day field crosses five files; a gap in any one is silent."""
    assert "indoor_outdoor" in enrich._LLM_FIELDS
    assert "indoor_outdoor" in enrich._default_fields({"date_kind": "single"})
    assert "indoor_outdoor" in publish._PUBLISHED_FIELDS
    assert "indoor_outdoor" in schema_items("enrich_schema.json")["properties"]
    assert "indoor_outdoor" in schema_items("verify_schema.json")["properties"]


def test_school_holiday_flags_are_published():
    for field in ("school_holiday_nl", "school_holiday_fr", "in_school_holiday"):
        assert field in publish._PUBLISHED_FIELDS, field


def test_link_and_credit_fields_are_published():
    for field in ("link_ok", "image_credit", "image_credit_url"):
        assert field in publish._PUBLISHED_FIELDS, field


# ── the two JSON schemas ────────────────────────────────────────────────────
def test_verify_schema_matches_enrich_schema():
    """verify_schema.json is maintained as a copy of enrich_schema.json; drift
    means the Sonnet second pass validates against a different shape."""
    assert schema_items("enrich_schema.json") == schema_items("verify_schema.json")


def test_schema_required_fields_all_have_properties():
    for name in ("enrich_schema.json", "verify_schema.json"):
        items = schema_items(name)
        orphans = [f for f in items["required"] if f not in items["properties"]]
        assert not orphans, f"{name} requires undefined fields: {orphans}"


# ── controlled vocabularies ─────────────────────────────────────────────────
def test_category_vocab_matches_the_schema():
    schema_enum = schema_items("enrich_schema.json")["properties"]["category"]["enum"]
    assert sorted(schema_enum) == sorted(config.CATEGORY_VOCAB)


def test_feature_tag_vocab_matches_the_schema():
    props = schema_items("enrich_schema.json")["properties"]
    schema_enum = props["feature_tags"]["items"]["enum"]
    assert sorted(schema_enum) == sorted(config.FEATURE_TAG_VOCAB)


def test_vocabularies_have_no_duplicates():
    for name in ("CATEGORY_VOCAB", "FEATURE_TAG_VOCAB"):
        vocab = getattr(config, name)
        assert len(vocab) == len(set(vocab)), f"{name} has duplicates"


def ts_string_union(path: Path, type_name: str) -> set[str]:
    """Pull the members out of `export type X = "a" | "b" | ...;`."""
    text = path.read_text()
    m = re.search(rf"export type {type_name}\s*=\s*(.*?);", text, re.S)
    assert m, f"{type_name} not found in {path.name}"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def test_category_vocab_matches_the_frontend():
    assert ts_string_union(FRONTEND / "types.ts", "Category") == set(config.CATEGORY_VOCAB)


def test_feature_tag_vocab_matches_the_frontend():
    assert ts_string_union(FRONTEND / "types.ts", "FeatureTag") == set(config.FEATURE_TAG_VOCAB)


def test_every_category_has_a_frontend_label():
    labels = (FRONTEND / "lib" / "labels.ts").read_text()
    missing = [c for c in config.CATEGORY_VOCAB if f'{c}:' not in labels and f'"{c}"' not in labels]
    assert not missing, f"CATEGORY_LABELS is missing: {missing}"


def test_every_feature_tag_has_a_frontend_label():
    labels = (FRONTEND / "lib" / "labels.ts").read_text()
    missing = [t for t in config.FEATURE_TAG_VOCAB if f'{t}:' not in labels and f'"{t}"' not in labels]
    assert not missing, f"FEATURE_LABELS is missing: {missing}"


# ── place kinds ─────────────────────────────────────────────────────────────
def test_place_kind_enum_is_consistent_across_python():
    kinds = set(build_places.KINDS)
    mapped = set(places._KIND_TO_CATEGORY)
    assert kinds <= mapped, f"_KIND_TO_CATEGORY is missing: {sorted(kinds - mapped)}"


def test_place_kinds_match_the_frontend():
    ts_kinds = ts_string_union(FRONTEND / "types.ts", "PlaceKind")
    missing = set(build_places.KINDS) - ts_kinds
    assert not missing, f"PlaceKind is missing: {sorted(missing)}"


def test_every_place_kind_has_a_frontend_label():
    labels = (FRONTEND / "lib" / "labels.ts").read_text()
    missing = [k for k in build_places.KINDS if f'{k}:' not in labels and f'"{k}"' not in labels]
    assert not missing, f"PLACE_KIND_LABELS is missing: {missing}"


def test_kinds_used_in_places_json_are_all_known():
    data = json.loads((REPO / "data" / "places.json").read_text())
    used = {p.get("kind") for p in data["places"]}
    unknown = used - set(build_places.KINDS) - {"other", None}
    assert not unknown, f"places.json uses unknown kinds: {sorted(unknown)}"


def test_categories_mapped_from_kinds_are_in_the_vocab():
    unknown = set(places._KIND_TO_CATEGORY.values()) - set(config.CATEGORY_VOCAB)
    assert not unknown, f"_KIND_TO_CATEGORY emits non-vocab categories: {sorted(unknown)}"


def test_language_free_kinds_are_real_kinds():
    unknown = places._LANGUAGE_FREE_KINDS - set(build_places.KINDS)
    assert not unknown, f"_LANGUAGE_FREE_KINDS has unknown kinds: {sorted(unknown)}"


def test_both_kinds_are_real_kinds():
    unknown = places._BOTH_KINDS - set(build_places.KINDS)
    assert not unknown, f"_BOTH_KINDS has unknown kinds: {sorted(unknown)}"


# ── enrichment cache versioning ─────────────────────────────────────────────
def test_content_hash_changes_with_schema_version(monkeypatch):
    """SCHEMA_VERSION is folded into the hash; without a bump, a prompt or field
    change replays every cached record and quietly does nothing."""
    act = {"title_nl": "x", "description_nl": "y", "date_start": "2026-01-01"}
    before = enrich._content_hash(act)
    monkeypatch.setattr(enrich, "SCHEMA_VERSION", enrich.SCHEMA_VERSION + 1)
    assert enrich._content_hash(act) != before


def test_content_hash_changes_with_content():
    a = enrich._content_hash({"title_nl": "x", "description_nl": "y", "date_start": "2026-01-01"})
    b = enrich._content_hash({"title_nl": "z", "description_nl": "y", "date_start": "2026-01-01"})
    assert a != b


def test_content_hash_is_stable():
    act = {"title_nl": "x", "description_nl": "y", "date_start": "2026-01-01"}
    assert enrich._content_hash(act) == enrich._content_hash(dict(act))


# ── data file sanity ────────────────────────────────────────────────────────
def test_places_json_entries_have_required_fields():
    data = json.loads((REPO / "data" / "places.json").read_text())
    for p in data["places"]:
        assert p.get("id"), p
        assert p.get("name"), p
        assert p.get("kind"), p


def test_place_ids_are_unique():
    data = json.loads((REPO / "data" / "places.json").read_text())
    ids = [p["id"] for p in data["places"]]
    dupes = {i for i in ids if ids.count(i) > 1}
    assert not dupes, f"duplicate place ids: {sorted(dupes)}"


@pytest.mark.parametrize("field", ["lat", "lng"])
def test_places_have_coordinates(field):
    """An ungeocoded place has distance_km null and drops out of every
    distance-filtered search, so it is invisible rather than merely imprecise."""
    data = json.loads((REPO / "data" / "places.json").read_text())
    missing = [p["name"] for p in data["places"] if p.get(field) is None]
    assert not missing, f"{len(missing)} places without {field}: {missing[:5]}"
