import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { BRIDGE_MESSAGE_ID } from "./constants";
import { defaultCapabilityRegistry } from "./capabilities";

// ---------------------------------------------------------------------------
// Structured types
// ---------------------------------------------------------------------------

export type BridgeErrorCode =
  | "MALFORMED_JSON"
  | "INVALID_REQUEST"
  | "UNSUPPORTED_VERSION"
  | "UNSUPPORTED_CAPABILITY"
  | "CAPABILITY_FAILED";

export type BridgeErrorResponse = {
  ok: false;
  error: { code: BridgeErrorCode; message: string };
};

export type BridgeSuccessResponse = {
  ok: true;
  result: Record<string, unknown>;
};

export type BridgeRequest = {
  v: 1 | 2;
  request_id: string;
  capability: string;
  payload: Record<string, unknown>;
};

export type CapabilityContext = {
  caller: { kind: "server" };
  requestVersion: 1 | 2;
};

export type CapabilityHandler = (
  capability: string,
  payload: Record<string, unknown>,
  context: CapabilityContext,
) => Record<string, unknown> | Promise<Record<string, unknown>>;

export type ResponseSender = (requestId: string, jsonBody: string) => Promise<void>;

type RouterEvent = Pick<ScriptEventCommandMessageAfterEvent, "id" | "message" | "sourceType">;

type ParseResult =
  | { ok: true; request: BridgeRequest }
  | { ok: false; requestId?: string; response: BridgeErrorResponse };

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let isBridgeRouterRegistered = false;
let capabilityHandler: CapabilityHandler | null = null;
let responseSender: ResponseSender | null = null;
let bridgeActive = false;
const preReadyQueue: RouterEvent[] = [];
let processingTail: Promise<void> = Promise.resolve();

export const MAX_PRE_READY_REQUESTS = 64;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

function invalidRequest(requestId?: string): ParseResult {
  return {
    ok: false,
    requestId,
    response: { ok: false, error: { code: "INVALID_REQUEST", message: "invalid bridge request" } },
  };
}

function logUnexpectedRouterFailure(error: unknown): void {
  console.error(
    "[bridge] unexpected router failure",
    error instanceof Error ? error.message : String(error),
  );
}

// ---------------------------------------------------------------------------
// shouldHandleScriptEvent
// ---------------------------------------------------------------------------

export function shouldHandleScriptEvent(messageId: string): boolean {
  return messageId === BRIDGE_MESSAGE_ID;
}

// ---------------------------------------------------------------------------
// parseBridgeRequest
// ---------------------------------------------------------------------------

export function parseBridgeRequest(message: string): ParseResult {
  let value: unknown;
  try {
    value = JSON.parse(message);
  } catch {
    return {
      ok: false,
      response: { ok: false, error: { code: "MALFORMED_JSON", message: "invalid JSON" } },
    };
  }
  const requestId =
    isRecord(value) && typeof value.request_id === "string" && value.request_id.trim()
      ? value.request_id
      : undefined;
  if (!isRecord(value)) {
    return invalidRequest(requestId);
  }
  const rawVersion = value.v ?? 1;
  if (rawVersion !== 1 && rawVersion !== 2) {
    return {
      ok: false,
      requestId,
      response: {
        ok: false,
        error: { code: "UNSUPPORTED_VERSION", message: "unsupported bridge version" },
      },
    };
  }
  if (
    !requestId ||
    typeof value.capability !== "string" ||
    !value.capability.trim() ||
    (value.payload !== undefined && !isRecord(value.payload))
  ) {
    return invalidRequest(requestId);
  }
  return {
    ok: true,
    request: {
      v: rawVersion,
      request_id: requestId,
      capability: value.capability,
      payload: value.payload ?? {},
    },
  };
}

// ---------------------------------------------------------------------------
// Pre-ready state machine
// ---------------------------------------------------------------------------

function schedule(event: RouterEvent): void {
  processingTail = processingTail
    .then(() => handleBridgeScriptEvent(event))
    .catch(logUnexpectedRouterFailure);
}

