import { useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import type { Dataset, VenueSetting } from "../types";
import {
  CATEGORY_LABELS,
  FEATURE_EMOJI,
  FEATURE_LABELS,
  LANGUAGE_EMOJI,
  LANGUAGE_LABELS,
  PLACE_KIND_EMOJI,
  PLACE_KIND_LABELS,
  VENUE_EMOJI,
  VENUE_LABELS,
} from "../lib/labels";
import {
  AGE_BUCKETS,
  LANGUAGES,
  VENUE_SETTINGS,
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

/** Section heading styled like the old `<p>` label, but a real heading so the
 * section (and any radiogroup/group inside it) has an accessible name.
 * index.css applies font-display to h1-h3, so font-sans keeps this looking
 * like the small uppercase label it always was. */
function SectionLabel({ id, children }: { id: string; children: ReactNode }) {
  return (
    <h2 id={id} className="mb-2 font-sans text-xs font-semibold uppercase tracking-wide text-muted">
      {children}
    </h2>
  );
}

/** Single-select chip row — a standard ARIA radiogroup: one `role="radio"` per
 * chip, roving tabindex (only the checked chip is tab-stoppable), and
 * Left/Right/Up/Down arrow keys move + select. */
function Toggle<T extends string>({
  options,
  active,
  onPick,
  labelledBy,
}: {
  options: { key: T; label: string }[];
  active: T;
  onPick: (v: T) => void;
  labelledBy: string;
}) {
  const refs = useRef<Partial<Record<T, HTMLButtonElement | null>>>({});

  const pickAndFocus = (key: T) => {
    onPick(key);
    refs.current[key]?.focus();
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLButtonElement>, index: number) => {
    let dir = 0;
    if (e.key === "ArrowRight" || e.key === "ArrowDown") dir = 1;
    else if (e.key === "ArrowLeft" || e.key === "ArrowUp") dir = -1;
    else return;
    e.preventDefault();
    const next = options[(index + dir + options.length) % options.length];
    pickAndFocus(next.key);
  };

  return (
    <div role="radiogroup" aria-labelledby={labelledBy} className="flex flex-wrap gap-1.5">
      {options.map((o, i) => (
        <button
          key={o.key}
          ref={(el) => {
            refs.current[o.key] = el;
          }}
          type="button"
          role="radio"
          aria-checked={active === o.key}
          tabIndex={active === o.key ? 0 : -1}
          onClick={() => onPick(o.key)}
          onKeyDown={(e) => handleKeyDown(e, i)}
          className={`chip ${active === o.key ? "chip--on" : ""}`}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/** Same chip row, but any number can be on at once — an ARIA group of
 * toggle buttons (`aria-pressed`), not a radiogroup. */
function MultiToggle<T extends string>({
  options,
  active,
  onToggle,
  labelledBy,
}: {
  options: { key: T; label: string; count?: number }[];
  active: readonly T[];
  onToggle: (v: T) => void;
  labelledBy: string;
}) {
  return (
    <div role="group" aria-labelledby={labelledBy} className="flex flex-wrap gap-1.5">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onToggle(o.key)}
          aria-pressed={active.includes(o.key)}
          className={`chip ${active.includes(o.key) ? "chip--on" : ""}`}
        >
          {o.label}
          {o.count != null && <span className="ml-1 opacity-80">{o.count}</span>}
        </button>
      ))}
    </div>
  );
}

/** A single boolean chip ("Hide weekly classes", "Special events only") with
 * proper pressed-state semantics for a screen reader. */
function BoolChip({
  pressed,
  onClick,
  children,
}: {
  pressed: boolean;
  onClick: () => void;
  children: ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={pressed}
      className={`chip ${pressed ? "chip--accent-on" : ""}`}
    >
      {children}
    </button>
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

  const whenId = useId();
  const startingFromId = useId();
  const distanceId = useId();
  const priceId = useId();
  const sortId = useId();
  const agesId = useId();
  const languagesId = useId();
  const venueId = useId();
  const typeId = useId();
  const kindId = useId();
  const featuresId = useId();

  // compared field-by-field against the effective defaults, so restored
  // preferences don't permanently light up "Clear filters", and an
  // as-yet-unknown extra key on FilterState is handled generically rather
  // than requiring this comparison to be updated for every new field.
  const dirty = (Object.keys(f) as (keyof FilterState)[]).some((key) => {
    if (key === "tab") return false;
    const a = f[key];
    const b = baseline[key];
    if (Array.isArray(a) && Array.isArray(b)) {
      return a.length !== b.length || a.some((v, i) => v !== b[i]);
    }
    return a !== b;
  });

  // A fixed reference order per key, so a selection serialises identically no
  // matter what order the chips were clicked in — clicking "8" then "4" must
  // produce the same array (and URL/JSON) as "4" then "8".
  const canonicalOrder = useMemo<
    Record<"categories" | "features" | "placeKinds" | "ages" | "languages" | "venue", readonly string[]>
  >(
    () => ({
      ages: AGE_BUCKETS,
      languages: LANGUAGES,
      venue: VENUE_SETTINGS,
      categories: dataset.categories.map((c) => c.key),
      placeKinds: (dataset.place_kinds ?? []).map((k) => k.key),
      features: dataset.feature_tags.map((t) => t.key),
    }),
    [dataset.categories, dataset.place_kinds, dataset.feature_tags]
  );

  const toggleIn = <K extends "categories" | "features" | "placeKinds" | "ages" | "languages" | "venue">(
    key: K,
    val: FilterState[K][number]
  ) => {
    const set = new Set(f[key] as string[]);
    if (set.has(val as string)) {
      set.delete(val as string);
    } else {
      set.add(val as string);
    }
    const order = canonicalOrder[key];
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
        className="w-full rounded-xl2 border border-line bg-white px-4 py-3 text-base shadow-card outline-hidden placeholder:text-muted focus:border-tangerine"
      />

      {f.tab === "weekend" && (
        <section aria-labelledby={whenId}>
          <SectionLabel id={whenId}>When</SectionLabel>
          <Toggle<WhenFilter>
            active={f.when}
            labelledBy={whenId}
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

      <section aria-labelledby={startingFromId}>
        <SectionLabel id={startingFromId}>Starting from</SectionLabel>
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
            className="flex h-11 w-11 shrink-0 items-center justify-center rounded-xl2 border border-line bg-white text-sm hover:border-tangerine"
          >
            <span aria-hidden="true">📍</span>
          </button>
        </div>
        {geoError && <p className="mb-2 text-xs text-berry">{geoError}</p>}

        <SectionLabel id={distanceId}>
          Distance — within {f.maxDistance} km of {origin.label}
        </SectionLabel>
        <input
          type="range"
          min={5}
          max={200}
          step={5}
          value={f.maxDistance}
          onChange={(e) => onChange({ maxDistance: Number(e.target.value) })}
          aria-label="Maximum distance"
          aria-valuetext={`${f.maxDistance} km`}
          className="w-full accent-tangerine"
        />
      </section>

      <section className="flex flex-wrap gap-x-8 gap-y-4">
        <div>
          <SectionLabel id={priceId}>Price</SectionLabel>
          <Toggle<PriceFilter>
            active={f.price}
            labelledBy={priceId}
            onPick={(price) => onChange({ price })}
            options={[
              { key: "any", label: "Any" },
              { key: "free", label: "Free" },
              { key: "cheap", label: "≤ €10" },
            ]}
          />
        </div>
        <div>
          <SectionLabel id={sortId}>Sort</SectionLabel>
          <Toggle<SortKey>
            active={f.sort}
            labelledBy={sortId}
            onPick={(sort) => onChange({ sort })}
            options={[
              { key: "date", label: "Date" },
              { key: "distance", label: "Distance" },
              { key: "price", label: "Price" },
            ]}
          />
        </div>
      </section>

      <section aria-labelledby={agesId}>
        <SectionLabel id={agesId}>
          Ages <span className="font-normal normal-case text-muted">— your kids&rsquo; ages</span>
        </SectionLabel>
        <MultiToggle<AgeBucket>
          active={f.ages}
          labelledBy={agesId}
          onToggle={(b) => toggleIn("ages", b)}
          options={AGE_BUCKETS.map((b) => ({ key: b, label: b }))}
        />
      </section>

      <section aria-labelledby={languagesId}>
        <SectionLabel id={languagesId}>
          Languages <span className="font-normal normal-case text-muted">— that you speak</span>
        </SectionLabel>
        <MultiToggle<Language>
          active={f.languages}
          labelledBy={languagesId}
          onToggle={(l) => toggleIn("languages", l)}
          options={LANGUAGES.map((l) => ({
            key: l,
            label: `${LANGUAGE_EMOJI[l]} ${LANGUAGE_LABELS[l]}`,
          }))}
        />
      </section>

      <section aria-labelledby={venueId}>
        <SectionLabel id={venueId}>
          Indoor / outdoor{" "}
          <span className="font-normal normal-case text-muted">
            &mdash; for a rainy day; places that are both always show
          </span>
        </SectionLabel>
        <MultiToggle<VenueSetting>
          active={f.venue}
          labelledBy={venueId}
          onToggle={(v) => toggleIn("venue", v)}
          options={VENUE_SETTINGS.map((v) => ({
            key: v,
            label: `${VENUE_EMOJI[v]} ${VENUE_LABELS[v]}`,
          }))}
        />
      </section>

      <section className="flex flex-wrap gap-1.5">
        {f.tab === "weekend" && (
          <BoolChip pressed={f.hideClasses} onClick={() => onChange({ hideClasses: !f.hideClasses })}>
            Hide weekly classes
          </BoolChip>
        )}
        <BoolChip pressed={f.specialOnly} onClick={() => onChange({ specialOnly: !f.specialOnly })}>
          Special events only
        </BoolChip>
      </section>

      {f.tab === "weekend" && dataset.categories.length > 0 && (
        <section aria-labelledby={typeId}>
          <SectionLabel id={typeId}>Type</SectionLabel>
          <MultiToggle
            active={f.categories}
            labelledBy={typeId}
            onToggle={(k) => toggleIn("categories", k)}
            options={dataset.categories.map((c) => ({
              key: c.key,
              label: CATEGORY_LABELS[c.key],
              count: c.count,
            }))}
          />
        </section>
      )}

      {f.tab === "places" && (dataset.place_kinds?.length ?? 0) > 0 && (
        <section aria-labelledby={kindId}>
          <SectionLabel id={kindId}>Kind</SectionLabel>
          <MultiToggle
            active={f.placeKinds}
            labelledBy={kindId}
            onToggle={(k) => toggleIn("placeKinds", k)}
            options={dataset
              .place_kinds!.filter((k) => k.key !== "zomerbar" && k.key !== "playground_restaurant")
              .map((k) => ({
                key: k.key,
                label: `${PLACE_KIND_EMOJI[k.key]} ${PLACE_KIND_LABELS[k.key]}`,
                count: k.count,
              }))}
          />
        </section>
      )}

      {dataset.feature_tags.filter((t) => FEATURE_LABELS[t.key]).length > 0 && (
        <section aria-labelledby={featuresId}>
          <SectionLabel id={featuresId}>What's there</SectionLabel>
          <MultiToggle
            active={f.features}
            labelledBy={featuresId}
            onToggle={(k) => toggleIn("features", k)}
            options={dataset.feature_tags
              .filter((t) => FEATURE_LABELS[t.key])
              .map((t) => ({
                key: t.key,
                label: `${FEATURE_EMOJI[t.key] ?? ""} ${FEATURE_LABELS[t.key]}`,
              }))}
          />
        </section>
      )}

      <div className="flex items-center justify-between border-t border-line pt-3 text-sm text-muted">
        <span>
          <strong className="text-ink">{resultCount}</strong> match{resultCount === 1 ? "" : "es"}
        </span>
        {dirty && (
          <button
            onClick={onReset}
            className="inline-flex min-h-[44px] items-center px-2 font-medium text-tangerine-deep hover:text-tangerine-dark"
          >
            Clear filters
          </button>
        )}
      </div>
    </div>
  );
}
