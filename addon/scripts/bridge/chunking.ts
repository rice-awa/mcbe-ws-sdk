import {
  BRIDGE_COMMAND_LINE_BYTE_BUDGET,
  BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS,
  BRIDGE_RESPONSE_PREFIX,
  BRIDGE_UI_CHAT_PREFIX,
} from "./constants";

export function utf8ByteLength(value: string): number {
  let byteLength = 0;
  for (const symbol of value) {
    const codePoint = symbol.codePointAt(0)!;
    if (codePoint <= 0x7f) byteLength += 1;
    else if (codePoint <= 0x7ff) byteLength += 2;
    else if (codePoint <= 0xffff) byteLength += 3;
    else byteLength += 4;
  }
  return byteLength;
}

export type ChunkOptions = {
  commandLineByteBudget?: number;
  maxContentCodePoints?: number;
  wrapCommandLine?: (chunk: string) => string;
};

export function formatChunk(prefix: string, id: string, index: number, total: number, content: string): string {
  return `${prefix}|${id}|${index}/${total}|${content}`;
}

export function formatResponseChunk(requestId: string, index: number, total: number, content: string): string {
  return formatChunk(BRIDGE_RESPONSE_PREFIX, requestId, index, total, content);
}

export function chunkBridgePayload(requestId: string, payload: string, options: ChunkOptions = {}): string[] {
  return chunkPayload(BRIDGE_RESPONSE_PREFIX, requestId, payload, options);
}

export function chunkUiChatPayload(id: string, payload: string, options: ChunkOptions = {}): string[] {
  return chunkPayload(BRIDGE_UI_CHAT_PREFIX, id, payload, options);
}

export function chunkPayload(prefix: string, id: string, payload: string, options: ChunkOptions = {}): string[] {
  const budget = options.commandLineByteBudget ?? BRIDGE_COMMAND_LINE_BYTE_BUDGET;
  const maxPoints = options.maxContentCodePoints ?? BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS;
  const wrap = options.wrapCommandLine ?? ((chunk: string) => `tell @s ${chunk}`);
  const symbols = Array.from(payload);

  const split = (totalHint: number): string[] => {
    const parts: string[] = [""];
    for (const symbol of symbols) {
      const index = parts.length;
      const candidate = parts[index - 1] + symbol;
      const candidateFrame = formatChunk(prefix, id, index, totalHint, candidate);
      if (Array.from(candidate).length <= maxPoints && utf8ByteLength(wrap(candidateFrame)) <= budget) {
        parts[index - 1] = candidate;
        continue;
      }

      const nextFrame = formatChunk(prefix, id, index + 1, totalHint, symbol);
      if (utf8ByteLength(wrap(nextFrame)) > budget) {
        throw new Error("chunk framing leaves no room for one Unicode code point");
      }
      parts.push(symbol);
    }
    return parts;
  };

  let totalHint = 1;
  while (true) {
    const parts = split(totalHint);
    if (parts.length === totalHint) {
      return parts.map((content, index) => formatChunk(prefix, id, index + 1, parts.length, content));
    }
    totalHint = parts.length;
  }
}
