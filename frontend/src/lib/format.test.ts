import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  firstFutureDate,
  formatAgeRange,
  formatDate,
  formatDistance,
  formatPrice,
} from "./format";
import type { Activity } from "../types";

function act(over: Partial<Activity> = {}): Activity {
  return {
    date_kind: "single",
    date_start: "2026-10-20T14:00:00",
    date_end: "2026-10-20T16:00:00",
    all_day: false,
    occurrences: [{ start: "2026-10-20T14:00:00", end: "2026-10-20T16:00:00" }],
    price_type: "paid",
    price_min_eur: 5,
    price_max_eur: 5,
    distance_km: 12,
    age_min: 4,
    age_max: 10,
    ...over,
  } as Activity;
}

// The date helpers compare against "now", so pin it.
beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(new Date("2026-10-12T09:00:00"));
});
afterEach(() => vi.useRealTimers());

// ── firstFutureDate ─────────────────────────────────────────────────────────
describe("firstFutureDate", () => {
  it("picks the earliest upcoming occurrence", () => {
    const d = firstFutureDate(act({
      occurrences: [
        { start: "2026-11-01T10:00:00" },
        { start: "2026-10-20T10:00:00" },
        { start: "2026-12-01T10:00:00" },
      ],
    } as Partial<Activity>));
    expect(d?.toISOString().slice(0, 10)).toBe("2026-10-20");
  });

  it("skips occurrences that have passed", () => {
    const d = firstFutureDate(act({
      occurrences: [{ start: "2026-01-01T10:00:00" }, { start: "2026-10-20T10:00:00" }],
    } as Partial<Activity>));
    expect(d?.toISOString().slice(0, 10)).toBe("2026-10-20");
  });

  it("falls back to the earliest date when everything has passed", () => {
    const d = firstFutureDate(act({
      occurrences: [{ start: "2026-01-01T10:00:00" }, { start: "2026-02-01T10:00:00" }],
    } as Partial<Activity>));
    expect(d?.toISOString().slice(0, 10)).toBe("2026-01-01");
  });

  it("ignores unparseable dates", () => {
    const d = firstFutureDate(act({
      occurrences: [{ start: "not-a-date" }, { start: "2026-10-20T10:00:00" }],
    } as Partial<Activity>));
    expect(d?.toISOString().slice(0, 10)).toBe("2026-10-20");
  });

  it("returns null with no occurrences", () => {
    expect(firstFutureDate(act({ occurrences: [] }))).toBeNull();
  });
});

// ── formatDate ──────────────────────────────────────────────────────────────
describe("formatDate", () => {
  it("labels permanent places", () => {
    expect(formatDate(act({ date_kind: "permanent" }))).toBe("Open year-round");
  });

  it("shows a time for a timed single event", () => {
    expect(formatDate(act())).toMatch(/14:00/);
  });

  it("omits the time for an all-day event", () => {
    expect(formatDate(act({ all_day: true }))).not.toMatch(/\d\d:\d\d/);
  });

  it("counts extra occurrences", () => {
    expect(formatDate(act({
      occurrences: [{ start: "2026-10-20T10:00:00" }, { start: "2026-10-27T10:00:00" }],
    } as Partial<Activity>))).toMatch(/\+1 more/);
  });

  it("marks a recurring series", () => {
    expect(formatDate(act({ date_kind: "recurring" }))).toMatch(/recurring/);
  });

  it("shows a window for a long run already underway", () => {
    // Otherwise a festival that started in August shows a stale August date.
    expect(formatDate(act({
      date_kind: "multi_day",
      date_start: "2026-08-01T10:00:00",
      date_end: "2026-11-30T18:00:00",
      occurrences: [],
    }))).toMatch(/^until /);
  });

  it("shows both ends for a long run still in the future", () => {
    const text = formatDate(act({
      date_kind: "multi_day",
      date_start: "2026-11-01T10:00:00",
      date_end: "2026-12-30T18:00:00",
      occurrences: [],
    }));
    expect(text).toMatch(/–/);
    expect(text).not.toMatch(/^until /);
  });

  it("falls back when there is no usable date", () => {
    expect(formatDate(act({
      date_kind: "single", date_start: null, date_end: null, occurrences: [],
    }))).toBe("Date to confirm");
  });
});

// ── formatPrice ─────────────────────────────────────────────────────────────
describe("formatPrice", () => {
  it("labels free", () => {
    expect(formatPrice(act({ price_type: "free" }))).toEqual({ text: "Free", free: true });
  });

  it("labels donation as not free", () => {
    expect(formatPrice(act({ price_type: "donation" }))).toEqual({
      text: "Donation", free: false,
    });
  });

  it("shows a single price", () => {
    expect(formatPrice(act({ price_min_eur: 7, price_max_eur: 7 })).text).toBe("€7");
  });

  it("shows a range", () => {
    expect(formatPrice(act({ price_min_eur: 5, price_max_eur: 12 })).text).toBe("€5–12");
  });

  it("treats a zero paid price as free", () => {
    expect(formatPrice(act({ price_min_eur: 0, price_max_eur: 0 })).free).toBe(true);
  });

  it("falls back when paid with no amounts", () => {
    expect(formatPrice(act({ price_min_eur: null, price_max_eur: null })).text).toBe("Paid");
  });

  it("marks an unknown price", () => {
    expect(formatPrice(act({ price_type: "unknown" })).text).toBe("Price ?");
  });
});

// ── formatDistance ──────────────────────────────────────────────────────────
describe("formatDistance", () => {
  it("rounds to whole kilometres", () => {
    expect(formatDistance(act({ distance_km: 12.4 }), "Leuven")).toBe("12 km");
  });

  it("names the origin when you are basically there", () => {
    expect(formatDistance(act({ distance_km: 0.4 }), "Leuven")).toBe("in Leuven");
  });

  it("returns null for an ungeocoded activity", () => {
    expect(formatDistance(act({ distance_km: null }), "Leuven")).toBeNull();
  });
});

// ── formatAgeRange ──────────────────────────────────────────────────────────
describe("formatAgeRange", () => {
  it("shows a closed range", () => {
    expect(formatAgeRange(act({ age_min: 4, age_max: 10 }))).toBe("4–10");
  });

  it("shows a single age once", () => {
    expect(formatAgeRange(act({ age_min: 6, age_max: 6 }))).toBe("6");
  });

  it.each([null, 18, 99])("treats %s as open-ended", (age_max) => {
    expect(formatAgeRange(act({ age_min: 6, age_max }))).toBe("6+");
  });

  it("says all ages when both ends are open", () => {
    expect(formatAgeRange(act({ age_min: 0, age_max: null }))).toBe("All ages");
    expect(formatAgeRange(act({ age_min: null, age_max: 99 }))).toBe("All ages");
  });

  it("shows an upper bound alone", () => {
    expect(formatAgeRange(act({ age_min: 0, age_max: 8 }))).toBe("up to 8");
  });
});
