import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import { defineConfig, globalIgnores } from "eslint/config";
import globals from "globals";
import tseslint from "typescript-eslint";

export default defineConfig([
  globalIgnores(["dist", "dist-demo", "public", "src/api/schema.d.ts", "playwright-report", "test-results"]),
  {
    files: ["**/*.{ts,tsx}"],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: { ecmaVersion: 2022, globals: globals.browser },
    rules: {
      // Backend and evidence text is untrusted (openapi.yaml → Untrusted text): never as HTML.
      "no-restricted-syntax": [
        "error",
        {
          selector: "JSXAttribute[name.name='dangerouslySetInnerHTML']",
          message: "Render API text as text, never HTML.",
        },
        {
          selector: "MemberExpression[property.name='innerHTML']",
          message: "Render API text as text, never HTML.",
        },
      ],
    },
  },
  {
    // Test helpers export components alongside functions; fast refresh doesn't apply to them.
    files: ["src/test/**", "**/*.test.{ts,tsx}"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    // Playwright tests run in Node; its fixture callback is named `use`, which isn't React's.
    files: ["e2e/**", "playwright.config.ts"],
    languageOptions: { globals: { ...globals.node, ...globals.browser } },
    rules: { "react-hooks/rules-of-hooks": "off" },
  },
  {
    // Only src/api/ talks to the backend (AGENTS.md → Frontend).
    files: ["src/**/*.{ts,tsx}"],
    ignores: ["src/api/**", "src/mocks/**", "src/test/**"],
    rules: {
      "no-restricted-globals": ["error", { name: "fetch", message: "Call the backend through src/api/." }],
      "no-restricted-imports": [
        "error",
        { paths: [{ name: "openapi-fetch", message: "Call the backend through src/api/." }] },
      ],
    },
  },
]);
