import type { Activity, SchoolHoliday, WeekendBucket } from "../types";

/**
 * Live re-derivation of `weekend_bucket` and the school-holiday flags in the
 * browser, against *today* rather than the pipeline run date.
 *
 * A faithful mirror of `automation/normalize.py` — `_wednesday`,
 * `_weekend_windows`, `_holiday_in`, `_bucketize` — the same way
 * `locations.ts#haversineKm` mirrors `automation/geo.py`. Keep the two in
 * lockstep. The weekly feed only refreshes on Mondays, so without this pass
 * "this weekend" / "next weekend" drift stale within a couple of days.
 *
 * Dates are handled as integer day-numbers (whole days since the Unix epoch,
 * UTC), so all the arithmetic is DST-proof and set-membership is exact. Date
 * strings are read literally (the wall-clock Y-M-D / hour as written, with their
 * own offset) to match how `dateutil` + `.date()`/`.hour` behave on the Python
 * side — never via `new Date()`, which would rebase to the viewer's timezone.
 */

const MS_PER_DAY = 86_400_000;

/** config.WINDOW_WEEKS — the "later" horizon. */
export const WINDOW_WEEKS = 13;

/** A span longer than this is almost certainly bad data; cap the expansion so a
 *  malformed `date_end` can't spin the browser. The pipeline has no such guard
 *  but also never renders in a tight loop. */
const MAX_SPAN_DAYS = 800;

/** Whole days between the Unix epoch and y-m-d, in UTC. `month` is 1-based. */
export function ymdToDayNum(year: number, month: number, day: number): number {
  return Math.floor(Date.UTC(year, month - 1, day) / MS_PER_DAY);
}

/** Mon=0 … Sun=6, matching Python's `date.weekday()`. Epoch day 0
 *  (1970-01-01) is a Thursday, i.e. index 3. */
function mondayIndex(dayNum: number): number {
  return (((dayNum + 3) % 7) + 7) % 7;
}

/** Python's `a % b` for a positive `b` (always non-negative). */
function pymod(a: number, b: number): number {
  return ((a % b) + b) % b;
}

export type BelgiumDate = [year: number, month: number, day: number];

/** Today's calendar date in Europe/Brussels — the app serves Belgium only, so
 *  this is both more correct and more deterministic than the viewer's own zone
 *  (and matches the pipeline, which runs on Brussels local time). */
export function belgiumToday(now: Date = new Date()): BelgiumDate {
  const iso = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Europe/Brussels",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).format(now); // "2026-09-09"
  const [y, m, d] = iso.split("-").map(Number);
  return [y, m, d];
}

interface Stamp {
  dayNum: number;
  hour: number;
}

const ISO_RE = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):)?/;

/** Literal Y-M-D (+ hour) from an ISO-ish string. A date-only string yields
 *  hour 0, matching `_parse_any_date` (midnight). */
function parseStamp(value: string | null | undefined): Stamp | null {
  if (!value) return null;
  const m = ISO_RE.exec(value);
  if (!m) return null;
  return {
    dayNum: ymdToDayNum(Number(m[1]), Number(m[2]), Number(m[3])),
    hour: m[4] === undefined ? 0 : Number(m[4]),
  };
}

/** Occurrence starts, falling back to `date_start` when there are none —
 *  covers both `_occ_starts` and `_occ_dates` on the Python side. */
function occStamps(a: Activity): Stamp[] {
  const out: Stamp[] = [];
  for (const o of a.occurrences ?? []) {
    const st = parseStamp(o.start);
    if (st) out.push(st);
  }
  if (out.length === 0) {
    const st = parseStamp(a.date_start);
    if (st) out.push(st);
  }
  return out;
}

function holidayNameOn(table: SchoolHoliday[] | undefined, dayNum: number): string | null {
  for (const h of table ?? []) {
    const s = parseStamp(h.start);
    const e = parseStamp(h.end);
    if (s && e && s.dayNum <= dayNum && dayNum <= e.dayNum) return h.name;
  }
  return null;
}

function firstHolidayName(sortedDays: number[], table: SchoolHoliday[] | undefined): string | null {
  for (const d of sortedDays) {
    const n = holidayNameOn(table, d);
    if (n) return n;
  }
  return null;
}

/** The next upcoming Wednesday (today included). */
function wednesdayDayNum(todayNum: number): number {
  return todayNum + pymod(2 - mondayIndex(todayNum), 7);
}

/** ({this-weekend days}, {next-weekend days}) as day-number sets. On a Sunday
 *  the modulo lands on *next* Saturday, so step back a week — but only on
 *  Sunday (Saturday already resolves to itself). */
