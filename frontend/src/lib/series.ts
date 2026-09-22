import type { Activity } from "../types";

/**
 * The pipeline's dedupe (automation/normalize.py) only merges records that
 * share the same `date_start`. A weekly UiT series ("Baboes", "Met pingpong
 * het weekend in", ...) is fetched as one row PER occurrence, each with its
 * own uuid/url — so the same event shows up as 3-5 separate cards. This
 * module collapses those, client-side, into one representative card that
 * carries the other dates along.
 *
 * Grouping key: normalised title (`title_nl`, the field the card displays)
 * + normalised `city` + `source`. `source` is included deliberately: two
 * different organisers running a same-named, same-city event (rare, but it
 * happens — e.g. a franchised workshop format) must NOT merge just because
 * the title collides; requiring the same feed is the cheap, safe guard for
 * that. A missing/empty city never merges — there is no second field (venue
 * name is inconsistent/missing across sources) that could safely stand in as
 * a match key, so "never merge without a city" is the safer failure mode
 * (a few surviving duplicate cards) over the alternative (wrongly folding two
 * different towns' events into one).
 *
 * Only DATED activities are considered — `date_kind === "permanent"` (a
 * places-tab entry) is never grouped, even if two places share a name.
 *
 * A pair of records that share the key AND have the exact same `date_start`
 * is NOT a series — that's the pipeline dedupe's territory (it should have
 * merged them upstream and didn't, for whatever reason) — so both are left
 * as separate, untouched cards here rather than being silently collapsed.
 */

function normalize(value: string | null | undefined): string {
  if (!value) return "";
  return value
    .normalize("NFD")
    .replace(/[̀-ͯ]/g, "")
    .toLowerCase()
    .trim()
    .replace(/\s+/g, " ");
}

function seriesKey(a: Activity): string | null {
  if (a.date_kind === "permanent") return null;
  const title = normalize(a.title_nl);
  const city = normalize(a.city);
  if (!title || !city) return null;
  return `${title}\u0000${city}\u0000${a.source}`;
}

/**
 * Groups a weekly series (same title/city/source, differing dates) into one
 * representative card. Pure, O(n): a single Map keyed on the normalised
 * (title, city, source) tuple, plus O(group size) work per group.
 *
 * The representative is a shallow copy carrying `other_dates` (the other
 * members' `date_start`, ascending, deduped) and `series_ids` (every
 * member's id, including the representative's own). Non-grouped activities
 * — and the non-representative members left ungrouped for the reasons above
 * — pass through with the exact same object identity, so callers relying on
 * reference equality (e.g. memoised card lists) are unaffected.
 */
export function groupSeries(activities: Activity[], today: Date = new Date()): Activity[] {
  const groups = new Map<string, Activity[]>();

  for (const a of activities) {
    const key = seriesKey(a);
    if (key === null) continue;
    const bucket = groups.get(key);
    if (bucket) bucket.push(a);
    else groups.set(key, [a]);
  }

  const replacement = new Map<Activity, Activity>(); // first-occurring member -> representative copy
  const toDrop = new Set<Activity>(); // other series members, removed from output

  for (const members of groups.values()) {
    if (members.length < 2) continue;

    // Only the first activity seen for a given date_start joins the series;
    // a later one with the SAME date_start is a dedupe-territory duplicate,
    // not a series occurrence, and is left as its own untouched card.
    const seenDates = new Set<string>();
    const seriesMembers: Activity[] = [];
    for (const m of members) {
      if (m.date_start == null) continue; // can't order/compare — leave untouched
      if (seenDates.has(m.date_start)) continue;
      seenDates.add(m.date_start);
      seriesMembers.push(m);
    }
    if (seriesMembers.length < 2) continue; // fewer than 2 distinct dates: not a series

    const todayTime = today.getTime();
    const time = (m: Activity) => new Date(m.date_start as string).getTime();
    const upcoming = seriesMembers.filter((m) => time(m) >= todayTime);

    const representativeSource = upcoming.length
      ? upcoming.reduce((soonest, m) => (time(m) < time(soonest) ? m : soonest))
      : seriesMembers.reduce((latest, m) => (time(m) > time(latest) ? m : latest));

    const otherDates = Array.from(
      new Set(
        seriesMembers
          .filter((m) => m !== representativeSource)
          .map((m) => m.date_start as string),
      ),
    ).sort();

    const rep: Activity = {
      ...representativeSource,
      other_dates: otherDates,
      series_ids: seriesMembers.map((m) => m.id),
    };

    // members[0] (not seriesMembers[0]) preserves the group's original
    // first-appearance position in the input array.
    const first = members.find((m) => seriesMembers.includes(m))!;
    replacement.set(first, rep);
    for (const m of seriesMembers) {
      if (m !== first) toDrop.add(m);
    }
  }

  const result: Activity[] = [];
  for (const a of activities) {
    if (toDrop.has(a)) continue;
    result.push(replacement.get(a) ?? a);
  }
  return result;
}
