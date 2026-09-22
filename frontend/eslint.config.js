// @ts-check
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";

// TypeScript itself (package.json) is deliberately held at 6.x: typescript-eslint
// (still 8.70.1 as of this writing) hard-refuses to load against TypeScript 7 --
// https://github.com/typescript-eslint/typescript-eslint/issues/10940 is open.
// Dependabot will propose bumping `typescript` to 7 once that issue closes and
// typescript-eslint's peer range allows it; until then, such a PR should fail
// CI at `npm run lint`, which is the signal we want.
//
// Non-type-checked linting only here: typed rules need the TS compiler's
// Program/type-checker API (`parserOptions.project`), which isn't wired up.
// typescript-eslint.configs.recommended (syntax-only) is enough for now.
export default tseslint.config(
  {
    ignores: ["dist/**", "dev-dist/**", "node_modules/**"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      ...tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      jsxA11y.flatConfigs.recommended,
    ],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: "module",
      globals: globals.browser,
    },
  },
);
