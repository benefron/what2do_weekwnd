import type { Activity, Dataset } from "../types";

export async function loadDataset(): Promise<Dataset> {
  const base = import.meta.env.BASE_URL || "/";
  const res = await fetch(`${base}data/latest.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load data (${res.status})`);
  return (await res.json()) as Dataset;
}

/** `from` is the activity's own language; "multi" has no single source, so let
 *  Google auto-detect rather than mislabelling it as Dutch. */
export function googleTranslateUrl(text: string, from: string = "nl"): string {
  const t = encodeURIComponent(text.slice(0, 900));
  const sl = from === "nl" || from === "fr" || from === "en" ? from : "auto";
  return `https://translate.google.com/?sl=${sl}&tl=en&op=translate&text=${t}`;
}

/** Google Maps search URL for the activity's location, or null when nothing is
 *  locatable. The universal `maps/search/?api=1` form opens the native app on
 *  phones and the website elsewhere. We search by venue name + address rather
 *  than coordinates so Maps lands on the venue's own listing (hours, photos,
 *  directions) instead of an anonymous pin; `lat,lng` is only the fallback for
 *  the few records with coordinates but no usable text. */
export function googleMapsUrl(a: Pick<Activity, "venue_name" | "address" | "city" | "lat" | "lng">): string | null {
  const venue = a.venue_name?.trim() || "";
  const address = a.address?.trim() || "";
  const city = a.city?.trim() || "";
  // Don't append the city when the address already ends with it ("…, Laeken" + "Laeken").
  const place = address && city && address.toLowerCase().endsWith(city.toLowerCase()) ? address : [address, city].filter(Boolean).join(", ");
  const query = [venue, place].filter(Boolean).join(", ");
  if (query) return `https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(query)}`;
  if (a.lat != null && a.lng != null) return `https://www.google.com/maps/search/?api=1&query=${a.lat}%2C${a.lng}`;
  return null;
}
