import { describe, expect, it } from "vitest";
import { groupSeries } from "./series";
import type { Activity } from "../types";

/** A minimally-valid activity; override any field. Mirrors filters.test.ts's factory. */
function act(over: Partial<Activity> = {}): Activity {
  return {
    id: "a1",
    source: "uitinbrussel",
    source_label: "UiT in Brussel",
    url: "https://example.be/e/1",
    last_seen_run: "run-1",
    title_nl: "Baboes",
    description_nl: "Een leuke voorstelling",
    organizer_nl: null,
    blurb_en: "A nice show",
    image_url: null,
    date_start: "2026-09-30T09:00:00+02:00",
    date_end: "2026-09-30T11:00:00+02:00",
    all_day: false,
    occurrences: [],
    date_kind: "recurring",
    weekend_bucket: ["wednesday"],
    in_school_holiday: false,
    school_holiday_name: null,
    venue_name: "Zaal",
    address: null,
    city: "Elsene",
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
    is_special_event: false,
    is_recurring_class: true,
    booking_required: null,
    enrichment_model: "haiku",
    confidence: "high",
    ...over,
  } as Activity;
}

const TODAY = new Date("2026-09-21T00:00:00+02:00");

describe("groupSeries", () => {
  it("a weekly series collapses to one card with the other dates listed", () => {
    const members = [
      act({ id: "s1", date_start: "2026-09-30T09:00:00+02:00" }),
      act({ id: "s2", date_start: "2026-10-14T09:00:00+02:00" }),
      act({ id: "s3", date_start: "2026-10-21T09:00:00+02:00" }),
    ];
    const result = groupSeries(members, TODAY);
    expect(result).toHaveLength(1);
    expect(result[0].series_ids).toEqual(["s1", "s2", "s3"]);
    expect(result[0].other_dates).toEqual(["2026-10-14T09:00:00+02:00", "2026-10-21T09:00:00+02:00"]);
  });

  it("representative is the next upcoming date, not the first in the file", () => {
    // s1 (past) appears first in the array; s3 (soonest future) should still
    // be chosen as the representative and shown at s1's position.
    const s1 = act({ id: "s1", date_start: "2026-09-01T09:00:00+02:00" }); // past
    const s2 = act({ id: "s2", date_start: "2026-11-01T09:00:00+02:00" }); // later future
    const s3 = act({ id: "s3", date_start: "2026-09-30T09:00:00+02:00" }); // soonest future
    const other = act({ id: "other", title_nl: "Unrelated", city: "Gent", source: "uitinvlaanderen" });

    const result = groupSeries([s1, other, s2, s3], TODAY);

    expect(result).toHaveLength(2);
    expect(result[0].id).toBe("s3"); // representative sits at s1's original (first) position
    expect(result[0].series_ids).toEqual(["s1", "s2", "s3"]);
    expect(result[1].id).toBe("other");
  });

  it("falls back to the latest past occurrence when every date has passed", () => {
    const s1 = act({ id: "s1", date_start: "2026-08-01T09:00:00+02:00" });
    const s2 = act({ id: "s2", date_start: "2026-08-15T09:00:00+02:00" });
    const result = groupSeries([s1, s2], TODAY);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("s2"); // latest of the two past dates
  });

  it("the same title in two cities stays two cards", () => {
    const jette = act({ id: "j1", city: "Jette" });
    const elsene = act({ id: "e1", city: "Elsene", date_start: "2026-10-14T09:00:00+02:00" });
    const result = groupSeries([jette, elsene], TODAY);
    expect(result).toHaveLength(2);
    expect(result.map((a) => a.id).sort()).toEqual(["e1", "j1"]);
  });

  it("the same title and city from different sources stays two cards", () => {
    const a = act({ id: "a1", source: "uitinbrussel" });
    const b = act({ id: "a2", source: "uitinvlaanderen", date_start: "2026-10-14T09:00:00+02:00" });
    const result = groupSeries([a, b], TODAY);
    expect(result).toHaveLength(2);
  });

  it("two records with the same title, city and date_start are not collapsed", () => {
    // This is the pipeline dedupe's territory (normalize.py) — series
    // grouping must not silently hide one of a genuine same-day duplicate.
    const a = act({ id: "dup1", date_start: "2026-09-30T09:00:00+02:00" });
    const b = act({ id: "dup2", date_start: "2026-09-30T09:00:00+02:00" });
    const result = groupSeries([a, b], TODAY);
    expect(result).toHaveLength(2);
    expect(result.map((r) => r.id).sort()).toEqual(["dup1", "dup2"]);
  });

  it("a duplicate-date member inside an otherwise-real series is left untouched", () => {
    const s1 = act({ id: "s1", date_start: "2026-09-30T09:00:00+02:00" });
    const dup = act({ id: "dup", date_start: "2026-09-30T09:00:00+02:00" }); // same date as s1
    const s2 = act({ id: "s2", date_start: "2026-10-14T09:00:00+02:00" });
    const result = groupSeries([s1, dup, s2], TODAY);
    // s1+s2 form the series (2 distinct dates); dup passes through separately
    expect(result).toHaveLength(2);
    const rep = result.find((r) => r.series_ids);
    expect(rep?.series_ids).toEqual(["s1", "s2"]);
    expect(result.some((r) => r.id === "dup")).toBe(true);
  });

  it("places are never grouped, even with an identical name", () => {
    const p1 = act({ id: "p1", date_kind: "permanent", date_start: null, city: "Leuven" });
    const p2 = act({ id: "p2", date_kind: "permanent", date_start: null, city: "Leuven" });
    const result = groupSeries([p1, p2], TODAY);
    expect(result).toHaveLength(2);
  });

  it("a missing city never merges, even with a matching venue name", () => {
    const a = act({ id: "n1", city: null, venue_name: "Gemeenschapscentrum" });
    const b = act({ id: "n2", city: null, venue_name: "Gemeenschapscentrum", date_start: "2026-10-14T09:00:00+02:00" });
    const result = groupSeries([a, b], TODAY);
    expect(result).toHaveLength(2);
  });

  it("ungrouped activities keep object identity", () => {
    const solo = act({ id: "solo", title_nl: "Enkel Evenement" });
    const [result] = groupSeries([solo], TODAY);
    expect(result).toBe(solo);
  });

  it("a single occurrence with no siblings is left alone", () => {
    const solo = act({ id: "solo" });
    const result = groupSeries([solo], TODAY);
    expect(result).toHaveLength(1);
    expect(result[0].series_ids).toBeUndefined();
    expect(result[0].other_dates).toBeUndefined();
  });
});
