import { system, world } from "@minecraft/server";

import { AI_RESP_MESSAGE_ID } from "./constants";

// ── 分片缓冲区 ──
// msg_id → Map<index, chunk payload>
const chunkBuffers = new Map<string, Map<number, AiRespChunk>>();

type AiRespChunk = {
  id: string;
  i: number;
  n: number;
  p: string;
  r: string;
  c: string;
};

/** 重组完成后的回调签名：(playerName, role, fullText, sourcePlayer) */
export type AiRespHandler = (playerName: string, role: string, text: string) => void;

let handler: AiRespHandler | null = null;
let isRegistered = false;

/** 注册一个回调，在 mcbeai:ai_resp 报文重组完成后调用。 */
export function setAiRespHandler(fn: AiRespHandler): void {
  handler = fn;
}

/** 注册 scriptEventReceive 订阅，监听 mcbeai:ai_resp 分片。 */
export function registerResponseSyncHandler(): void {
  if (isRegistered) {
    return;
  }
  isRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (event.id !== AI_RESP_MESSAGE_ID) {
      return;
    }

    try {
      const chunk = JSON.parse(event.message) as AiRespChunk;
      handleChunk(chunk);
    } catch {
      // 忽略解析错误
    }
  });
}

function handleChunk(chunk: AiRespChunk): void {
  const { id, i, n, p: playerName, r: role, c: content } = chunk;

  if (!id || i <= 0 || n <= 0 || i > n) {
    return;
  }

  let buffer = chunkBuffers.get(id);
  if (!buffer) {
    buffer = new Map<number, AiRespChunk>();
    chunkBuffers.set(id, buffer);
  }

  buffer.set(i, chunk);

  if (buffer.size < n) {
    return;
  }

  const sortedChunks = [...buffer.values()].sort((a, b) => a.i - b.i);
  const fullText = sortedChunks.map((c) => c.c).join("");

  chunkBuffers.delete(id);

  if (handler) {
    handler(playerName, role, fullText);
  }
}
