#!/usr/bin/env node
// typescript-eslint (as of 8.70.1, the latest published version) refuses to
// load against TypeScript 7's native compiler -- see
// https://github.com/typescript-eslint/typescript-eslint/issues/10940, still
// open. TypeScript's own migration notes call this "running side-by-side
// with TypeScript 6.0": https://devblogs.microsoft.com/typescript/announcing-typescript-7-0/#running-side-by-side-with-typescript-6.0
//
// `typescript-for-eslint` (a devDependency aliased to `npm:typescript@^6`)
// gives us that TS 6 copy, but plain node module resolution won't find it:
// typescript-eslint and its @typescript-eslint/* helper packages only declare
// `typescript` as a peerDependency, so npm has no dependency edge to
// redirect and hoists everything to the one root `node_modules/typescript`
// (v7). This script runs as `postinstall` and symlinks the TS 6 copy into
// `node_modules/typescript` inside each package that needs it, so their
// `require("typescript")` resolves the nested copy before falling back to
// the root one. Idempotent and safe to re-run.
import { existsSync, mkdirSync, rmSync, symlinkSync, realpathSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const nodeModules = path.join(frontendRoot, "node_modules");
const source = path.join(nodeModules, "typescript-for-eslint");

if (!existsSync(source)) {
  // Not installed (e.g. a partial/production install) -- nothing to link.
  process.exit(0);
}
const sourceReal = realpathSync(source);

const targets = [
  "typescript-eslint",
  "@typescript-eslint/parser",
  "@typescript-eslint/eslint-plugin",
  "@typescript-eslint/typescript-estree",
  "@typescript-eslint/utils",
  "@typescript-eslint/type-utils",
  "@typescript-eslint/project-service",
  "@typescript-eslint/tsconfig-utils",
  "ts-api-utils",
];

for (const pkg of targets) {
  const pkgDir = path.join(nodeModules, pkg);
  if (!existsSync(pkgDir)) continue; // optional/absent in some installs

  const nestedModules = path.join(pkgDir, "node_modules");
  const link = path.join(nestedModules, "typescript");

  if (existsSync(link) && realpathSync(link) === sourceReal) continue; // already correct

  mkdirSync(nestedModules, { recursive: true });
  rmSync(link, { recursive: true, force: true });
  symlinkSync(sourceReal, link, "dir");
}
