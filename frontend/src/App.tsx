import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import type { Dataset } from "./types";
import { loadDataset } from "./lib/data";
import {
  DEFAULT_FILTERS,
  applyFilters,
  filtersToParams,
  paramsToFilters,
  type FilterState,
  type SavedPrefs,
  type Tab,
} from "./lib/filters";
import { activeFilterCount, isActivitySaved, isTabOnlyChange } from "./lib/appState";
import { groupSeries } from "./lib/series";
import { parseOrigin, withDistance } from "./lib/locations";
import { belgiumToday, withBuckets } from "./lib/buckets";
import ActivityCard from "./components/ActivityCard";
import FilterBar from "./components/FilterBar";

const SAVED_KEY = "weekwnd.saved.v1";
const PREFS_KEY = "weekwnd.prefs.v1";
const PAGE_SIZE = 24;
const RESULTS_ID = "results";
const FILTER_PANEL_ID = "filter-panel";

const TAB_DEFS: [Tab, string][] = [
  ["weekend", "This weekend & beyond"],
  ["places", "Places to go"],
  ["zomerbar", "Zomerbars"],
  ["eatplay", "Eat & play"],
];
const tabId = (t: Tab) => `tab-${t}`;

function loadSaved(): Set<string> {
  try {
    return new Set(JSON.parse(localStorage.getItem(SAVED_KEY) || "[]"));
  } catch {
    return new Set();
  }
}

/** Where you are, how old your kids are and what you speak don't change between
 *  visits, so they're remembered. A URL param still overrides them. */
function loadPrefs(): SavedPrefs {
  try {
    return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") as SavedPrefs;
  } catch {
    return {};
  }
}

