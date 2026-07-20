interface BedrockConsole {
  error(...data: unknown[]): void;
  log(...data: unknown[]): void;
  warn(...data: unknown[]): void;
}

declare const console: BedrockConsole;
