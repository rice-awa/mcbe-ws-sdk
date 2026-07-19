import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { BRIDGE_MESSAGE_ID } from "./constants";

let isBridgeRouterRegistered = false;

export type CapabilityHandler = (
  capability: string,
  payload: Record<string, unknown>,
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

  const request = JSON.parse(event.message) as {
    request_id: string;
    capability: string;
    payload?: Record<string, unknown>;
  };

  const payload = capabilityHandler
    ? await capabilityHandler(request.capability, request.payload ?? {})
    : defaultErrorPayload(request.capability);

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
