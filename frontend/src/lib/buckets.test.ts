import { describe, expect, it, vi } from "vitest";
import type { Activity, SchoolHoliday } from "../types";
import {
  belgiumToday,
  computeBuckets,
  isPastEvent,
  withBuckets,
  ymdToDayNum,
  type BelgiumDate,
} from "./buckets";

/** Only the fields the bucket logic reads. */
function act(over: Partial<Activity> = {}): Activity {
  return {
    date_kind: "single",
    all_day: false,
    date_start: null,
    date_end: null,
    occurrences: [],
    ...over,
  } as Activity;
}

/** A single-day event at the given local date/time. */
function on(date: string, time = "10:00"): Activity {
  return act({
    date_start: `${date}T${time}:00`,
    date_end: `${date}T${time}:00`,
    occurrences: [{ start: `${date}T${time}:00`, end: `${date}T${time}:00` }],
  });
}

const NL: SchoolHoliday[] = [
  { name: "Herfstvakantie", start: "2026-10-26", end: "2026-11-01" },
];
const FR: SchoolHoliday[] = [
  { name: "Congé d'automne", start: "2026-10-19", end: "2026-10-30" },
];

describe("weekend windows", () => {
  // 2026-09-12 is a Saturday, 2026-09-13 a Sunday.
  const SAT: BelgiumDate = [2026, 9, 12];
  const SUN: BelgiumDate = [2026, 9, 13];
  const MON: BelgiumDate = [2026, 9, 14];

  it("on Saturday, Saturday and Sunday are this_weekend", () => {
    expect(computeBuckets(on("2026-09-12"), SAT).weekend_bucket).toContain("this_weekend");
    expect(computeBuckets(on("2026-09-13"), SAT).weekend_bucket).toContain("this_weekend");
  });

  it("on Sunday, the weekend underway is still this_weekend (not next_weekend)", () => {
    const b = computeBuckets(on("2026-09-12"), SUN).weekend_bucket; // yesterday's Saturday
    expect(b).toContain("this_weekend");
    expect(b).not.toContain("next_weekend");
  });

  it("on Sunday, the following Saturday is next_weekend", () => {
    const b = computeBuckets(on("2026-09-19"), SUN).weekend_bucket;
    expect(b).toContain("next_weekend");
    expect(b).not.toContain("this_weekend");
  });

  it("on Monday, the coming Saturday is this_weekend and the one after is next_weekend", () => {
    expect(computeBuckets(on("2026-09-19"), MON).weekend_bucket).toContain("this_weekend");
    expect(computeBuckets(on("2026-09-26"), MON).weekend_bucket).toContain("next_weekend");
  });
});

describe("wednesday half-day rule", () => {
  // 2026-09-14 Monday -> coming Wednesday is 2026-09-16.
  const MON: BelgiumDate = [2026, 9, 14];

  it("excludes a single-day event that starts before noon", () => {
    expect(computeBuckets(on("2026-09-16", "09:00"), MON).weekend_bucket).not.toContain("wednesday");
  });

  it("includes a single-day event that starts at or after noon", () => {
    expect(computeBuckets(on("2026-09-16", "14:00"), MON).weekend_bucket).toContain("wednesday");
  });

  it("includes an all-day event", () => {
    const a = act({
      all_day: true,
      date_start: "2026-09-16T00:00:00",
      occurrences: [{ start: "2026-09-16T00:00:00", end: null }],
    });
    expect(computeBuckets(a, MON).weekend_bucket).toContain("wednesday");
  });

  it("includes a multi-day run covering Wednesday regardless of start hour", () => {
    const a = act({
      date_kind: "multi_day",
      date_start: "2026-09-15T09:00:00",
      date_end: "2026-09-17T17:00:00",
      occurrences: [{ start: "2026-09-15T09:00:00", end: "2026-09-17T17:00:00" }],
    });
    expect(computeBuckets(a, MON).weekend_bucket).toContain("wednesday");
  });
});

describe("later horizon", () => {
  const MON: BelgiumDate = [2026, 9, 14];

  it("tags an event three weeks out", () => {
    expect(computeBuckets(on("2026-10-05"), MON).weekend_bucket).toContain("later");
  });

  it("drops an event beyond the 13-week window to an empty bucket", () => {
    // ~20 weeks out, not on any weekend window, no holiday.
    const b = computeBuckets(on("2027-02-01"), MON).weekend_bucket;
    expect(b).toEqual([]);
  });
});

