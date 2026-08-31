import type { Activity, Category, FeatureTag, WeekendBucket } from "../types";
import { firstFutureDate, formatPrice } from "./format";

export type Tab = "weekend" | "places";
export type AgeFilter = "any" | "4yo" | "8yo" | "both";
export type PriceFilter = "any" | "free" | "cheap";
export type WhenFilter = "any" | WeekendBucket;
export type SortKey = "date" | "distance" | "price";

export interface FilterState {
  tab: Tab;
  search: string;
  categories: Category[];
  features: FeatureTag[];
  maxDistance: number;
  price: PriceFilter;
  age: AgeFilter;
  hideFrench: boolean;
  hideClasses: boolean;
  when: WhenFilter;
  specialOnly: boolean;
  sort: SortKey;
}

export const DEFAULT_FILTERS: FilterState = {
  tab: "weekend",
  search: "",
  categories: [],
  features: [],
  maxDistance: 50,
  price: "any",
  age: "any",
  hideFrench: false,
  hideClasses: true,
  when: "any",
  specialOnly: false,
  sort: "date",
};

// ── URL sync ────────────────────────────────────────────────────────────────
export function filtersToParams(f: FilterState): string {
  const p = new URLSearchParams();
  if (f.tab !== "weekend") p.set("tab", f.tab);
  if (f.search) p.set("q", f.search);
  if (f.categories.length) p.set("cat", f.categories.join(","));
  if (f.features.length) p.set("feat", f.features.join(","));
  if (f.maxDistance !== DEFAULT_FILTERS.maxDistance) p.set("km", String(f.maxDistance));
  if (f.price !== "any") p.set("price", f.price);
  if (f.age !== "any") p.set("age", f.age);
  if (f.hideFrench) p.set("nofr", "1");
  if (!f.hideClasses) p.set("classes", "1");
  if (f.when !== "any") p.set("when", f.when);
  if (f.specialOnly) p.set("special", "1");
  if (f.sort !== "date") p.set("sort", f.sort);
  const s = p.toString();
  return s ? `?${s}` : "";
}

export function paramsToFilters(search: string): FilterState {
  const p = new URLSearchParams(search);
  const list = (v: string | null) => (v ? (v.split(",").filter(Boolean) as never[]) : []);
  return {
    ...DEFAULT_FILTERS,
    tab: (p.get("tab") as Tab) || "weekend",
    search: p.get("q") ?? "",
    categories: list(p.get("cat")),
    features: list(p.get("feat")),
    maxDistance: p.get("km") ? Number(p.get("km")) : DEFAULT_FILTERS.maxDistance,
    price: (p.get("price") as PriceFilter) || "any",
    age: (p.get("age") as AgeFilter) || "any",
    hideFrench: p.get("nofr") === "1",
    hideClasses: p.get("classes") !== "1",
    when: (p.get("when") as WhenFilter) || "any",
    specialOnly: p.get("special") === "1",
    sort: (p.get("sort") as SortKey) || "date",
  };
}

// ── predicates ──────────────────────────────────────────────────────────────
function matchesSearch(a: Activity, q: string): boolean {
  if (!q) return true;
  const hay = [
    a.title_nl,
    a.description_nl,
    a.blurb_en ?? "",
    a.venue_name ?? "",
    a.city ?? "",
    a.feature_tags.join(" "),
  ]
    .join(" ")
    .toLowerCase();
  return q
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((term) => hay.includes(term));
}

export function applyFilters(activities: Activity[], f: FilterState): Activity[] {
  const isPlace = (a: Activity) => a.date_kind === "permanent";
  let out = activities.filter((a) => (f.tab === "places" ? isPlace(a) : !isPlace(a)));

  out = out.filter((a) => {
    if (!matchesSearch(a, f.search)) return false;
    if (f.categories.length && !f.categories.includes(a.category)) return false;
    if (f.features.length && !f.features.some((t) => a.feature_tags.includes(t))) return false;
    if (a.distance_km != null && a.distance_km > f.maxDistance) return false;

    if (f.price === "free" && !formatPrice(a).free) return false;
    if (f.price === "cheap") {
      const hi = a.price_max_eur ?? a.price_min_eur;
      if (!(formatPrice(a).free || (hi != null && hi <= 10))) return false;
    }

    if (f.age === "4yo" && !a.fits_4yo) return false;
    if (f.age === "8yo" && !a.fits_8yo) return false;
    if (f.age === "both" && !(a.fits_4yo && a.fits_8yo)) return false;

    if (f.hideFrench && a.french_required) return false;
    if (f.hideClasses && f.tab === "weekend" && a.is_recurring_class) return false;
    if (f.tab === "weekend" && f.when !== "any" && !a.weekend_bucket.includes(f.when)) return false;
    if (f.specialOnly && !a.is_special_event) return false;
    return true;
  });

  const priceVal = (a: Activity) => a.price_min_eur ?? a.price_max_eur ?? (a.price_type === "free" ? 0 : 9999);
  out.sort((a, b) => {
    if (f.sort === "distance") return (a.distance_km ?? 1e9) - (b.distance_km ?? 1e9);
    if (f.sort === "price") return priceVal(a) - priceVal(b);
    const da = firstFutureDate(a)?.getTime() ?? 8.64e15;
    const db = firstFutureDate(b)?.getTime() ?? 8.64e15;
    return da - db;
  });
  return out;
}