export default function App() {
  const [dataset, setDataset] = useState<Dataset | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Read once — the FilterState initializer below reuses this same value
  // rather than calling loadPrefs() a second time.
  const [prefs] = useState<SavedPrefs>(loadPrefs);
  const [filters, setFilters] = useState<FilterState>(() => paramsToFilters(location.search, prefs));
  const [saved, setSaved] = useState<Set<string>>(loadSaved);
  const [showFilters, setShowFilters] = useState(false);
  const [visibleCount, setVisibleCount] = useState(PAGE_SIZE);

  const load = () => {
    setError(null);
    loadDataset()
      .then(setDataset)
      .catch((e) => setError(String(e)));
  };
  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // The weekend/holiday buckets are derived against "today" (Brussels). Keep a
  // day-granular clock so a PWA left open across midnight — or reopened days
  // later — re-buckets on its own, without waiting for another state change.
  const [todayKey, setTodayKey] = useState(() => belgiumToday().join("-"));
  useEffect(() => {
    const tick = () => setTodayKey(belgiumToday().join("-"));
    const id = window.setInterval(tick, 10 * 60 * 1000);
    document.addEventListener("visibilitychange", tick);
    window.addEventListener("focus", tick);
    return () => {
      window.clearInterval(id);
      document.removeEventListener("visibilitychange", tick);
      window.removeEventListener("focus", tick);
    };
  }, []);

  // Back/forward: re-derive filter state from the URL the browser just
  // restored. suppressNextSync stops the sync effect below from immediately
  // re-pushing/replacing on top of the entry we just navigated to.
  const suppressNextSync = useRef(false);
  useEffect(() => {
    const onPopState = () => {
      suppressNextSync.current = true;
      setFilters(paramsToFilters(location.search, prefs));
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (suppressNextSync.current) {
      suppressNextSync.current = false;
      return;
    }
    const qs = filtersToParams(filters);
    if (qs === location.search) return; // nothing to write, and nothing to push
    const url = `${location.pathname}${qs}`;
    if (isTabOnlyChange(location.search, qs)) {
      history.pushState(null, "", url);
    } else {
      history.replaceState(null, "", url);
    }
  }, [filters]);

  useEffect(() => {
    try {
      localStorage.setItem(SAVED_KEY, JSON.stringify([...saved]));
    } catch {
      /* private mode — ignore */
    }
  }, [saved]);

  // Skip the very first run: the initial filters value already reflects a URL
  // override (from a shared link) merged over saved prefs, and persisting that
  // on load would silently overwrite the visitor's own home/ages/languages
  // just for opening someone else's link. Only a later, real change re-fires.
  const skipFirstPrefsWrite = useRef(true);
  useEffect(() => {
    if (skipFirstPrefsWrite.current) {
      skipFirstPrefsWrite.current = false;
      return;
    }
    try {
      const { origin, ages, languages } = filters;
      localStorage.setItem(PREFS_KEY, JSON.stringify({ origin, ages, languages }));
    } catch {
      /* private mode — ignore */
    }
  }, [filters.origin, filters.ages, filters.languages]);

  const patch = (p: Partial<FilterState>) => setFilters((f) => ({ ...f, ...p }));
  const setTab = (tab: Tab) => setFilters((f) => ({ ...f, tab }));
  const toggleSave = (id: string) =>
    setSaved((s) => {
      const next = new Set(s);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // `onlySaved` is a view, not a preference — it never joins SavedPrefs/the
  // localStorage write above, so a shared "?saved=1" link never sticks.
  const baseline = useMemo<FilterState>(() => ({ ...DEFAULT_FILTERS, ...prefs }), [prefs]);
  const resetFilters = () => setFilters((f) => ({ ...baseline, tab: f.tab }));

  const origin = useMemo(() => parseOrigin(filters.origin), [filters.origin]);

  // distance_km ships measured from Leuven; re-derive it for the chosen origin.
  // weekend_bucket / holiday flags are baked at pipeline time (Mondays only), so
  // re-derive those against today too. Both keep every downstream consumer
  // reading activity.distance_km / activity.weekend_bucket unchanged.
  const located = useMemo(
    () =>
      dataset
        ? withBuckets(
            withDistance(dataset.activities, origin),
            // older cached payloads only carry the merged `school_holidays`
            // (== the NL calendar); fall back to it so the holiday filter still
            // works before the client picks up a fresh feed.
            dataset.school_holidays_nl ?? dataset.school_holidays,
            dataset.school_holidays_fr,
          )
        : [],
    // todayKey is the daily re-bucket trigger (see the clock effect above).
    [dataset, origin, todayKey]
  );

  // Filtering + sorting deliberately does NOT depend on `saved` (unless
  // onlySaved is active) — otherwise toggling one star would re-filter and
  // re-sort all ~900 rows on every click.
  const filteredSorted = useMemo(() => {
    if (!dataset) return [];
    return applyFilters(located, filters);
  }, [dataset, located, filters]);

  // onlySaved is applied to the ungrouped list FIRST, so a saved activity that
  // is merely one occurrence of a weekly series still surfaces even when it
  // isn't the occurrence groupSeries would otherwise pick as representative.
  // Series grouping runs after that (weekend tab only — it no-ops elsewhere
  // since permanent places are never grouped), and its output's length is the
  // count shown to the user.
  const results = useMemo(() => {
    if (!dataset) return [];
    const ungrouped = filters.onlySaved
      ? filteredSorted.filter((a) => saved.has(a.id))
      : filteredSorted;
    return filters.tab === "weekend" ? groupSeries(ungrouped, new Date()) : ungrouped;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dataset, filteredSorted, filters.onlySaved, filters.tab, saved, todayKey]);

  // Reset pagination whenever the result set itself changes identity (a new
  // filter/tab/origin/search produced a different array), not on every
  // render.
  useEffect(() => {
    setVisibleCount(PAGE_SIZE);
  }, [results]);

  const visible = results.slice(0, visibleCount);
  const remaining = results.length - visible.length;

  const sentinelRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = sentinelRef.current;
    if (!el || remaining <= 0) return;
    const obs = new IntersectionObserver(
      (entries) => {
        if (entries[0]?.isIntersecting) {
          setVisibleCount((v) => Math.min(v + PAGE_SIZE, results.length));
        }
      },
      { rootMargin: "600px" }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [remaining, results.length]);

  const activeCount = activeFilterCount(filters, baseline);

  const tabBtnRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const focusTab = (idx: number) => tabBtnRefs.current[idx]?.focus();
  const handleTabKeyDown = (e: KeyboardEvent<HTMLButtonElement>, idx: number) => {
    if (e.key === "Home") {
      e.preventDefault();
      setTab(TAB_DEFS[0][0]);
      focusTab(0);
      return;
    }
    if (e.key === "End") {
      e.preventDefault();
      const last = TAB_DEFS.length - 1;
      setTab(TAB_DEFS[last][0]);
      focusTab(last);
      return;
    }
    let dir = 0;
    if (e.key === "ArrowRight") dir = 1;
    else if (e.key === "ArrowLeft") dir = -1;
    else return;
    e.preventDefault();
    const next = (idx + dir + TAB_DEFS.length) % TAB_DEFS.length;
    setTab(TAB_DEFS[next][0]);
    focusTab(next);
  };

  const generated = dataset
    ? new Date(dataset.generated_at).toLocaleDateString("en-GB", { day: "numeric", month: "long" })
    : "";

  return (
    <div className="min-h-screen">
      <a href={`#${RESULTS_ID}`} className="skip-link">
        Skip to results
      </a>

      <header className="border-b border-line bg-paper/85 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl flex-col gap-3 px-4 py-4 sm:px-6">
          <div className="flex items-baseline justify-between gap-4">
            <h1 className="font-display text-2xl font-semibold tracking-tight sm:text-3xl">
              What2do <span className="text-tangerine-deep">Weekend</span>
            </h1>
            {dataset && (
              <span className="text-xs text-muted">
                Updated {generated}
                {dataset.degraded && " · limited data"}
              </span>
            )}
          </div>
          <p className="max-w-prose text-sm text-muted">
            Things to do with the kids, anywhere in Belgium. Listings keep their original Dutch
            or French — tap <em>Translate</em> for English.
          </p>

          <div role="tablist" aria-label="Sections" className="-mb-1 flex flex-wrap items-center gap-1 overflow-x-auto">
            {TAB_DEFS.map(([t, label], i) => {
              const active = filters.tab === t;
              return (
                <button
                  key={t}
                  ref={(el) => {
                    tabBtnRefs.current[i] = el;
                  }}
                  role="tab"
                  id={tabId(t)}
                  aria-selected={active}
                  aria-controls={RESULTS_ID}
                  tabIndex={active ? 0 : -1}
                  onClick={() => setTab(t)}
                  onKeyDown={(e) => handleTabKeyDown(e, i)}
                  className={`min-h-[44px] whitespace-nowrap rounded-full px-4 py-2 text-sm font-semibold transition ${
                    active ? "bg-ink text-paper" : "text-muted hover:text-ink"
                  }`}
                >
                  {label}
                </button>
              );
            })}
            <button
              onClick={() => patch({ onlySaved: !filters.onlySaved })}
              aria-pressed={filters.onlySaved}
              className={`ml-auto min-h-[44px] whitespace-nowrap rounded-full px-4 py-2 text-sm font-semibold transition ${
                filters.onlySaved ? "bg-tangerine-deep text-white" : "text-muted hover:text-ink"
              }`}
            >
              ★ Saved {saved.size ? `(${saved.size})` : ""}
            </button>
          </div>
        </div>
      </header>

      <main className="mx-auto grid max-w-6xl gap-6 px-4 py-6 sm:px-6 lg:grid-cols-[300px_1fr]">
        <aside className="lg:sticky lg:top-6 lg:h-fit">
          <button
            onClick={() => setShowFilters((v) => !v)}
            aria-expanded={showFilters}
            aria-controls={FILTER_PANEL_ID}
            className="mb-3 w-full rounded-xl2 border border-line bg-white px-4 py-3 text-left text-sm font-semibold shadow-card lg:hidden"
          >
            {showFilters ? "Hide filters" : "Show filters"} · {results.length} matches
            {activeCount > 0 && ` · ${activeCount} active`}
          </button>
          <div id={FILTER_PANEL_ID} className={`${showFilters ? "block" : "hidden"} lg:block`}>
            {dataset && (
              <FilterBar
                filters={filters}
                dataset={dataset}
                resultCount={results.length}
                onChange={patch}
                origin={origin}
                baseline={baseline}
                onReset={resetFilters}
              />
            )}
          </div>
        </aside>

        <section id={RESULTS_ID} tabIndex={-1} role="tabpanel" aria-labelledby={tabId(filters.tab)}>
          <h2 id="results-heading" className="sr-only">
            Results
          </h2>

          {error ? (
            <div
              role="alert"
              className="rounded-xl2 border border-dashed border-berry/40 bg-white/60 p-10 text-center"
            >
              <p className="text-lg font-semibold">Couldn&rsquo;t load this week&rsquo;s activities</p>
              <p className="mt-1 text-sm text-muted">Check your connection and try again.</p>
              <button
                onClick={load}
                className="mt-4 inline-flex min-h-[44px] items-center rounded-xl2 bg-ink px-5 py-2 text-sm font-semibold text-paper"
              >
                Retry
              </button>
              <details className="mx-auto mt-4 max-w-sm text-left text-xs text-muted">
                <summary className="cursor-pointer">Technical details</summary>
                <pre className="mt-2 whitespace-pre-wrap wrap-break-word">{error}</pre>
              </details>
            </div>
          ) : !dataset ? (
            <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="h-72 animate-pulse rounded-xl2 border border-line bg-white/60" />
              ))}
            </div>
          ) : results.length === 0 ? (
            <div className="rounded-xl2 border border-dashed border-line bg-white/60 p-10 text-center">
              {filters.onlySaved && saved.size === 0 ? (
                <>
                  <p className="text-lg font-semibold">Nothing saved yet</p>
                  <p className="mt-1 text-sm text-muted">Tap ☆ on a card to keep it here.</p>
                  <button
                    onClick={() => patch({ onlySaved: false })}
                    className="mt-4 inline-flex min-h-[44px] items-center rounded-xl2 bg-ink px-5 py-2 text-sm font-semibold text-paper"
                  >
                    Show everything
                  </button>
                </>
              ) : filters.onlySaved ? (
                <>
                  <p className="text-lg font-semibold">Your saved items don&rsquo;t match the current filters</p>
                  <p className="mt-1 text-sm text-muted">Try widening the distance or clearing a filter.</p>
                  <button
                    onClick={resetFilters}
                    className="mt-4 inline-flex min-h-[44px] items-center rounded-xl2 bg-ink px-5 py-2 text-sm font-semibold text-paper"
                  >
                    Clear filters
                  </button>
                </>
              ) : (
                <>
                  <p className="text-lg font-semibold">Nothing matches yet</p>
                  <p className="mt-1 text-sm text-muted">Try widening the distance or clearing a filter.</p>
                  <button
                    onClick={resetFilters}
                    className="mt-4 inline-flex min-h-[44px] items-center rounded-xl2 bg-ink px-5 py-2 text-sm font-semibold text-paper"
                  >
                    Clear filters
                  </button>
                </>
              )}
            </div>
          ) : (
            <>
              <p aria-live="polite" className="mb-3 text-sm text-muted">
                <strong className="text-ink">{results.length}</strong> match{results.length === 1 ? "" : "es"}
              </p>
              <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
                {visible.map((a) => (
                  <ActivityCard
                    key={a.id}
                    activity={a}
                    saved={isActivitySaved(a, saved)}
                    originLabel={origin.label}
                    onToggleSave={toggleSave}
                  />
                ))}
              </div>
              {remaining > 0 && (
                <>
                  <div ref={sentinelRef} aria-hidden className="h-px" />
                  <div className="mt-6 flex justify-center">
                    <button
                      onClick={() => setVisibleCount((v) => Math.min(v + PAGE_SIZE, results.length))}
                      className="min-h-[44px] rounded-xl2 border border-line bg-white px-5 py-2 text-sm font-semibold shadow-card hover:border-tangerine"
                    >
                      Show more ({remaining} remaining)
                    </button>
                  </div>
                </>
              )}
            </>
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
