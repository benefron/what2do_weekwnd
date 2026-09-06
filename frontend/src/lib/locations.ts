import type { Activity } from "../types";

/** A point to measure distance from. `key` doubles as the URL/localStorage value. */
export interface Origin {
  key: string;
  label: string;
  lat: number;
  lng: number;
}

/**
 * Preset home points. Leuven first (the original audience), then the Brussels
 * communes, then the other cities people are likely to travel from. Bilingual
 * labels because the audience now spans both language communities.
 */
export const HOME_LOCATIONS: Origin[] = [
  { key: "leuven", label: "Leuven", lat: 50.8798, lng: 4.7005 },
  { key: "brussel", label: "Brussels centre / Brussel", lat: 50.8467, lng: 4.3525 },
  { key: "ukkel", label: "Uccle / Ukkel", lat: 50.8003, lng: 4.3372 },
  { key: "elsene", label: "Ixelles / Elsene", lat: 50.8229, lng: 4.3714 },
  { key: "etterbeek", label: "Etterbeek", lat: 50.8366, lng: 4.3897 },
  { key: "schaarbeek", label: "Schaerbeek / Schaarbeek", lat: 50.8676, lng: 4.3737 },
  { key: "woluwe", label: "Woluwe-Saint-Lambert", lat: 50.8465, lng: 4.4265 },
  { key: "anderlecht", label: "Anderlecht", lat: 50.8362, lng: 4.3096 },
  { key: "jette", label: "Jette", lat: 50.8776, lng: 4.326 },
  { key: "tervuren", label: "Tervuren", lat: 50.8235, lng: 4.5158 },
  { key: "mechelen", label: "Mechelen", lat: 51.0259, lng: 4.4776 },
  { key: "antwerpen", label: "Antwerp / Antwerpen", lat: 51.2211, lng: 4.3997 },
  { key: "gent", label: "Ghent / Gent", lat: 51.0538, lng: 3.725 },
];

export const DEFAULT_ORIGIN = HOME_LOCATIONS[0];

const COORD_RE = /^(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)$/;

/**
 * Great-circle distance in km, rounded to one decimal. Mirrors
 * automation/geo.py:haversine_km exactly (same radius, same rounding) so a
 * client-computed distance matches the shipped distance_km when origin=Leuven.
 */
export function haversineKm(aLat: number, aLng: number, bLat: number, bLng: number): number {
  const R = 6371.0088;
  const rad = Math.PI / 180;
  const p1 = aLat * rad;
  const p2 = bLat * rad;
  const dp = (bLat - aLat) * rad;
  const dl = (bLng - aLng) * rad;
  const h =
    Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return Math.round(2 * R * Math.asin(Math.sqrt(h)) * 10) / 10;
}

/** A preset key, or a "lat,lng" pair from the "use my location" button. */
export function parseOrigin(raw: string | null | undefined): Origin {
  if (!raw) return DEFAULT_ORIGIN;
  const preset = HOME_LOCATIONS.find((o) => o.key === raw);
  if (preset) return preset;
  const m = COORD_RE.exec(raw);
  if (m) {
    const lat = Number(m[1]);
    const lng = Number(m[2]);
    if (Math.abs(lat) <= 90 && Math.abs(lng) <= 180) return customOrigin(lat, lng);
  }
  return DEFAULT_ORIGIN;
}

export function customOrigin(lat: number, lng: number): Origin {
  const round = (n: number) => Math.round(n * 10000) / 10000;
  return { key: `${round(lat)},${round(lng)}`, label: "My location", lat: round(lat), lng: round(lng) };
}

export function isCustomOrigin(o: Origin): boolean {
  return !HOME_LOCATIONS.some((p) => p.key === o.key);
}

/**
 * Re-derive distance_km for the chosen origin. Everything downstream (the
 * distance predicate, the distance sort, the card badge) keeps reading
 * activity.distance_km, so no other call site needs to know about origins.
 */
export function withDistance(activities: Activity[], origin: Origin): Activity[] {
  return activities.map((a) =>
    a.lat == null || a.lng == null
      ? a.distance_km == null
        ? a
        : { ...a, distance_km: null }
      : { ...a, distance_km: haversineKm(a.lat, a.lng, origin.lat, origin.lng) }
  );
}
