import type { Dataset } from "../types";

export async function loadDataset(): Promise<Dataset> {
  const base = import.meta.env.BASE_URL || "/";
  const res = await fetch(`${base}data/latest.json`, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load data (${res.status})`);
  return (await res.json()) as Dataset;
}

export function googleTranslateUrl(text: string): string {
  const t = encodeURIComponent(text.slice(0, 900));
  return `https://translate.google.com/?sl=nl&tl=en&op=translate&text=${t}`;
}
