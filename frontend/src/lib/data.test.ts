import { describe, expect, it } from "vitest";
import { googleMapsUrl } from "./data";
import type { Activity } from "../types";

// Only the location fields matter here; the helper must not depend on anything else.
function loc(over: Partial<Activity>): Activity {
  return {
    venue_name: null,
    address: null,
    city: null,
    lat: null,
    lng: null,
    ...over,
  } as Activity;
}

describe("googleMapsUrl", () => {
  it("searches by venue name + address so Maps opens the venue's own listing, not a bare pin", () => {
    const url = googleMapsUrl(
      loc({ venue_name: "noordouest café", address: "Verhoevenstraat, 1020, Laeken", lat: 50.87, lng: 4.34 }),
    );
    expect(url).toBe(
      "https://www.google.com/maps/search/?api=1&query=" +
        encodeURIComponent("noordouest café, Verhoevenstraat, 1020, Laeken"),
    );
  });

  it("falls back to the city when there is no street address", () => {
    const url = googleMapsUrl(loc({ venue_name: "Speelbos", city: "Heverlee", lat: 50.86, lng: 4.7 }));
    expect(url).toContain(encodeURIComponent("Speelbos, Heverlee"));
  });

  it("does not repeat the city when the address already ends with it", () => {
    const url = googleMapsUrl(loc({ venue_name: "X", address: "Verhoevenstraat, 1020, Laeken", city: "Laeken" }));
    expect(decodeURIComponent(url ?? "")).not.toMatch(/Laeken.*Laeken/);
  });

  it("falls back to coordinates when nothing textual is known — a pin beats no link", () => {
    expect(googleMapsUrl(loc({ lat: 50.8711968, lng: 4.3424362 }))).toBe(
      "https://www.google.com/maps/search/?api=1&query=50.8711968%2C4.3424362",
    );
  });

  it("returns null when the activity has nothing locatable, so the card shows plain text", () => {
    expect(googleMapsUrl(loc({}))).toBeNull();
    expect(googleMapsUrl(loc({ venue_name: "  " }))).toBeNull();
  });

  it("prefers text over coordinates even when both exist", () => {
    const url = googleMapsUrl(loc({ venue_name: "Technopolis", city: "Mechelen", lat: 51.0, lng: 4.5 }));
    expect(url).toContain("Technopolis");
    expect(url).not.toContain("51");
  });
});
