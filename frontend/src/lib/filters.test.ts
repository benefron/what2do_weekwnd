import { describe, expect, it } from "vitest";
import {
  AGE_BUCKETS,
  DEFAULT_FILTERS,
  LANGUAGES,
  VENUE_SETTINGS,
  ageSpan,
  applyFilters,
  filtersToParams,
  paramsToFilters,
  type AgeBucket,
  type FilterState,
} from "./filters";
import type { Activity } from "../types";

/** A minimally-valid activity; override any field. */
function act(over: Partial<Activity> = {}): Activity {
  return {
    id: "a1",
    source: "uitinleuven",
    source_label: "UiT in Leuven",
    source_event_id: null,
    url: "https://example.be/e/1",
    last_seen_run: "run-1",
    title_nl: "Kindervoorstelling",
    description_nl: "Een leuke voorstelling",
    organizer_nl: null,
    blurb_en: "A nice show",
    image_url: null,
    date_start: "2026-10-17T10:00:00",
    date_end: "2026-10-17T12:00:00",
    all_day: false,
    occurrences: [],
    date_kind: "single",
    weekend_bucket: ["this_weekend"],
    in_school_holiday: false,
    school_holiday_name: null,
    venue_name: "Zaal",
    address: null,
    city: "Leuven",
    postal_code: null,
    lat: 50.87,
    lng: 4.7,
    distance_km: 5,
    geocode_source: "payload",
    category: "theatre_puppetry",
    feature_tags: [],
    audience: "family",
    age_min: 4,
    age_max: 10,
    age_source: "uit",
    fits_4yo: true,
    fits_8yo: true,
    price_type: "paid",
    price_min_eur: 5,
    price_max_eur: 5,
    price_note_nl: null,
    primary_language: "nl",
    french_required: false,
    language_note: null,
    language_free: false,
    is_special_event: true,
    is_recurring_class: false,
    booking_required: null,
    enrichment_model: "haiku",
    confidence: "high",
    ...over,
  } as Activity;
}

const base = (over: Partial<FilterState> = {}): FilterState => ({
  ...DEFAULT_FILTERS,
  ...over,
});

// ── ageSpan ─────────────────────────────────────────────────────────────────
describe("ageSpan", () => {
  it("passes a normal range through", () => {
    expect(ageSpan(act({ age_min: 4, age_max: 10 }))).toEqual([4, 10]);
  });

  it.each([null, 99, 18, 21])("treats %s as no upper bound", (age_max) => {
    // Regression: the "11+" chip is open-ended, so an adult-ward or sentinel
    // upper bound must not make it look like a closed range that excludes it.
    expect(ageSpan(act({ age_max }))[1]).toBe(99);
  });

  it("keeps a real upper bound below 18", () => {
    expect(ageSpan(act({ age_max: 12 }))[1]).toBe(12);
  });

  it("defaults a missing lower bound to 0", () => {
    expect(ageSpan(act({ age_min: null }))[0]).toBe(0);
  });
});

// ── age buckets ─────────────────────────────────────────────────────────────
describe("age filtering", () => {
  const run = (a: Activity, ages: AgeBucket[]) => applyFilters([a], base({ ages })).length;

  it("keeps everything when no age is selected", () => {
    expect(run(act({ age_min: 30, age_max: 40 }), [])).toBe(1);
  });

  it("matches by span overlap, not exact equality", () => {
    expect(run(act({ age_min: 4, age_max: 10 }), ["8"])).toBe(1);
  });

  it("excludes an activity whose span misses the bucket", () => {
    expect(run(act({ age_min: 12, age_max: 16 }), ["4"])).toBe(0);
  });

  it("0-3 covers the pre-school years", () => {
    expect(run(act({ age_min: 2, age_max: 3 }), ["0-3"])).toBe(1);
    expect(run(act({ age_min: 6, age_max: 8 }), ["0-3"])).toBe(0);
  });

  it("11+ is genuinely open-ended", () => {
    expect(run(act({ age_min: 14, age_max: null }), ["11+"])).toBe(1);
    expect(run(act({ age_min: 30, age_max: 99 }), ["11+"])).toBe(1);
  });

  it("any selected bucket is enough", () => {
    expect(run(act({ age_min: 8, age_max: 9 }), ["4", "8"])).toBe(1);
  });
});

// ── languages ───────────────────────────────────────────────────────────────
describe("language filtering", () => {
  const run = (a: Activity, languages: FilterState["languages"]) =>
    applyFilters([a], base({ languages })).length;

  it("keeps everything when no language is selected", () => {
    expect(run(act({ primary_language: "fr" }), [])).toBe(1);
  });

  it("keeps a language you speak", () => {
    expect(run(act({ primary_language: "fr" }), ["fr"])).toBe(1);
  });

  it("drops a language you do not speak", () => {
    expect(run(act({ primary_language: "fr" }), ["nl"])).toBe(0);
  });

  it("always keeps bilingual content", () => {
    expect(run(act({ primary_language: "multi" }), ["en"])).toBe(1);
  });

  it("always keeps language-free activities", () => {
    // A playground works whatever you speak — this is why language_free exists.
    expect(run(act({ primary_language: "fr", language_free: true }), ["nl"])).toBe(1);
  });
});