export function enqueueOrHandle(event: ScriptEventCommandMessageAfterEvent): void {
  if (!shouldHandleScriptEvent(event.id) || event.sourceType !== "Server") return;
  const snapshot: RouterEvent = {
    id: event.id,
    message: event.message,
    sourceType: event.sourceType,
  };
  if (!bridgeActive) {
    if (preReadyQueue.length >= MAX_PRE_READY_REQUESTS) {
      console.warn("[bridge] pre-ready queue full: code=BRIDGE_NOT_READY_QUEUE_FULL");
      return;
    }
    preReadyQueue.push(snapshot);
    return;
  }
  schedule(snapshot);
}

export async function activateBridge(sender: ResponseSender): Promise<void> {
  if (bridgeActive) return;
  responseSender = sender;
  bridgeActive = true;
  while (preReadyQueue.length > 0) {
    const event = preReadyQueue.shift();
    if (event) schedule(event);
  }
  await processingTail;
}

// ---------------------------------------------------------------------------
// setCapabilityHandler
// ---------------------------------------------------------------------------

export function setCapabilityHandler(fn: CapabilityHandler): void {
  capabilityHandler = fn;
}

// ---------------------------------------------------------------------------
// handleBridgeScriptEvent
// ---------------------------------------------------------------------------

export async function handleBridgeScriptEvent(event: RouterEvent): Promise<void> {
  if (event.sourceType !== "Server") return;

  const parsed = parseBridgeRequest(event.message);
  if (!parsed.ok) {
    if (parsed.requestId && responseSender) {
      try {
        await responseSender(parsed.requestId, JSON.stringify(parsed.response));
      } catch (error) {
        console.error(
          `[bridge] response sender failed for requestId=${parsed.requestId}: ${(error as Error).constructor.name}`,
        );
      }
    }
    return;
  }

  const { request } = parsed;

  let resultPayload: Record<string, unknown>;
  const context: CapabilityContext = {
    caller: { kind: "server" },
    requestVersion: request.v,
  };

  if (capabilityHandler) {
    try {
      resultPayload = await capabilityHandler(request.capability, request.payload, context);
    } catch {
      resultPayload = {
        ok: false,
        error: { code: "CAPABILITY_FAILED", message: "capability handler failed" },
      };
    }
  } else {
    const defaultHandler = defaultCapabilityRegistry[request.capability];
    if (defaultHandler) {
      try {
        resultPayload = await defaultHandler(request.capability, request.payload, context);
      } catch {
        resultPayload = {
          ok: false,
          error: { code: "CAPABILITY_FAILED", message: "capability handler failed" },
        };
      }
    } else {
      resultPayload = {
        ok: false,
        error: { code: "UNSUPPORTED_CAPABILITY", message: `unsupported capability: ${request.capability}` },
      };
    }
  }

  if (responseSender) {
    try {
      await responseSender(request.request_id, JSON.stringify(resultPayload));
    } catch (error) {
      console.error(
        `[bridge] response sender failed for requestId=${request.request_id}: ${(error as Error).constructor.name}`,
      );
    }
  }
}

// ---------------------------------------------------------------------------
// registerBridgeRouter
// ---------------------------------------------------------------------------

export function registerBridgeRouter(): void {
  if (isBridgeRouterRegistered) return;
  isBridgeRouterRegistered = true;
  system.afterEvents.scriptEventReceive.subscribe((event) => {
    enqueueOrHandle(event);
  });
}

// ---------------------------------------------------------------------------
// Internal testing helpers
// ---------------------------------------------------------------------------

/** @internal */
export function _testingGetQueueSize(): number {
  return preReadyQueue.length;
}

/** @internal */
export function _testingFlush(): Promise<void> {
  return processingTail;
}

/** @internal */
export function _testingReset(): void {
  preReadyQueue.length = 0;
  responseSender = null;
  capabilityHandler = null;
  bridgeActive = false;
  isBridgeRouterRegistered = false;
  processingTail = Promise.resolve();
}
