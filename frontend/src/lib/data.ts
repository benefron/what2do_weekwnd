import type { Dataset } from "../types";

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
