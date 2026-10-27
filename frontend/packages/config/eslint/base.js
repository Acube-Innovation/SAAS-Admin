import js from "@eslint/js";
import tseslint from "typescript-eslint";

/** Shared ESLint flat config for TypeScript packages. */
export default tseslint.config(
  { ignores: ["dist/**", "coverage/**", ".turbo/**"] },
  js.configs.recommended,
  ...tseslint.configs.recommended,
);
