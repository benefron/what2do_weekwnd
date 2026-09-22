// @ts-check
import js from "@eslint/js";
import globals from "globals";
import tseslint from "typescript-eslint";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";

// Non-type-checked linting only: typescript-eslint's typed rules need the
// TS compiler's Program/type-checker API, which typescript-eslint hasn't
// yet ported to TypeScript 7's native (Corsa/Go) compiler -- its own peer
// range is still `>=4.8.4 <6.1.0` (see the deps upgrade log). Syntax-only
// linting (typescript-eslint.configs.recommended, no `parserOptions.project`)
// works fine against TS7's parser output, so that's what's wired up here.
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
