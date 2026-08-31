import type { Dataset } from "../types";
import { CATEGORY_LABELS, FEATURE_EMOJI, FEATURE_LABELS } from "../lib/labels";
import {
  DEFAULT_FILTERS,
  type AgeFilter,
  type FilterState,
  type PriceFilter,
  type SortKey,
  type WhenFilter,
} from "../lib/filters";

interface Props {
  filters: FilterState;
  dataset: Dataset;
  resultCount: number;
  onChange: (patch: Partial<FilterState>) => void;
  onReset: () => void;
}

function Toggle<T extends string>({
  value,
  options,
  active,
  onPick,
}: {
  value: T;
  options: { key: T; label: string }[];
  active: T;
  onPick: (v: T) => void;
}) {
  void value;
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

export default function FilterBar({ filters: f, dataset, resultCount, onChange, onReset }: Props) {
  const dirty = JSON.stringify({ ...f, tab: "x" }) !== JSON.stringify({ ...DEFAULT_FILTERS, tab: "x" });

  const toggleIn = <K extends "categories" | "features">(key: K, val: FilterState[K][number]) => {
    const set = new Set(f[key] as string[]);
    set.has(val as string) ? set.delete(val as string) : set.add(val as string);
    onChange({ [key]: [...set] } as unknown as Partial<FilterState>);
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
            value={f.when}
            active={f.when}
            onPick={(when) => onChange({ when })}
            options={[
              { key: "any", label: "Anytime" },
              { key: "this_weekend", label: "This weekend" },
              { key: "next_weekend", label: "Next weekend" },
              { key: "school_holiday", label: "School holidays" },
            ]}
          />
        </section>
      )}

      <section>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">
          Distance — within {f.maxDistance} km of Leuven
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
            value={f.price}
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
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Good for</p>
          <Toggle<AgeFilter>
            value={f.age}
            active={f.age}
            onPick={(age) => onChange({ age })}
            options={[
              { key: "any", label: "Either kid" },
              { key: "4yo", label: "The 4yo" },
              { key: "8yo", label: "The 8yo" },
              { key: "both", label: "Both" },
            ]}
          />
        </div>
        <div>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">Sort</p>
          <Toggle<SortKey>
            value={f.sort}
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

      <section className="flex flex-wrap gap-1.5">
        <button
          onClick={() => onChange({ hideFrench: !f.hideFrench })}
          className={`chip ${f.hideFrench ? "chip--accent-on" : ""}`}
        >
          Hide French-only
        </button>
        <button
          onClick={() => onChange({ specialOnly: !f.specialOnly })}
          className={`chip ${f.specialOnly ? "chip--accent-on" : ""}`}
        >
          Special events only
        </button>
      </section>

      {dataset.categories.length > 0 && (
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

      {dataset.feature_tags.length > 0 && (
        <section>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted">What's there</p>
          <div className="flex flex-wrap gap-1.5">
            {dataset.feature_tags.map((t) => (
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