describe("school holidays", () => {
  const MON: BelgiumDate = [2026, 9, 14];

  it("tags an event inside the Flemish holiday and records the name", () => {
    const r = computeBuckets(on("2026-10-28"), MON, NL, FR);
    expect(r.weekend_bucket).toContain("school_holiday");
    expect(r.school_holiday_nl).toBe("Herfstvakantie");
    expect(r.school_holiday_fr).toBe("Congé d'automne");
    expect(r.in_school_holiday).toBe(true);
  });

  it("tags an event inside only the FWB holiday", () => {
    const r = computeBuckets(on("2026-10-20"), MON, NL, FR);
    expect(r.weekend_bucket).toContain("school_holiday");
    expect(r.school_holiday_nl).toBeNull();
    expect(r.school_holiday_fr).toBe("Congé d'automne");
  });

  it("leaves a non-holiday event untagged", () => {
    const r = computeBuckets(on("2026-10-05"), MON, NL, FR);
    expect(r.weekend_bucket).not.toContain("school_holiday");
    expect(r.in_school_holiday).toBe(false);
  });
});

describe("permanent activities", () => {
  it("are always just 'later'", () => {
    const r = computeBuckets(act({ date_kind: "permanent" }), [2026, 9, 14], NL, FR);
    expect(r.weekend_bucket).toEqual(["later"]);
    expect(r.in_school_holiday).toBe(false);
  });
});

describe("DST safety", () => {
  it("adds exactly 7 calendar days across the spring-forward boundary", () => {
    // 2027-03-28 is the EU spring-forward Sunday. From Sunday 2027-03-21,
    // "next weekend" must be 2027-03-27/28, not shifted by the lost hour.
    const b = computeBuckets(on("2027-03-27"), [2027, 3, 21]).weekend_bucket;
    expect(b).toContain("next_weekend");
  });
});

describe("isPastEvent", () => {
  const TODAY: BelgiumDate = [2026, 9, 14];

  it("is true when every date is before today", () => {
    expect(isPastEvent(on("2026-09-06"), TODAY)).toBe(true);
  });

  it("is false when an occurrence is today or later", () => {
    expect(isPastEvent(on("2026-09-14"), TODAY)).toBe(false);
    expect(isPastEvent(on("2026-09-20"), TODAY)).toBe(false);
  });

  it("is false for a permanent activity", () => {
    expect(isPastEvent(act({ date_kind: "permanent" }), TODAY)).toBe(false);
  });

  it("is false when there are no dates at all", () => {
    expect(isPastEvent(act(), TODAY)).toBe(false);
  });

  it("keeps a multi-day run alive while its end is still ahead", () => {
    const a = act({
      date_kind: "multi_day",
      date_start: "2026-09-01T10:00:00",
      date_end: "2026-12-01T10:00:00",
      occurrences: [{ start: "2026-09-01T10:00:00", end: "2026-12-01T10:00:00" }],
    });
    expect(isPastEvent(a, TODAY)).toBe(false);
  });
});

describe("withBuckets", () => {
  it("overrides the stale buckets from the feed", () => {
    const stale = on("2026-09-19");
    stale.weekend_bucket = ["later"];
    const [fresh] = withBuckets([stale], NL, FR, [2026, 9, 14]);
    expect(fresh.weekend_bucket).toContain("this_weekend");
  });
});

describe("belgiumToday", () => {
  it("returns the Brussels calendar date", () => {
    vi.useFakeTimers();
    // 2026-06-30 23:30 UTC is already 2026-07-01 01:30 in Brussels (CEST).
    vi.setSystemTime(new Date("2026-06-30T23:30:00Z"));
    expect(belgiumToday()).toEqual([2026, 7, 1]);
    vi.useRealTimers();
  });
});

describe("ymdToDayNum", () => {
  it("counts whole UTC days from the epoch", () => {
    expect(ymdToDayNum(1970, 1, 1)).toBe(0);
    expect(ymdToDayNum(1970, 1, 2)).toBe(1);
  });
});
