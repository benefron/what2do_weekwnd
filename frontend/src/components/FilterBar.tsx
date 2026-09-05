import { useState } from "react";
import type { Dataset } from "../types";
import {
  CATEGORY_LABELS,
  FEATURE_EMOJI,
  FEATURE_LABELS,
  LANGUAGE_EMOJI,
  LANGUAGE_LABELS,
  PLACE_KIND_EMOJI,
  PLACE_KIND_LABELS,
} from "../lib/labels";
import {
  AGE_BUCKETS,
  LANGUAGES,
  type AgeBucket,
  type FilterState,
  type Language,
  type PriceFilter,
  type SortKey,
  type WhenFilter,
} from "../lib/filters";
import { HOME_LOCATIONS, customOrigin, isCustomOrigin, type Origin } from "../lib/locations";

interface Props {
  filters: FilterState;
  dataset: Dataset;
  resultCount: number;
  origin: Origin;
  /** Effective defaults (DEFAULT_FILTERS + saved prefs) — what "cleared" means. */
  baseline: FilterState;
  onChange: (patch: Partial<FilterState>) => void;
  onReset: () => void;
}

function Toggle<T extends string>({
  options,
  active,
  onPick,
}: {
  options: { key: T; label: string }[];
  active: T;
  onPick: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onPick(o.key)}
          className={`chip ${active === o.key ? "chip--on" : ""}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Same chip row, but any number can be on at once. */
function MultiToggle<T extends string>({
  options,
  active,
  onToggle,
}: {
  options: { key: T; label: string }[];
  active: readonly T[];
  onToggle: (v: T) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o.key}
          onClick={() => onToggle(o.key)}
          aria-pressed={active.includes(o.key)}
          className={`chip ${active.includes(o.key) ? "chip--on" : ""}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export default function FilterBar({
  filters: f,
  dataset,
  resultCount,
  origin,
  baseline,
  onChange,
  onReset,
}: Props) {
  const [geoError, setGeoError] = useState<string | null>(null);

  // compared against the effective defaults, so restored preferences don't
  // permanently light up "Clear filters"
  const dirty = JSON.stringify({ ...f, tab: "x" }) !== JSON.stringify({ ...baseline, tab: "x" });

  // A fixed reference order per key, so a selection serialises identically no
  // matter what order the chips were clicked in — clicking "8" then "4" must
  // produce the same array (and URL/JSON) as "4" then "8".
  const CANONICAL_ORDER: Record<"categories" | "features" | "placeKinds" | "ages" | "languages", readonly string[]> = {
    ages: AGE_BUCKETS,
    languages: LANGUAGES,
    categories: dataset.categories.map((c) => c.key),
    placeKinds: (dataset.place_kinds ?? []).map((k) => k.key),
    features: dataset.feature_tags.map((t) => t.key),
  };

  const toggleIn = <K extends "categories" | "features" | "placeKinds" | "ages" | "languages">(
    key: K,
    val: FilterState[K][number]
  ) => {
    const set = new Set(f[key] as string[]);
    set.has(val as string) ? set.delete(val as string) : set.add(val as string);
    const order = CANONICAL_ORDER[key];
    const next = [...set].sort((a, b) => order.indexOf(a) - order.indexOf(b));
    onChange({ [key]: next } as unknown as Partial<FilterState>);
  };

  // Buckets are computed against the run date, so name the actual Wednesday.
  const wednesdayLabel = (() => {
    const g = new Date(dataset.generated_at);
    if (isNaN(g.getTime())) return "Wednesday";
    const isoDow = (g.getDay() + 6) % 7; // Mon=0 … Sun=6, matching the pipeline
    const wed = new Date(g);
    wed.setDate(wed.getDate() + (((2 - isoDow) % 7) + 7) % 7);
    return `Wed ${wed.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}`;
  })();

  const useMyLocation = () => {
    setGeoError(null);
    if (!navigator.geolocation) {
      setGeoError("This browser can't share your location.");
      return;
    }
    navigator.geolocation.getCurrentPosition(
      (pos) => onChange({ origin: customOrigin(pos.coords.latitude, pos.coords.longitude).key }),
      (err) =>
        setGeoError(
          err.code === err.PERMISSION_DENIED
            ? "Location permission denied — still measuring from " + origin.label + "."
            : "Couldn't get your location — still measuring from " + origin.label + "."
        ),
      { timeout: 10000, maximumAge: 300000 }
    );
  };

  return (
    <div className="flex flex-col gap-5">
      <input
        type="search"
        value={f.search}
        onChange={(e) => onChange({ search: e.target.value })}
        placeholder="Search activities, places, what's on…"
        className="w-full rounded-xl2 border border-line bg-white px-4 py-3 text-base shadow-card outline-none placeholder:text-muted/70 focus:border-tangerine"
      />

      {f.tab === "weekend" && (
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">When</p>
          <Toggle<WhenFilter>
            active={f.when}
            onPick={(when) =>
              // Wednesday afternoon is the school half-day, and most of what runs
              // then is weekly classes — which are hidden by default.
              onChange(when === "wednesday" ? { when, hideClasses: false } : { when })
            }
            options={[
              { key: "any", label: "Anytime" },
              { key: "wednesday", label: wednesdayLabel },
              { key: "this_weekend", label: "This weekend" },
              { key: "next_weekend", label: "Next weekend" },
              { key: "school_holiday", label: "School holidays" },
            ]}
          />
        </section>
      )}

      <section>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Starting from</p>
        <div className="mb-3 flex gap-1.5">
          <select
            value={isCustomOrigin(origin) ? "__custom" : origin.key}
            onChange={(e) => onChange({ origin: e.target.value })}
            className="min-w-0 flex-1 rounded-xl2 border border-line bg-white px-3 py-2 text-sm"
            aria-label="Measure distances from"
          >
            {isCustomOrigin(origin) && <option value="__custom">{origin.label}</option>}
            {HOME_LOCATIONS.map((o) => (
              <option key={o.key} value={o.key}>
                {o.label}
              </option>
            ))}
          </select>
          <button
            onClick={useMyLocation}
            title="Use my location"
            aria-label="Use my location"
            className="shrink-0 rounded-xl2 border border-line bg-white px-3 py-2 text-sm hover:border-tangerine"
          >
            📍
          </button>
        </div>
        {geoError && <p className="mb-2 text-xs text-berry">{geoError}</p>}

        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          Distance — within {f.maxDistance} km of {origin.label}
        </p>
        <input
          type="range"
          min={5}
          max={200}
          step={5}
          value={f.maxDistance}
          onChange={(e) => onChange({ maxDistance: Number(e.target.value) })}
          className="w-full accent-tangerine"
        />
      </section>

      <section className="flex flex-wrap gap-x-8 gap-y-4">
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Price</p>
          <Toggle<PriceFilter>
            active={f.price}
            onPick={(price) => onChange({ price })}
            options={[
              { key: "any", label: "Any" },
              { key: "free", label: "Free" },
              { key: "cheap", label: "≤ €10" },
            ]}
          />
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Sort</p>
          <Toggle<SortKey>
            active={f.sort}
            onPick={(sort) => onChange({ sort })}
            options={[
              { key: "date", label: "Date" },
              { key: "distance", label: "Distance" },
              { key: "price", label: "Price" },
            ]}
          />
        </div>
      </section>

      <section>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          Ages <span className="font-normal normal-case text-muted/70">— your kids&rsquo; ages</span>
        </p>
        <MultiToggle<AgeBucket>
          active={f.ages}
          onToggle={(b) => toggleIn("ages", b)}
          options={AGE_BUCKETS.map((b) => ({ key: b, label: b }))}
        />
      </section>

      <section>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          Languages <span className="font-normal normal-case text-muted/70">— that you speak</span>
        </p>
        <MultiToggle<Language>
          active={f.languages}
          onToggle={(l) => toggleIn("languages", l)}
          options={LANGUAGES.map((l) => ({
            key: l,
            label: `${LANGUAGE_EMOJI[l]} ${LANGUAGE_LABELS[l]}`,
          }))}
        />
      </section>

      <section className="flex flex-wrap gap-1.5">
        {f.tab === "weekend" && (
          <button
            onClick={() => onChange({ hideClasses: !f.hideClasses })}
            className={`chip ${f.hideClasses ? "chip--accent-on" : ""}`}
          >
            Hide weekly classes
          </button>
        )}
        {f.tab !== "weekend" && (
          <button
            onClick={() => onChange({ indoorOnly: !f.indoorOnly })}
            className={`chip ${f.indoorOnly ? "chip--accent-on" : ""}`}
          >
            Indoor only
          </button>
        )}
        <button
          onClick={() => onChange({ specialOnly: !f.specialOnly })}
          className={`chip ${f.specialOnly ? "chip--accent-on" : ""}`}
        >
          Special events only
        </button>
      </section>

      {f.tab === "weekend" && dataset.categories.length > 0 && (
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Type</p>
          <div className="flex flex-wrap gap-1.5">
            {dataset.categories.map((c) => (
              <button
                key={c.key}
                onClick={() => toggleIn("categories", c.key)}
                className={`chip ${f.categories.includes(c.key) ? "chip--on" : ""}`}
              >
                {CATEGORY_LABELS[c.key]} <span className="opacity-50">{c.count}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {f.tab === "places" && (dataset.place_kinds?.length ?? 0) > 0 && (
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Kind</p>
          <div className="flex flex-wrap gap-1.5">
            {dataset.place_kinds!
              .filter((k) => k.key !== "zomerbar" && k.key !== "playground_restaurant")
              .map((k) => (
                <button
                  key={k.key}
                  onClick={() => toggleIn("placeKinds", k.key)}
                  className={`chip ${f.placeKinds.includes(k.key) ? "chip--on" : ""}`}
                >
                  {PLACE_KIND_EMOJI[k.key]} {PLACE_KIND_LABELS[k.key]}{" "}
                  <span className="opacity-50">{k.count}</span>
                </button>
              ))}
          </div>
        </section>
      )}

      {dataset.feature_tags.filter((t) => FEATURE_LABELS[t.key]).length > 0 && (
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">What's there</p>
          <div className="flex flex-wrap gap-1.5">
            {dataset.feature_tags
              .filter((t) => FEATURE_LABELS[t.key])
              .map((t) => (
                <button
                  key={t.key}
                  onClick={() => toggleIn("features", t.key)}
                  className={`chip ${f.features.includes(t.key) ? "chip--on" : ""}`}
                >
                  {FEATURE_EMOJI[t.key] ?? ""} {FEATURE_LABELS[t.key]}
                </button>
              ))}
          </div>
        </section>
      )}

      <div className="flex items-center justify-between border-t border-line pt-3 text-sm text-muted">
        <span>
          <strong className="text-ink">{resultCount}</strong> match{resultCount === 1 ? "" : "es"}
        </span>
        {dirty && (
          <button onClick={onReset} className="font-medium text-tangerine hover:text-tangerine-dark">
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
