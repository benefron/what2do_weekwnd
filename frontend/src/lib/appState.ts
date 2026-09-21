import type { Activity } from "../types";
import type { FilterState } from "./filters";

/**
 * How many filter fields differ from the effective defaults (DEFAULT_FILTERS
 * + saved prefs) — shown on the mobile "Show filters" toggle so a collapsed
 * panel still tells you something is active.
 *
 * This deliberately mirrors FilterBar.tsx's own `dirty` check (field-by-field,
 * ignoring `tab`, generic over FilterState's keys so a new field doesn't need
 * a second edit here) rather than being exported from FilterBar, since
 * FilterBar's Props/exports are frozen for this task. Keep the two in sync.
 */
export function activeFilterCount(f: FilterState, baseline: FilterState): number {
  return (Object.keys(f) as (keyof FilterState)[]).filter((key) => {
    if (key === "tab") return false;
    const a = f[key];
    const b = baseline[key];
    if (Array.isArray(a) && Array.isArray(b)) {
      return a.length !== b.length || a.some((v, i) => v !== b[i]);
    }
    return a !== b;
  }).length;
}

/**
 * True when `nextSearch` differs from `prevSearch` ONLY in the `tab` param
 * (and the tab actually changed) — the one case that should push a history
 * entry, so Back after switching tabs lands on the previous tab instead of
 * leaving the site. Every other filter change is a `replaceState` so chips
 * don't spam history.
 */
export function isTabOnlyChange(prevSearch: string, nextSearch: string): boolean {
  const prev = new URLSearchParams(prevSearch);
  const next = new URLSearchParams(nextSearch);
  const keys = new Set([...prev.keys(), ...next.keys()]);
  keys.delete("tab");
  for (const k of keys) {
    if (prev.get(k) !== next.get(k)) return false;
  }
  return prev.get("tab") !== next.get("tab");
}

/**
 * "Saved" state for a card that may represent a collapsed weekly series
 * (lib/series.ts#groupSeries): saved if ANY member's id is in the saved set,
 * not just the representative's own id, so a card doesn't silently lose its
 * star when the specific occurrence you starred isn't the one now shown.
 */
export function isActivitySaved(a: Activity, saved: ReadonlySet<string>): boolean {
  if (a.series_ids?.length) return a.series_ids.some((id) => saved.has(id));
  return saved.has(a.id);
}
