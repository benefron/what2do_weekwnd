#!/usr/bin/env node
/**
 * Rasterise frontend/public/icons/icon.svg and icon-maskable.svg into the PNGs
 * the PWA manifest and index.html expect.
 *
 * Re-run with:
 *   node frontend/scripts/make-icons.mjs
 * (or `cd frontend && node scripts/make-icons.mjs`)
 *
 * Tries, in order, whatever rasteriser already exists on the machine — no new
 * npm dependency is added for this:
 *   1. rsvg-convert   (librsvg, `brew install librsvg`)
 *   2. ImageMagick    (`magick` or legacy `convert`)
 *   3. Inkscape CLI
 *   4. macOS `sips` (works for SVG -> PNG on recent macOS)
 * If none of those are available, it fails loudly rather than silently
 * skipping an icon.
 */
import { execFileSync } from "node:child_process";
import { existsSync, statSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const iconsDir = path.resolve(__dirname, "..", "public", "icons");

function which(cmd) {
  try {
    execFileSync("which", [cmd], { stdio: ["ignore", "pipe", "ignore"] });
    return true;
  } catch {
    return false;
  }
}

const HAVE_RSVG = which("rsvg-convert");
const HAVE_MAGICK = which("magick");
const HAVE_CONVERT = !HAVE_MAGICK && which("convert");
const HAVE_INKSCAPE =
  which("inkscape") || existsSync("/Applications/Inkscape.app/Contents/MacOS/inkscape");
const INKSCAPE_BIN = which("inkscape")
  ? "inkscape"
  : "/Applications/Inkscape.app/Contents/MacOS/inkscape";
const HAVE_SIPS = which("sips");

function rasterise(svgPath, pngPath, size) {
  if (HAVE_RSVG) {
    execFileSync("rsvg-convert", ["-w", String(size), "-h", String(size), "-o", pngPath, svgPath]);
    return "rsvg-convert";
  }
  if (HAVE_MAGICK) {
    execFileSync("magick", [
      "-background",
      "none",
      "-density",
      "384",
      svgPath,
      "-resize",
      `${size}x${size}`,
      pngPath,
    ]);
    return "magick";
  }
  if (HAVE_CONVERT) {
    execFileSync("convert", [
      "-background",
      "none",
      "-density",
      "384",
      svgPath,
      "-resize",
      `${size}x${size}`,
      pngPath,
    ]);
    return "convert";
  }
  if (HAVE_INKSCAPE) {
    execFileSync(INKSCAPE_BIN, [
      svgPath,
      `--export-filename=${pngPath}`,
      `-w`,
      String(size),
      `-h`,
      String(size),
    ]);
    return "inkscape";
  }
  if (HAVE_SIPS) {
    // sips can rasterise SVG directly on recent macOS.
    execFileSync("sips", ["-s", "format", "png", "-z", String(size), String(size), svgPath, "--out", pngPath]);
    return "sips";
  }
  throw new Error(
    "No SVG rasteriser found. Install one of: librsvg (`brew install librsvg`), " +
      "ImageMagick (`brew install imagemagick`), or Inkscape.",
  );
}

const jobs = [
  { svg: "icon.svg", png: "icon-192.png", size: 192 },
  { svg: "icon.svg", png: "icon-512.png", size: 512 },
  { svg: "icon.svg", png: "apple-touch-icon.png", size: 180 },
  { svg: "icon-maskable.svg", png: "icon-maskable-512.png", size: 512 },
];

let usedTool = null;
for (const job of jobs) {
  const svgPath = path.join(iconsDir, job.svg);
  const pngPath = path.join(iconsDir, job.png);
  if (!existsSync(svgPath)) {
    throw new Error(`Missing source SVG: ${svgPath}`);
  }
  usedTool = rasterise(svgPath, pngPath, job.size);
  const { size } = statSync(pngPath);
  console.log(`${job.png} (${job.size}x${job.size}) via ${usedTool} — ${size} bytes`);
}

console.log("Done.");
