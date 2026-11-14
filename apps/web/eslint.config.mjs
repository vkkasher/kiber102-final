import js from "@eslint/js";
import tseslint from "typescript-eslint";
import hooks from "eslint-plugin-react-hooks";
import a11y from "eslint-plugin-jsx-a11y";
export default tseslint.config(
  { ignores: [".next/**", ".next-*/**", "out/**", "test-results/**", "playwright-report/**", "next-env.d.ts"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
  { files: ["**/*.{ts,tsx,mjs}"], languageOptions: { globals: { process: "readonly", URL: "readonly", URLSearchParams: "readonly", Response: "readonly", Headers: "readonly", Request: "readonly", AbortSignal: "readonly", TextDecoder: "readonly", Uint8Array: "readonly", crypto: "readonly", console: "readonly", setTimeout: "readonly", globalThis: "readonly" } } },
  { files: ["src/**/*.tsx"], plugins: { "react-hooks": hooks, "jsx-a11y": a11y }, rules: { ...a11y.configs.recommended.rules, "react-hooks/rules-of-hooks": "error", "react-hooks/exhaustive-deps": "error" } }
);