function weekendWindows(todayNum: number): [Set<number>, Set<number>] {
  const wd = mondayIndex(todayNum);
  let offset = pymod(5 - wd, 7);
  if (wd === 6) offset -= 7;
  const saturday = todayNum + offset;
  return [
    new Set([saturday, saturday + 1]),
    new Set([saturday + 7, saturday + 8]),
  ];
}

export interface BucketFields {
  weekend_bucket: WeekendBucket[];
  in_school_holiday: boolean;
  school_holiday_name: string | null;
  school_holiday_nl: string | null;
  school_holiday_fr: string | null;
}

/** Port of `normalize._bucketize`. `today` is injected so callers (and tests)
 *  control "now". */
export function computeBuckets(
  a: Activity,
  today: BelgiumDate,
  holidaysNl?: SchoolHoliday[],
  holidaysFr?: SchoolHoliday[],
): BucketFields {
  if (a.date_kind === "permanent") {
    return {
      weekend_bucket: ["later"],
      in_school_holiday: false,
      school_holiday_name: null,
      school_holiday_nl: null,
      school_holiday_fr: null,
    };
  }

  const todayNum = ymdToDayNum(today[0], today[1], today[2]);
  const windowEnd = todayNum + WINDOW_WEEKS * 7;
  const wednesday = wednesdayDayNum(todayNum);
  const [thisWknd, nextWknd] = weekendWindows(todayNum);
  const buckets = new Set<WeekendBucket>();

  const stamps = occStamps(a);
  for (const st of stamps) {
    if (st.dayNum === wednesday && (a.all_day || st.hour === 0 || st.hour >= 12)) {
      buckets.add("wednesday");
    }
  }

  // Every date this activity touches: its own occurrences, plus the full span
  // of a periodic/multi-day run where we only captured start and end.
  const days = new Set<number>(stamps.map((s) => s.dayNum));
  const ds = parseStamp(a.date_start);
  const de = parseStamp(a.date_end);
  if (ds && de && de.dayNum >= ds.dayNum) {
    const last = Math.min(de.dayNum, ds.dayNum + MAX_SPAN_DAYS);
    const spanLen = last - ds.dayNum + 1;
    // A multi-day run covering Wednesday is open that afternoon, so it qualifies
    // outright. A single-day entry must still clear the half-day test.
    if (
      ds.dayNum <= wednesday &&
      wednesday <= last &&
      (spanLen > 1 || a.all_day || ds.hour === 0 || ds.hour >= 12)
    ) {
      buckets.add("wednesday");
    }
    for (let d = ds.dayNum; d <= last; d++) days.add(d);
  }

  for (const d of days) {
    if (thisWknd.has(d)) buckets.add("this_weekend");
    if (nextWknd.has(d)) buckets.add("next_weekend");
    if (todayNum <= d && d <= windowEnd) buckets.add("later");
  }

  const sortedDays = [...days].sort((x, y) => x - y);
  const nameNl = firstHolidayName(sortedDays, holidaysNl);
  const nameFr = firstHolidayName(sortedDays, holidaysFr);
  if (nameNl || nameFr) buckets.add("school_holiday");

  return {
    weekend_bucket: [...buckets].sort(),
    in_school_holiday: Boolean(nameNl || nameFr),
    school_holiday_name: nameNl ?? nameFr ?? null,
    school_holiday_nl: nameNl,
    school_holiday_fr: nameFr,
  };
}

/** True only when the activity has concrete dates and every one of them is
 *  before today. Mirrors the pipeline's own past-event drop
 *  (`normalize.normalize_all`): an event with *no* dates is not "past" — it is
 *  instead dropped downstream for having an empty `weekend_bucket`. */
export function isPastEvent(a: Activity, today: BelgiumDate = belgiumToday()): boolean {
  if (a.date_kind === "permanent") return false;
  const todayNum = ymdToDayNum(today[0], today[1], today[2]);
  const days = occStamps(a).map((s) => s.dayNum);
  const de = parseStamp(a.date_end);
  if (de) days.push(de.dayNum);
  if (days.length === 0) return false;
  return Math.max(...days) < todayNum;
}

/** Re-derive the date buckets for every activity, layered like
 *  `locations.withDistance`: callers keep reading `activity.weekend_bucket`. */
export function withBuckets(
  activities: Activity[],
  holidaysNl?: SchoolHoliday[],
  holidaysFr?: SchoolHoliday[],
  today: BelgiumDate = belgiumToday(),
): Activity[] {
  return activities.map((a) => ({ ...a, ...computeBuckets(a, today, holidaysNl, holidaysFr) }));
}
