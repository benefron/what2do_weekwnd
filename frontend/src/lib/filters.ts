import type { Activity, Category, FeatureTag, PlaceKind, WeekendBucket } from "../types";
import { firstFutureDate, formatPrice } from "./format";
import { DEFAULT_ORIGIN } from "./locations";

export type Tab = "weekend" | "places" | "zomerbar" | "eatplay";

// which place kinds belong to which tab
export const TAB_PLACE_KINDS: Record<Exclude<Tab, "weekend">, PlaceKind[] | null> = {
  places: null, // everything permanent except the two below
  zomerbar: ["zomerbar"],
  eatplay: ["playground_restaurant"],
};

/** One chip per child age. "0-3" lumps the pre-school years; "11+" is open-ended. */
export type AgeBucket = "0-3" | "4" | "5" | "6" | "7" | "8" | "9" | "10" | "11+";

export const AGE_BUCKETS: AgeBucket[] = ["0-3", "4", "5", "6", "7", "8", "9", "10", "11+"];

export const AGE_BUCKET_RANGE: Record<AgeBucket, [number, number]> = {
  "0-3": [0, 3],
  "4": [4, 4],
  "5": [5, 5],
  "6": [6, 6],
  "7": [7, 7],
  "8": [8, 8],
  "9": [9, 9],
  "10": [10, 10],
  "11+": [11, 99],  // genuinely open-ended, matching ageSpan's own treatment of null/>=18 upper bounds
};

export type Language = "nl" | "fr" | "en";
export const LANGUAGES: Language[] = ["nl", "fr", "en"];

export type PriceFilter = "any" | "free" | "cheap";
export type WhenFilter = "any" | WeekendBucket;
export type SortKey = "date" | "distance" | "price";

export interface FilterState {
  tab: Tab;
  search: string;
  categories: Category[];
  placeKinds: PlaceKind[];
  features: FeatureTag[];
  origin: string;
  maxDistance: number;
  indoorOnly: boolean;
  price: PriceFilter;
  ages: AgeBucket[];
  languages: Language[];
  hideClasses: boolean;
  when: WhenFilter;
  specialOnly: boolean;
  sort: SortKey;
}

export const DEFAULT_FILTERS: FilterState = {
  tab: "weekend",
  search: "",
  categories: [],
  placeKinds: [],
  features: [],
  origin: DEFAULT_ORIGIN.key,
  maxDistance: 50,
  indoorOnly: false,
  price: "any",
  ages: [],
  languages: [],
  hideClasses: true,
  when: "any",
  specialOnly: false,
  sort: "date",
};

/** The filter fields worth remembering between visits (see App.tsx prefs). */
export type SavedPrefs = Partial<Pick<FilterState, "origin" | "ages" | "languages">>;

// ── URL sync ────────────────────────────────────────────────────────────────
export function filtersToParams(f: FilterState): string {
  const p = new URLSearchParams();
  if (f.tab !== "weekend") p.set("tab", f.tab);
  if (f.search) p.set("q", f.search);
  if (f.categories.length) p.set("cat", f.categories.join(","));
  if (f.placeKinds.length) p.set("pk", f.placeKinds.join(","));
  if (f.features.length) p.set("feat", f.features.join(","));
  if (f.origin !== DEFAULT_FILTERS.origin) p.set("from", f.origin);
  if (f.maxDistance !== DEFAULT_FILTERS.maxDistance) p.set("km", String(f.maxDistance));
  if (f.indoorOnly) p.set("indoor", "1");
  if (f.price !== "any") p.set("price", f.price);
  if (f.ages.length) p.set("age", f.ages.join(","));
  if (f.languages.length) p.set("lang", f.languages.join(","));
  if (!f.hideClasses) p.set("classes", "1");
  if (f.when !== "any") p.set("when", f.when);
  if (f.specialOnly) p.set("special", "1");
  if (f.sort !== "date") p.set("sort", f.sort);
  const s = p.toString();
  return s ? `?${s}` : "";
}

/** Links shared before the age filter became multi-select used age=4yo|8yo|both. */
const LEGACY_AGE: Record<string, AgeBucket[]> = {
  "4yo": ["4"],
  "8yo": ["8"],
  both: ["4", "8"],
};

function parseAges(raw: string | null): AgeBucket[] {
  if (!raw) return [];
  if (LEGACY_AGE[raw]) return LEGACY_AGE[raw];
  const valid = new Set<string>(AGE_BUCKETS);
  return AGE_BUCKETS.filter((b) => raw.split(",").includes(b) && valid.has(b));
}

