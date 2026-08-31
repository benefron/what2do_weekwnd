// Kept in sync with automation/config.py + automation/prompts/enrich_schema.json

export type Category =
  | "festival" | "kermis_carnaval" | "theatre_puppetry" | "music_concert"
  | "museum_exhibition" | "museum_workshop" | "library_workshop" | "nature_farm"
  | "zoo_animal_park" | "sports_active" | "market_food" | "parade_seasonal"
  | "film" | "storytelling" | "playground_indoor" | "guided_tour"
  | "holiday_camp" | "other";

export type FeatureTag =
  | "face_painting" | "archery" | "bouncy_castle" | "puppet_show" | "kermis"
  | "carnaval" | "food" | "stories" | "costumes" | "animals" | "crafts"
  | "music" | "science" | "water_play" | "dance" | "magic" | "treasure_hunt"
  | "fireworks" | "parade" | "train_ride" | "pumpkin_picking"
  | "christmas_market" | "easter" | "halloween";

export type WeekendBucket = "this_weekend" | "next_weekend" | "school_holiday" | "later";

export interface Occurrence {
  start: string | null;
  end: string | null;
}

export interface Activity {
  id: string;
  source: string;
  source_label: string;
  url: string;
  last_seen_run: string;

  title_nl: string;
  description_nl: string;
  organizer_nl: string | null;
  blurb_en: string | null;
  image_url: string | null;

  date_start: string | null;
  date_end: string | null;
  all_day: boolean;
  occurrences: Occurrence[];
  date_kind: "single" | "multi_day" | "recurring" | "permanent";
  weekend_bucket: WeekendBucket[];
  in_school_holiday: boolean;
  school_holiday_name: string | null;

  venue_name: string | null;
  address: string | null;
  city: string | null;
  postal_code: string | null;
  lat: number | null;
  lng: number | null;
  distance_km: number | null;
  geocode_source: string;

  category: Category;
  feature_tags: FeatureTag[];
  audience: string;
  age_min: number | null;
  age_max: number | null;
  age_source: string | null;
  fits_4yo: boolean;
  fits_8yo: boolean;

  price_type: "free" | "paid" | "donation" | "unknown";
  price_min_eur: number | null;
  price_max_eur: number | null;
  price_note_nl: string | null;

  primary_language: "nl" | "fr" | "en" | "multi";
  french_required: boolean;
  language_note: string | null;

  is_special_event: boolean;
  is_recurring_class: boolean;
  booking_required: boolean | null;

  enrichment_model: string;
  confidence: "high" | "medium" | "low";
}

export interface SchoolHoliday {
  name: string;
  start: string;
  end: string;
}

export interface Dataset {
  generated_at: string;
  run_id: string;
  window: { start: string; end: string };
  leuven_center: [number, number];
  school_holidays: SchoolHoliday[];
  categories: { key: Category; count: number }[];
  feature_tags: { key: FeatureTag; count: number }[];
  sources_fetched: string[];
  sources_failed: string[];
  degraded: boolean;
  activities: Activity[];
}
