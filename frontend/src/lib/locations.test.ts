import { describe, expect, it } from "vitest";
import {
  DEFAULT_ORIGIN,
  HOME_LOCATIONS,
  customOrigin,
  haversineKm,
  isCustomOrigin,
  parseOrigin,
  withDistance,
} from "./locations";
import type { Activity } from "../types";

const LEUVEN: [number, number] = [50.8798, 4.7005];
const BRUSSELS: [number, number] = [50.8467, 4.3525];
const ANTWERP: [number, number] = [51.2194, 4.4025];

describe("haversineKm", () => {
  it("is zero for the same point", () => {
    expect(haversineKm(...LEUVEN, ...LEUVEN)).toBe(0);
  });

  // Values produced by automation/geo.py's haversine_km for the same inputs.
  // The frontend recomputes distance for whichever origin the user picks while
  // the shipped distance_km comes from Python, so any drift between the two
  // would make a place jump when you change origin and back.
  it.each([
    ["Brussels", BRUSSELS, 24.7],
    ["Antwerp", ANTWERP, 43.1],
    ["Liege", [50.6326, 5.5797] as [number, number], 67.7],
  ])("matches automation/geo.py for %s", (_name, dest, expected) => {
    expect(haversineKm(...LEUVEN, ...(dest as [number, number]))).toBe(expected);
  });

  it("is symmetric", () => {
    expect(haversineKm(...LEUVEN, ...ANTWERP)).toBeCloseTo(
      haversineKm(...ANTWERP, ...LEUVEN), 6);
  });

  it("rounds to one decimal place", () => {
    const d = haversineKm(...LEUVEN, ...BRUSSELS);
    expect(d).toBe(Math.round(d * 10) / 10);
  });
});

describe("presets", () => {
  it("defaults to Leuven", () => {
    expect(DEFAULT_ORIGIN.key).toBe(HOME_LOCATIONS[0].key);
    expect(DEFAULT_ORIGIN.label.toLowerCase()).toContain("leuven");
  });

  it("has unique keys", () => {
    const keys = HOME_LOCATIONS.map((o) => o.key);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("gives every preset real Belgian coordinates", () => {
    for (const o of HOME_LOCATIONS) {
      expect(o.lat).toBeGreaterThan(49.4);
      expect(o.lat).toBeLessThan(51.6);
      expect(o.lng).toBeGreaterThan(2.5);
      expect(o.lng).toBeLessThan(6.5);
      expect(o.label.trim()).not.toBe("");
    }
  });
});

describe("parseOrigin", () => {
  it("resolves a known preset key", () => {
    expect(parseOrigin(HOME_LOCATIONS[1].key).key).toBe(HOME_LOCATIONS[1].key);
  });

  it("falls back to the default for junk", () => {
    expect(parseOrigin("not-a-place").key).toBe(DEFAULT_ORIGIN.key);
    expect(parseOrigin(null).key).toBe(DEFAULT_ORIGIN.key);
    expect(parseOrigin(undefined).key).toBe(DEFAULT_ORIGIN.key);
    expect(parseOrigin("").key).toBe(DEFAULT_ORIGIN.key);
  });

  it("round-trips a geolocation origin through the URL form", () => {
    const custom = customOrigin(50.9, 4.6);
    const parsed = parseOrigin(custom.key);
    expect(parsed.lat).toBeCloseTo(50.9, 4);
    expect(parsed.lng).toBeCloseTo(4.6, 4);
    expect(isCustomOrigin(parsed)).toBe(true);
  });

  it("rejects a malformed coordinate pair", () => {
    expect(parseOrigin("1,2,3").key).toBe(DEFAULT_ORIGIN.key);
    expect(parseOrigin("abc,def").key).toBe(DEFAULT_ORIGIN.key);
  });
});

describe("isCustomOrigin", () => {
  it("is false for presets", () => {
    for (const o of HOME_LOCATIONS) expect(isCustomOrigin(o)).toBe(false);
  });

  it("is true for a geolocated point", () => {
    expect(isCustomOrigin(customOrigin(50.9, 4.6))).toBe(true);
  });
});

describe("withDistance", () => {
  const activity = (over: Partial<Activity> = {}) =>
    ({ id: "a", lat: 50.8467, lng: 4.3525, distance_km: 999, ...over }) as Activity;

  it("recomputes distance from the chosen origin", () => {
    const [a] = withDistance([activity()], DEFAULT_ORIGIN);
    expect(a.distance_km).toBeCloseTo(25.1, 0);
  });

  it("leaves an ungeocoded activity null rather than guessing", () => {
    const [a] = withDistance([activity({ lat: null, lng: null })], DEFAULT_ORIGIN);
    expect(a.distance_km).toBeNull();
  });

  it("changes with the origin", () => {
    const brusselsPreset = HOME_LOCATIONS.find((o) => /brussel/i.test(o.label));
    if (!brusselsPreset) return;
    const fromLeuven = withDistance([activity()], DEFAULT_ORIGIN)[0].distance_km!;
    const fromBrussels = withDistance([activity()], brusselsPreset)[0].distance_km!;
    expect(fromBrussels).toBeLessThan(fromLeuven);
  });

  it("does not mutate the input", () => {
    const input = [activity()];
    withDistance(input, DEFAULT_ORIGIN);
    expect(input[0].distance_km).toBe(999);
  });
});
