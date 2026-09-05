import { useState } from "react";
import type { Activity } from "../types";
import {
  CATEGORY_LABELS, FEATURE_EMOJI, FEATURE_LABELS, LANGUAGE_EMOJI, LANGUAGE_LABELS,
  PLACE_KIND_EMOJI, PLACE_KIND_LABELS,
} from "../lib/labels";
import { formatAgeRange, formatDate, formatDistance, formatPrice } from "../lib/format";
import { googleTranslateUrl } from "../lib/data";

interface Props {
  activity: Activity;
  saved: boolean;
  originLabel: string;
  onToggleSave: (id: string) => void;
}

export default function ActivityCard({ activity: a, saved, originLabel, onToggleSave }: Props) {
  const price = formatPrice(a);
  const distance = formatDistance(a, originLabel);
  const [imgOk, setImgOk] = useState(true);
  const showImg = a.image_url && imgOk;

  return (
    <article className="group relative flex flex-col overflow-hidden rounded-xl2 border border-line bg-white shadow-card">
      {a.is_special_event && a.date_kind !== "permanent" && (
        <span className="absolute left-3 top-3 z-10 rounded-full bg-tangerine px-2.5 py-1 text-xs font-semibold text-white shadow">
          Special event
        </span>
      )}
      <button
        onClick={() => onToggleSave(a.id)}
        aria-label={saved ? "Remove from saved" : "Save"}
        className="absolute right-3 top-3 z-10 grid h-8 w-8 place-items-center rounded-full bg-white/90 text-lg shadow transition hover:scale-110"
      >
        {saved ? "★" : "☆"}
      </button>

      <div className="aspect-[16/10] w-full overflow-hidden bg-forest-soft">
        {showImg ? (
          <img
            src={a.image_url!}
            alt=""
            loading="lazy"
            referrerPolicy="no-referrer"
            className="h-full w-full object-cover transition duration-500 group-hover:scale-105"
            onError={() => setImgOk(false)}
          />
        ) : (
          <div className="flex h-full items-center justify-center text-4xl opacity-40">
            {FEATURE_EMOJI[a.feature_tags[0]] ?? "🎈"}
          </div>
        )}
      </div>

      <div className="flex flex-1 flex-col gap-2 p-4">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs font-medium text-muted">
          <span className="text-forest">
            {a.date_kind === "permanent" && a.kind
              ? `${PLACE_KIND_EMOJI[a.kind]} ${PLACE_KIND_LABELS[a.kind]}`
              : CATEGORY_LABELS[a.category]}
          </span>
          <span aria-hidden>·</span>
          <span>
            {a.date_kind === "permanent" && a.city ? a.city : formatDate(a)}
          </span>
        </div>

        <h3
          className="font-display text-lg font-semibold leading-snug text-ink"
          lang={a.primary_language === "multi" ? undefined : a.primary_language}
        >
          {a.title_nl}
        </h3>

        {a.blurb_en && <p className="text-sm text-muted">{a.blurb_en}</p>}

        <div className="mt-1 flex flex-wrap items-center gap-1.5">
          <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${price.free ? "bg-forest text-white" : "bg-ink/5 text-ink"}`}>
            {price.text}
          </span>
          {distance && (
            <span className="rounded-full bg-ink/5 px-2 py-0.5 text-xs font-medium text-ink">📍 {distance}</span>
          )}
          <span className="rounded-full border border-line px-2 py-0.5 text-xs">{formatAgeRange(a)}</span>
          {a.primary_language !== "nl" && (
            <span className="rounded-full bg-ink/5 px-2 py-0.5 text-xs font-medium text-ink">
              {LANGUAGE_EMOJI[a.primary_language]} {LANGUAGE_LABELS[a.primary_language]}
            </span>
          )}
          {a.french_required && (
            <span className="rounded-full bg-berry/10 px-2 py-0.5 text-xs font-medium text-berry">
              🇫🇷 French needed
            </span>
          )}
        </div>

        {a.feature_tags.length > 0 && (
          <div className="flex flex-wrap gap-1 pt-1">
            {a.feature_tags
              .filter((t) => FEATURE_LABELS[t])
              .slice(0, 5)
              .map((t) => (
                <span key={t} className="rounded-md bg-paper px-1.5 py-0.5 text-xs text-muted">
                  {FEATURE_EMOJI[t] ?? ""} {FEATURE_LABELS[t]}
                </span>
              ))}
          </div>
        )}

        {a.language_note && <p className="text-xs italic text-berry">{a.language_note}</p>}

        <div className="mt-auto flex items-center gap-3 pt-3 text-sm font-medium">
          <a href={a.url} target="_blank" rel="noreferrer" className="text-tangerine hover:text-tangerine-dark">
            Details ↗
          </a>
          <a
            href={googleTranslateUrl(`${a.title_nl}. ${a.description_nl}`, a.primary_language)}
            target="_blank"
            rel="noreferrer"
            className="text-muted hover:text-ink"
          >
            Translate
          </a>
          {a.venue_name && <span className="ml-auto truncate text-xs text-muted">{a.venue_name}</span>}
        </div>
      </div>
    </article>
  );
}
