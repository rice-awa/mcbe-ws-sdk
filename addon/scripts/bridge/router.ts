import type { Player, ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { BRIDGE_MESSAGE_ID, TOOL_PLAYER_NAME } from "./constants";
import { defaultCapabilityRegistry } from "./capabilities";

let isBridgeRouterRegistered = false;

export type CapabilityHandler = (
  capability: string,
  payload: Record<string, unknown>
) => Record<string, unknown> | Promise<Record<string, unknown>>;

export type ResponseSender = (requestId: string, jsonBody: string) => Promise<void>;

let capabilityHandler: CapabilityHandler | null = null;
let responseSender: ResponseSender | null = null;

/** 注册能力处理函数。未知能力返回默认错误 payload。 */
export function setCapabilityHandler(fn: CapabilityHandler): void {
  capabilityHandler = fn;
}

/** 注册响应发送器（将编码后的 JSON 载荷分片回传给 MCBEAI_TOOL 玩家）。 */
export function setResponseSender(fn: ResponseSender): void {
  responseSender = fn;
}

function defaultErrorPayload(capability: string): Record<string, unknown> {
  return { ok: false, error: `unsupported capability: ${capability}` };
}

export function shouldHandleScriptEvent(messageId: string): boolean {
  return messageId === BRIDGE_MESSAGE_ID;
}

export async function handleBridgeScriptEvent(event: ScriptEventCommandMessageAfterEvent): Promise<void> {
  if (!shouldHandleScriptEvent(event.id)) {
    return;
  }

  // 发送者校验：脚本事件默认仅放行服务端来源，与 Python SDK 用
  // sender == MCBEAI_TOOL 做入站信任边界的意图对齐——服务端来源比聊天消息路径
  // 更窄，足以防止玩家伪造 scriptevent。实体来源仅在发送者为 MCBEAI_TOOL 工具玩家时放行。
  const isFromServer = event.sourceType === "Server";
  const isFromToolPlayer =
    event.sourceType === "Entity" && (event.sourceEntity as Player | undefined)?.name === TOOL_PLAYER_NAME;
  if (!isFromServer && !isFromToolPlayer) {
    console.warn(`[bridge] 忽略非法来源的 scriptevent: id=${event.id}, sourceType=${event.sourceType}`);
    return;
  }

  const request = JSON.parse(event.message) as {
    request_id: string;
    capability: string;
    payload?: Record<string, unknown>;
  };

  let payload: Record<string, unknown>;
  if (capabilityHandler) {
    // 宿主已注入处理器，优先使用（覆盖默认注册表）。
    payload = await capabilityHandler(request.capability, request.payload ?? {});
  } else {
    // 回退到默认注册表：按能力名查找并调用。
    const defaultHandler = defaultCapabilityRegistry[request.capability];
    if (defaultHandler) {
      payload = await defaultHandler(request.capability, request.payload ?? {});
    } else {
      payload = defaultErrorPayload(request.capability);
    }
  }

  if (responseSender) {
    await responseSender(request.request_id, JSON.stringify(payload));
  }
}

export function registerBridgeRouter(): void {
  if (isBridgeRouterRegistered) {
    return;
  }

  isBridgeRouterRegistered = true;

  system.afterEvents.scriptEventReceive.subscribe((event) => {
    if (!shouldHandleScriptEvent(event.id)) {
      return;
    }

    void handleBridgeScriptEvent(event);
  });
}