/**
 * URL params win over `base` (the saved prefs), so a shared link always shows
 * the sender what they saw regardless of the recipient's stored preferences.
 *
 * A sender on a global default omits the corresponding param entirely, so if we
 * still filled those gaps from `base` a default-Leuven/no-age link would open at
 * the recipient's saved Brussels location with their saved age filter. Any param
 * present therefore means "reproduce exactly this state" and prefs are ignored;
 * `base` only fills a bare first visit with no query string at all.
 */
export function paramsToFilters(search: string, base: SavedPrefs = {}): FilterState {
  const p = new URLSearchParams(search);
  const list = (v: string | null) => (v ? (v.split(",").filter(Boolean) as never[]) : []);
  const langs = (v: string | null) =>
    v ? LANGUAGES.filter((l) => v.split(",").includes(l)) : [];
  const defaults = { ...DEFAULT_FILTERS, ...(Array.from(p.keys()).length ? {} : base) };
  return {
    ...defaults,
    tab: (p.get("tab") as Tab) || "weekend",
    search: p.get("q") ?? "",
    categories: list(p.get("cat")),
    placeKinds: list(p.get("pk")),
    features: list(p.get("feat")),
    origin: p.get("from") ?? defaults.origin,
    maxDistance: p.get("km") ? Number(p.get("km")) : defaults.maxDistance,
    indoorOnly: p.get("indoor") === "1",
    price: (p.get("price") as PriceFilter) || "any",
    ages: p.get("age") ? parseAges(p.get("age")) : defaults.ages,
    // nofr=1 was the old "hide French-only" toggle
    languages: p.get("lang")
      ? langs(p.get("lang"))
      : p.get("nofr") === "1"
        ? ["nl", "en"]
        : defaults.languages,
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

/**
 * The activity's age span, with the open-ended cases normalised. A null upper
 * bound means "no maximum"; so does 99 (the sentinel the enricher emits) and
 * anything adult-ward, so a "11+" chip must not be fooled into hiding them.
 */
export function ageSpan(a: Activity): [number, number] {
  return [a.age_min ?? 0, a.age_max == null || a.age_max >= 18 ? 99 : a.age_max];
}

function matchesAges(a: Activity, buckets: AgeBucket[]): boolean {
  if (!buckets.length) return true;
  const [lo, hi] = ageSpan(a);
  return buckets.some((b) => {
    const [blo, bhi] = AGE_BUCKET_RANGE[b];
    return blo <= hi && bhi >= lo;
  });
}

/**
 * "Languages we speak": content in one of them, anything explicitly bilingual,
 * and anything that doesn't depend on language at all (playgrounds, pools).
 */
function matchesLanguages(a: Activity, langs: Language[]): boolean {
  if (!langs.length) return true;
  if (a.primary_language === "multi") return true;
  if (a.language_free) return true;
  return langs.includes(a.primary_language as Language);
}

const SPECIAL_TAB_KINDS: PlaceKind[] = ["zomerbar", "playground_restaurant"];

export function applyFilters(activities: Activity[], f: FilterState): Activity[] {
  const isPlace = (a: Activity) => a.date_kind === "permanent";
  let out: Activity[];
  if (f.tab === "weekend") {
    out = activities.filter((a) => !isPlace(a));
  } else {
    const kinds = TAB_PLACE_KINDS[f.tab];
    out = activities.filter(
      (a) =>
        isPlace(a) &&
        (kinds
          ? kinds.includes((a.kind ?? "other") as PlaceKind)
          : !SPECIAL_TAB_KINDS.includes((a.kind ?? "other") as PlaceKind))
    );
  }

  out = out.filter((a) => {
    if (!matchesSearch(a, f.search)) return false;
    if (f.tab === "weekend" && f.categories.length && !f.categories.includes(a.category)) return false;
    if (f.tab === "places" && f.placeKinds.length && !f.placeKinds.includes((a.kind ?? "other") as PlaceKind))
      return false;
    if (f.indoorOnly && a.indoor !== true) return false;
    if (f.features.length && !f.features.some((t) => a.feature_tags.includes(t))) return false;
    if (a.distance_km != null && a.distance_km > f.maxDistance) return false;

    if (f.price === "free" && !formatPrice(a).free) return false;
    if (f.price === "cheap") {
      const hi = a.price_max_eur ?? a.price_min_eur;
      if (!(formatPrice(a).free || (hi != null && hi <= 10))) return false;
    }

    if (!matchesAges(a, f.ages)) return false;
    if (!matchesLanguages(a, f.languages)) return false;

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
