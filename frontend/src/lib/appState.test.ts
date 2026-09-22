import { describe, expect, it } from "vitest";
import { activeFilterCount, isActivitySaved, isTabOnlyChange } from "./appState";
import { DEFAULT_FILTERS, type FilterState } from "./filters";
import type { Activity } from "../types";

describe("activeFilterCount", () => {
  it("is zero when filters equal the baseline", () => {
    expect(activeFilterCount(DEFAULT_FILTERS, DEFAULT_FILTERS)).toBe(0);
  });

  it("ignores the tab field", () => {
    const f: FilterState = { ...DEFAULT_FILTERS, tab: "places" };
    expect(activeFilterCount(f, DEFAULT_FILTERS)).toBe(0);
  });

  it("counts a scalar field that diverges", () => {
    const f: FilterState = { ...DEFAULT_FILTERS, price: "free" };
    expect(activeFilterCount(f, DEFAULT_FILTERS)).toBe(1);
  });

  it("counts an array field that diverges by length", () => {
    const f: FilterState = { ...DEFAULT_FILTERS, ages: ["4", "8"] };
    expect(activeFilterCount(f, DEFAULT_FILTERS)).toBe(1);
  });

  it("counts an array field that diverges by content, same length", () => {
    const baseline: FilterState = { ...DEFAULT_FILTERS, ages: ["4"] };
    const f: FilterState = { ...DEFAULT_FILTERS, ages: ["8"] };
    expect(activeFilterCount(f, baseline)).toBe(1);
  });

  it("counts several diverging fields independently", () => {
    const f: FilterState = { ...DEFAULT_FILTERS, price: "free", hideClasses: false, onlySaved: true };
    expect(activeFilterCount(f, DEFAULT_FILTERS)).toBe(3);
  });
});

describe("isTabOnlyChange", () => {
  it("is true when only the tab param differs", () => {
    expect(isTabOnlyChange("?tab=weekend&q=zoo", "?tab=places&q=zoo")).toBe(true);
  });

  it("is false when another param also differs", () => {
    expect(isTabOnlyChange("?tab=weekend&q=zoo", "?tab=places&q=park")).toBe(false);
  });

  it("is false when the tab did not actually change", () => {
    expect(isTabOnlyChange("?tab=weekend", "?tab=weekend")).toBe(false);
  });

  it("is true switching from no tab param (default) to an explicit one", () => {
    expect(isTabOnlyChange("", "?tab=places")).toBe(true);
  });

  it("is false when a param is added alongside a tab change", () => {
    expect(isTabOnlyChange("?tab=weekend", "?tab=places&saved=1")).toBe(false);
  });
});

describe("isActivitySaved", () => {
  function act(over: Partial<Activity> = {}): Activity {
    return { id: "a1", ...over } as Activity;
  }

  it("checks the plain id when there is no series", () => {
    expect(isActivitySaved(act({ id: "a1" }), new Set(["a1"]))).toBe(true);
    expect(isActivitySaved(act({ id: "a1" }), new Set(["a2"]))).toBe(false);
  });

  it("is saved if any series member id is saved, even if the representative is not", () => {
    const a = act({ id: "rep", series_ids: ["rep", "m2", "m3"] });
    expect(isActivitySaved(a, new Set(["m3"]))).toBe(true);
  });

  it("is not saved when no series member id is in the set", () => {
    const a = act({ id: "rep", series_ids: ["rep", "m2", "m3"] });
    expect(isActivitySaved(a, new Set(["other"]))).toBe(false);
  });
});
