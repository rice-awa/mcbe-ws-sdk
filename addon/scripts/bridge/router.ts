import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { CAPABILITY_REQUEST_SCRIPT_EVENT_ID, MCBEWS_V1_ERROR_CODES } from "./protocol";
import { defaultCapabilityRegistry } from "./capabilities";
import { utf8ByteLength } from "./chunking";

// ---------------------------------------------------------------------------
// Structured types
// ---------------------------------------------------------------------------

export type BridgeErrorCode = (typeof MCBEWS_V1_ERROR_CODES)[number];

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
  context: CapabilityContext
) => Record<string, unknown> | Promise<Record<string, unknown>>;

export type ResponseSender = (requestId: string, jsonBody: string) => Promise<void>;

export type ResponseSendOutcome =
  | { requestId: string; ok: true; delivered: true }
  | {
      requestId: string;
      ok: false;
      delivered: false;
      error: { code: "RESPONSE_SEND_FAILED"; message: string; errorType: string };
    };

type RouterEvent = Pick<ScriptEventCommandMessageAfterEvent, "id" | "message" | "sourceType">;

type ParseResult =
  { ok: true; request: BridgeRequest } | { ok: false; requestId?: string; response: BridgeErrorResponse };

// ---------------------------------------------------------------------------
// Module state
// ---------------------------------------------------------------------------

let isBridgeRouterRegistered = false;
let capabilityHandler: CapabilityHandler | null = null;
let responseSender: ResponseSender | null = null;
let bridgeActive = false;
const preReadyQueue: RouterEvent[] = [];
let processingTail: Promise<void> = Promise.resolve();
let lastResponseSendOutcome: ResponseSendOutcome | null = null;

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
  console.error("[bridge] unexpected router failure", error instanceof Error ? error.constructor.name : typeof error);
}

function recordResponseSendFailure(requestId: string, errorType = "send_failure"): ResponseSendOutcome {
  const outcome: ResponseSendOutcome = {
    requestId,
    ok: false,
    delivered: false,
    error: {
      code: "RESPONSE_SEND_FAILED",
      message: "bridge response sender failed",
      errorType,
    },
  };
  lastResponseSendOutcome = outcome;
  return outcome;
}

function recordResponseSent(requestId: string): ResponseSendOutcome {
  const outcome: ResponseSendOutcome = { requestId, ok: true, delivered: true };
  lastResponseSendOutcome = outcome;
  return outcome;
}

// ---------------------------------------------------------------------------
// shouldHandleScriptEvent
// ---------------------------------------------------------------------------

export function shouldHandleScriptEvent(messageId: string): boolean {
  return messageId === CAPABILITY_REQUEST_SCRIPT_EVENT_ID;
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
    isRecord(value) && typeof value.request_id === "string" && value.request_id.trim() ? value.request_id : undefined;
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
    .then(async () => {
      await handleBridgeScriptEvent(event);
    })
    .catch(logUnexpectedRouterFailure);
}

export function enqueueOrHandle(event: ScriptEventCommandMessageAfterEvent): void {
  if (!shouldHandleScriptEvent(event.id)) return;

  // IMPORTANT: /wsserver commandRequest-scriptevent does NOT always arrive as
  // sourceType === "Server". In practice Bedrock may tag it as Entity (bound to
  // the connecting player). The parent MCBE-AI-Agent-addon accepts any source;
  // filtering Server-only causes silent drops → Python 5s bridge timeout even
  // though statusMessage says "Script event ... has been sent".
  //
  // We still log non-Server sources so real player-typed /scriptevent spam is
  // visible, but we do not drop them — the request carries a random request_id
  // and only resolves a pending Python future if it matches.
  if (event.sourceType !== "Server") {
    console.warn(
      `[bridge] accepting non-Server scriptevent: channel=capability, sourceType=${event.sourceType}, ` +
        `bytes=${utf8ByteLength(event.message)}`
    );
  }

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
    console.warn(
      `[bridge] queue pre-ready request: channel=capability, queueSize=${preReadyQueue.length + 1}, ` +
        `sourceType=${event.sourceType}, bytes=${utf8ByteLength(event.message)}`
    );
    preReadyQueue.push(snapshot);
    return;
  }
  console.log(
    `[bridge] accept scriptevent: channel=capability, sourceType=${event.sourceType}, ` +
      `bytes=${utf8ByteLength(event.message)}`
  );
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

export async function handleBridgeScriptEvent(event: RouterEvent): Promise<ResponseSendOutcome | null> {
  // Do not re-filter sourceType here — enqueueOrHandle already decided to accept.
  // A second Server-only gate would drop WS-originated Entity events after queueing.

  const parsed = parseBridgeRequest(event.message);
  if (!parsed.ok) {
    if (parsed.requestId && responseSender) {
      try {
        await responseSender(parsed.requestId, JSON.stringify(parsed.response));
        return recordResponseSent(parsed.requestId);
      } catch (error) {
        console.error(
          `[bridge] response sender failed: channel=capability, requestId=${parsed.requestId}, ` +
            `errorType=${error instanceof Error ? error.constructor.name : typeof error}`
        );
        return recordResponseSendFailure(
          parsed.requestId,
          error instanceof Error ? error.constructor.name : typeof error
        );
      }
    }
    return null;
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
      const body = JSON.stringify(resultPayload);
      console.log(
        `[bridge] send response: channel=capability, requestId=${request.request_id}, ` +
          `bytes=${utf8ByteLength(body)}`
      );
      await responseSender(request.request_id, body);
      console.log(`[bridge] response sent: channel=capability, requestId=${request.request_id}`);
      return recordResponseSent(request.request_id);
    } catch (error) {
      console.error(
        `[bridge] response sender failed: channel=capability, requestId=${request.request_id}, ` +
          `errorType=${error instanceof Error ? error.constructor.name : typeof error}`
      );
      return recordResponseSendFailure(
        request.request_id,
        error instanceof Error ? error.constructor.name : typeof error
      );
    }
  } else {
    console.warn(`[bridge] no responseSender: channel=capability, requestId=${request.request_id}`);
    return recordResponseSendFailure(request.request_id, "missing_response_sender");
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
export function _testingGetLastResponseSendOutcome(): ResponseSendOutcome | null {
  return lastResponseSendOutcome;
}

/** @internal */
export function _testingReset(): void {
  preReadyQueue.length = 0;
  responseSender = null;
  capabilityHandler = null;
  bridgeActive = false;
  isBridgeRouterRegistered = false;
  processingTail = Promise.resolve();
  lastResponseSendOutcome = null;
}
