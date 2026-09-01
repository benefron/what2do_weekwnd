import type { Activity } from "../types";

const DAY_MS = 86400000;

export function firstFutureDate(a: Activity): Date | null {
  const now = Date.now() - DAY_MS;
  const dates = (a.occurrences ?? [])
    .map((o) => (o.start ? new Date(o.start) : null))
    .filter((d): d is Date => !!d && !isNaN(d.getTime()))
    .sort((x, y) => x.getTime() - y.getTime());
  return dates.find((d) => d.getTime() >= now) ?? dates[0] ?? null;
}

const DMY: Intl.DateTimeFormatOptions = { day: "numeric", month: "short" };
const WDMY: Intl.DateTimeFormatOptions = { weekday: "short", day: "numeric", month: "short" };

export function formatDate(a: Activity): string {
  if (a.date_kind === "permanent") return "Open year-round";

  const now = Date.now() - DAY_MS;
  const start = a.date_start ? new Date(a.date_start) : null;
  const end = a.date_end ? new Date(a.date_end) : null;
  const spans = end && start && end.getTime() - start.getTime() > 6 * DAY_MS;

  // ongoing / long-running series: show the window, not a stale start date
  if ((a.date_kind === "recurring" || a.date_kind === "multi_day") && end && end.getTime() > now && spans) {
    if (start && start.getTime() > now) {
      return `${start.toLocaleDateString("en-GB", DMY)} – ${end.toLocaleDateString("en-GB", DMY)}`;
    }
    return `until ${end.toLocaleDateString("en-GB", WDMY)}`;
  }

  const d = firstFutureDate(a);
  if (!d) return "Date to confirm";
  const base = d.toLocaleDateString("en-GB", WDMY);
  const count = (a.occurrences ?? []).length;
  if (a.date_kind === "recurring") return `${base} · recurring`;
  if (count > 1) return `${base} +${count - 1} more`;
  if (!a.all_day && (d.getHours() || d.getMinutes())) {
    return `${base}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
  }
  return base;
}

export function formatPrice(a: Activity): { text: string; free: boolean } {
  if (a.price_type === "free") return { text: "Free", free: true };
  if (a.price_type === "donation") return { text: "Donation", free: false };
  if (a.price_type === "paid") {
    const lo = a.price_min_eur;
    const hi = a.price_max_eur;
    if (lo != null && hi != null && lo !== hi) return { text: `€${lo}–${hi}`, free: false };
    if (lo != null) return { text: `€${lo}`, free: lo === 0 };
    if (hi != null) return { text: `€${hi}`, free: false };
    return { text: "Paid", free: false };
  }
  return { text: "Price ?", free: false };
}

export function formatDistance(a: Activity): string | null {
  if (a.distance_km == null) return null;
  if (a.distance_km < 1) return "in Leuven";
  return `${Math.round(a.distance_km)} km`;
}