// ── indoor / outdoor ────────────────────────────────────────────────────────
describe("venue filtering", () => {
  const run = (a: Activity, venue: FilterState["venue"]) =>
    applyFilters([a], base({ venue })).length;

  it("keeps everything when nothing is selected", () => {
    expect(run(act({ indoor_outdoor: "outdoor" }), [])).toBe(1);
  });

  it("keeps a matching setting", () => {
    expect(run(act({ indoor_outdoor: "indoor" }), ["indoor"])).toBe(1);
  });

  it("drops a non-matching setting", () => {
    expect(run(act({ indoor_outdoor: "outdoor" }), ["indoor"])).toBe(0);
  });

  it("always keeps 'both' — a zoo with pavilions answers 'somewhere indoor'", () => {
    expect(run(act({ indoor_outdoor: "both" }), ["indoor"])).toBe(1);
    expect(run(act({ indoor_outdoor: "both" }), ["outdoor"])).toBe(1);
  });

  it("keeps an unclassified activity rather than hiding it", () => {
    // Regression: the old test was `indoor === true`, and since every event
    // carries indoor: null, switching the filter on emptied the whole tab.
    expect(run(act({ indoor_outdoor: null, indoor: null }), ["indoor"])).toBe(1);
  });

  it("falls back to the legacy boolean for old payloads", () => {
    expect(run(act({ indoor_outdoor: undefined, indoor: true }), ["indoor"])).toBe(1);
    expect(run(act({ indoor_outdoor: undefined, indoor: true }), ["outdoor"])).toBe(0);
  });
});

// ── other predicates ────────────────────────────────────────────────────────
describe("search", () => {
  it("matches across title, blurb, venue and city", () => {
    expect(applyFilters([act()], base({ search: "kindervoorstelling" }))).toHaveLength(1);
    expect(applyFilters([act()], base({ search: "leuven" }))).toHaveLength(1);
  });

  it("requires every term to match", () => {
    expect(applyFilters([act()], base({ search: "kindervoorstelling leuven" }))).toHaveLength(1);
    expect(applyFilters([act()], base({ search: "kindervoorstelling gent" }))).toHaveLength(0);
  });

  it("is case insensitive", () => {
    expect(applyFilters([act()], base({ search: "KINDER" }))).toHaveLength(1);
  });
});

describe("distance", () => {
  it("drops anything beyond the radius", () => {
    expect(applyFilters([act({ distance_km: 120 })], base({ maxDistance: 50 }))).toHaveLength(0);
  });

  it("keeps an ungeocoded activity rather than hiding it", () => {
    expect(applyFilters([act({ distance_km: null })], base({ maxDistance: 50 }))).toHaveLength(1);
  });
});

describe("price", () => {
  it("free keeps only free activities", () => {
    expect(applyFilters([act({ price_type: "free", price_min_eur: 0 })], base({ price: "free" }))).toHaveLength(1);
    expect(applyFilters([act({ price_type: "paid", price_min_eur: 12 })], base({ price: "free" }))).toHaveLength(0);
  });

  it("cheap allows up to 10 euro", () => {
    expect(applyFilters([act({ price_max_eur: 8 })], base({ price: "cheap" }))).toHaveLength(1);
    expect(applyFilters([act({ price_max_eur: 25 })], base({ price: "cheap" }))).toHaveLength(0);
  });
});

describe("classes and tabs", () => {
  it("hides weekly classes by default on the weekend tab", () => {
    const cls = act({ is_recurring_class: true });
    expect(applyFilters([cls], base({ tab: "weekend", hideClasses: true }))).toHaveLength(0);
    expect(applyFilters([cls], base({ tab: "weekend", hideClasses: false }))).toHaveLength(1);
  });

  it("keeps permanent places off the weekend tab", () => {
    const place = act({ date_kind: "permanent", kind: "museum" });
    expect(applyFilters([place], base({ tab: "weekend" }))).toHaveLength(0);
  });

  it("keeps dated events off the places tab", () => {
    expect(applyFilters([act()], base({ tab: "places" }))).toHaveLength(0);
  });

  it("routes zomerbars to their own tab", () => {
    const bar = act({ date_kind: "permanent", kind: "zomerbar" });
    expect(applyFilters([bar], base({ tab: "zomerbar" }))).toHaveLength(1);
    expect(applyFilters([bar], base({ tab: "places" }))).toHaveLength(0);
  });
});

describe("when", () => {
  it("filters by weekend bucket", () => {
    const wed = act({ weekend_bucket: ["wednesday", "later"] });
    expect(applyFilters([wed], base({ when: "wednesday" }))).toHaveLength(1);
    expect(applyFilters([wed], base({ when: "this_weekend" }))).toHaveLength(0);
  });
});

