import { useEffect, useMemo, useState } from "react";
import type { Dataset } from "./types";
import { loadDataset } from "./lib/data";
import {
  DEFAULT_FILTERS,
  applyFilters,
  filtersToParams,
  paramsToFilters,
  type FilterState,
  type Tab,
} from "./lib/filters";
import ActivityCard from "./components/ActivityCard";
import FilterBar from "./components/FilterBar";

const SAVED_KEY = "weekwnd.saved.v1";

function loadSaved(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(SAVED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

export default function App() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState<FilterState>(() => paramsToFilters(location.search));
  const [saved, setSaved] = useState<Set<string>>(loadSaved);
  const [showFilters, setShowFilters] = useState(false);
  const [onlySaved, setOnlySaved] = useState(false);

  useEffect(() => {
    loadDataset().then(setDataset).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => {
    const qs = filtersToParams(filters);
    history.replaceState(null, "", `${location.pathname}${qs}`);
  }, [filters]);

  useEffect(() => {
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
    } catch {
      /* private mode — ignore */
    }
  }, [saved]);

  const patch = (p: Partial<FilterState>) => setFilters((f) => ({ ...f, ...p }));
  const setTab = (tab: Tab) => setFilters((f) => ({ ...f, tab }));
  const toggleSave = (id: string) =>
    setSaved((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  const results = useMemo(() => {
    if (!dataset) return [];
    const r = applyFilters(dataset.activities, filters);
    return onlySaved ? r.filter((a) => saved.has(a.id)) : r;
  }, [dataset, filters, onlySaved, saved]);

  if (error) {
    return (
      <div className="mx-auto max-w-md p-8 text-center">
        <h1 className="mb-2 text-2xl">Couldn't load activities</h1>
        <p className="text-muted">{error}</p>
      </div>
    );
  }

  const generated = dataset
    ? new Date(dataset.generated_at).toLocaleDateString("en-GB", { day: "numeric", month: "long" })
    : "";

  return (
    <div className="min-h-screen">
      <header className="border-b border-line bg-paper/85 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-baseline justify-between gap-4">
            <h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
              What2do <span className="text-tangerine">Weekend</span>
            </h1>
            {dataset && (
              <span className="text-xs text-muted">
                Updated {generated}
                {dataset.degraded && " · limited data"}
              </span>
            )}
          </div>
          <p className="max-w-prose text-sm text-muted">
            Family activities in and around Leuven — for a 4- and an 8-year-old. Dutch text, tap
            <em> Translate</em> for English.
          </p>

          <nav className="flex gap-1">
            {(["weekend", "places"] as Tab[]).map((t) => (
              <button
                key={t}
                onClick={() => setTab(t)}
                className={`rounded-full px-4 py-2 text-sm font-semibold transition ${
                  filters.tab === t ? "bg-ink text-paper" : "text-muted hover:text-ink"
                }`}
              >
                {t === "weekend" ? "This weekend & beyond" : "Places to go"}
              </button>
            ))}
            <button
              onClick={() => setOnlySaved((v) => !v)}
              className={`ml-auto rounded-full px-4 py-2 text-sm font-semibold transition ${
                onlySaved ? "bg-tangerine text-white" : "text-muted hover:text-ink"
              }`}
            >
              ★ Saved {saved.size ? `(${saved.size})` : ""}
            </button>
          </nav>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[300px_1fr]">
        <aside className="lg:sticky lg:top-6 lg:h-fit">
          <button
            onClick={() => setShowFilters((v) => !v)}
            className="mb-3 w-full rounded-xl2 border border-line bg-white px-4 py-3 text-left text-sm font-semibold shadow-card lg:hidden"
          >
            {showFilters ? "Hide filters" : "Show filters"} · {results.length} matches
          </button>
          <div className={`${showFilters ? "block" : "hidden"} lg:block`}>
            {dataset && (
              <FilterBar
                filters={filters}
                dataset={dataset}
                resultCount={results.length}
                onChange={patch}
                onReset={() => setFilters((f) => ({ ...DEFAULT_FILTERS, tab: f.tab }))}
              />
            )}
          </div>
        </aside>

        <section>
          {!dataset ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-72 animate-pulse rounded-xl2 border border-line bg-white/60" />
              ))}
            </div>
          ) : results.length === 0 ? (
            <div className="rounded-xl2 border border-dashed border-line bg-white/60 p-10 text-center">
              <p className="text-lg font-semibold">Nothing matches yet</p>
              <p className="mt-1 text-sm text-muted">
                Try widening the distance or clearing a filter.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {results.map((a) => (
                <ActivityCard key={a.id} activity={a} saved={saved.has(a.id)} onToggleSave={toggleSave} />
              ))}
            </div>
          )}
        </section>
      </main>

      <footer className="mx-auto max-w-6xl px-4 py-8 text-xs text-muted sm:px-6">
        {dataset && (
          <p>
            Sources this run: {dataset.sources_fetched.join(", ") || "none"}
            {dataset.sources_failed.length > 0 && ` · failed: ${dataset.sources_failed.join(", ")}`}
          </p>
        )}
      </footer>
    </div>
  );
}
