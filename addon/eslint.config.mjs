import minecraftLinting from "eslint-plugin-minecraft-linting";
import tsParser from "@typescript-eslint/parser";
import ts from "@typescript-eslint/eslint-plugin";

export default [
  {
    files: ["scripts/**/*.ts"],
    languageOptions: {
      parser: tsParser,
      ecmaVersion: "latest",
    },
    plugins: {
      ts,
      "minecraft-linting": minecraftLinting,
    },
    rules: {
      "minecraft-linting/avoid-unnecessary-command": "error",
      "no-restricted-globals": [
        "error",
        ...[
          "Buffer",
          "TextDecoder",
          "TextEncoder",
          "document",
          "fetch",
          "process",
          "setInterval",
          "setTimeout",
          "window",
        ].map((name) => ({
          name,
          message: `${name} is not part of the Bedrock Script runtime. Use @minecraft/server APIs or runtime-independent JavaScript.`,
        })),
      ],
      "no-restricted-imports": [
        "error",
        {
          patterns: [
            {
              group: ["node:*"],
              message: "Node.js modules are not available in the Bedrock Script runtime.",
            },
          ],
        },
      ],
    },
  },
];