describe("stale weekend feed", () => {
  it("drops a dated event whose bucket has gone empty (past the 13-week horizon)", () => {
    const stale = act({ weekend_bucket: [] });
    expect(applyFilters([stale], base({ tab: "weekend" }))).toHaveLength(0);
  });

  it("drops a dated event whose dates have all passed", () => {
    const past = act({
      weekend_bucket: ["later"],
      date_start: "2020-01-01T10:00:00",
      date_end: "2020-01-01T12:00:00",
      occurrences: [{ start: "2020-01-01T10:00:00", end: "2020-01-01T12:00:00" }],
    });
    expect(applyFilters([past], base({ tab: "weekend" }))).toHaveLength(0);
  });
});

// ── URL round-trip ──────────────────────────────────────────────────────────
describe("URL serialisation", () => {
  it("omits defaults so a clean state gives a clean URL", () => {
    expect(filtersToParams(DEFAULT_FILTERS)).toBe("");
  });

  it("round-trips a fully populated state", () => {
    const state = base({
      tab: "places",
      search: "bos",
      ages: ["4", "8"],
      languages: ["nl", "fr"],
      venue: ["indoor"],
      origin: "brussels-1000",
      maxDistance: 75,
      price: "free",
      when: "wednesday",
      hideClasses: false,
      specialOnly: true,
      sort: "distance",
    });
    expect(paramsToFilters(filtersToParams(state))).toEqual(state);
  });

  it.each([
    [["4", "8"], ["8", "4"]],
    [["nl", "fr"], ["fr", "nl"]],
  ])("serialises a selection identically whatever order it was built in", (a, b) => {
    const one = filtersToParams(base({ ages: a as AgeBucket[] }));
    const two = filtersToParams(base({ ages: b as AgeBucket[] }));
    expect(paramsToFilters(one).ages).toEqual(paramsToFilters(two).ages);
  });

  it("ignores unknown values rather than trusting the URL", () => {
    expect(paramsToFilters("?age=99,4").ages).toEqual(["4"]);
    expect(paramsToFilters("?lang=nl,klingon").languages).toEqual(["nl"]);
    expect(paramsToFilters("?venue=indoor,underwater").venue).toEqual(["indoor"]);
  });
});

describe("legacy URLs", () => {
  it.each([
    ["4yo", ["4"]],
    ["8yo", ["8"]],
    ["both", ["4", "8"]],
  ])("translates the old age=%s parameter", (legacy, expected) => {
    expect(paramsToFilters(`?age=${legacy}`).ages).toEqual(expected);
  });

  it("translates the old nofr=1 toggle", () => {
    expect(paramsToFilters("?nofr=1").languages).toEqual(["nl", "en"]);
  });

  it("translates the old indoor=1 toggle", () => {
    expect(paramsToFilters("?indoor=1").venue).toEqual(["indoor"]);
  });
});

describe("saved preferences vs shared links", () => {
  const prefs = { origin: "brussels-1000", ages: ["8"] as AgeBucket[], languages: ["fr"] as const };

  it("seeds a bare visit from saved preferences", () => {
    const f = paramsToFilters("", prefs as never);
    expect(f.origin).toBe("brussels-1000");
    expect(f.ages).toEqual(["8"]);
  });

  it("ignores preferences entirely once the URL carries any state", () => {
    // Regression: a sender on global defaults omits from/age/lang, so filling
    // those gaps from the recipient's prefs opened their Brussels location and
    // age filter instead of what the sender actually saw.
    const f = paramsToFilters("?when=this_weekend", prefs as never);
    expect(f.origin).toBe(DEFAULT_FILTERS.origin);
    expect(f.ages).toEqual([]);
    expect(f.languages).toEqual([]);
    expect(f.when).toBe("this_weekend");
  });

  it("still honours explicit params over preferences", () => {
    const f = paramsToFilters("?from=antwerp-2000", prefs as never);
    expect(f.origin).toBe("antwerp-2000");
  });
});

// ── vocab wiring ────────────────────────────────────────────────────────────
describe("chip vocabularies", () => {
  it("age buckets are unique and ordered", () => {
    expect(new Set(AGE_BUCKETS).size).toBe(AGE_BUCKETS.length);
    expect(AGE_BUCKETS[0]).toBe("0-3");
    expect(AGE_BUCKETS[AGE_BUCKETS.length - 1]).toBe("11+");
  });

  it("offers indoor and outdoor but not 'both' as a chip", () => {
    // "both" is a data value, not a choice — picking it would be meaningless
    // since it already survives either selection.
    expect(VENUE_SETTINGS).toEqual(["indoor", "outdoor"]);
  });

  it("languages cover the three the app supports", () => {
    expect(LANGUAGES).toEqual(["nl", "fr", "en"]);
  });
});
