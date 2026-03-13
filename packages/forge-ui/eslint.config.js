import js from "@eslint/js";
import tsPlugin from "@typescript-eslint/eslint-plugin";
import tsParser from "@typescript-eslint/parser";
import reactHooksPlugin from "eslint-plugin-react-hooks";

export default [
  js.configs.recommended,
  {
    ignores: ["dist/**", "*.config.*"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsParser,
      parserOptions: {
        ecmaVersion: "latest",
        sourceType: "module",
        ecmaFeatures: { jsx: true },
      },
      globals: {
        // Browser globals
        document: "readonly",
        window: "readonly",
        localStorage: "readonly",
        fetch: "readonly",
        console: "readonly",
        setTimeout: "readonly",
        clearTimeout: "readonly",
        structuredClone: "readonly",
        URL: "readonly",
        URLSearchParams: "readonly",
        EventSource: "readonly",
        AbortController: "readonly",
        RequestInit: "readonly",
        Response: "readonly",
        // DOM types
        React: "readonly",
        HTMLElement: "readonly",
        HTMLDialogElement: "readonly",
        HTMLInputElement: "readonly",
        HTMLTextAreaElement: "readonly",
        HTMLSelectElement: "readonly",
        HTMLButtonElement: "readonly",
        HTMLDetailsElement: "readonly",
        HTMLDivElement: "readonly",
        HTMLLabelElement: "readonly",
        HTMLHeadingElement: "readonly",
        HTMLParagraphElement: "readonly",
        // Event types
        KeyboardEvent: "readonly",
        MouseEvent: "readonly",
        PopStateEvent: "readonly",
        Event: "readonly",
        MediaQueryListEvent: "readonly",
        MediaQueryList: "readonly",
      },
    },
    plugins: {
      "@typescript-eslint": tsPlugin,
      "react-hooks": reactHooksPlugin,
    },
    rules: {
      ...tsPlugin.configs.recommended.rules,
      "no-unused-vars": "off",
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
      "@typescript-eslint/consistent-type-imports": [
        "warn",
        { prefer: "type-imports" },
      ],
      "react-hooks/rules-of-hooks": "error",
      "react-hooks/exhaustive-deps": "warn",
      "no-console": ["warn", { allow: ["warn", "error"] }],
    },
  },
];
