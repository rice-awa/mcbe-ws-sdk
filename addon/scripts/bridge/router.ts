import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { CAPABILITY_REQUEST_SCRIPT_EVENT_ID, MCBEWS_V1_ERROR_CODES } from "./protocol";
import { createDefaultCapabilityRegistry, registerDefaultCapabilities } from "./capabilities";
import type {
  CapabilityContext,
  CapabilityHandler,
  CapabilityRegistration,
  RegisterCapabilityOptions,
} from "./capabilityRegistry";
import { utf8ByteLength } from "./chunking";
import { MANIFEST as V2_MANIFEST } from "./v2/assets";
import { V2Endpoint } from "./v2/runtime";
import { ReassemblyPool } from "./v2/transport";
import { matches } from "./v2/codec";

export type {
  CapabilityContext,
  CapabilityHandler,
  CapabilityRegistration,
  CapabilityValidator,
  RegisterCapabilityOptions,
} from "./capabilityRegistry";

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
const capabilityRegistry = createDefaultCapabilityRegistry();
let responseSender: ResponseSender | null = null;
let bridgeActive = false;
const preReadyQueue: RouterEvent[] = [];
let processingTail: Promise<void> = Promise.resolve();
let lastResponseSendOutcome: ResponseSendOutcome | null = null;
let activePending = 0;
let droppedActiveRequests = 0;
const v2Pool = new ReassemblyPool();
type V2Peer = { endpoint: V2Endpoint; expires: number };
type V2Challenge = { epoch: string; expires: number };
const v2Peers = new Map<string, V2Peer>();
const v2Challenges = new Map<string, V2Challenge>();
const v2TokenSeed =
  Math.floor(Math.random() * 0x100000000)
    .toString(36)
    .padStart(7, "0") +
  Math.floor(Math.random() * 0x1000000)
    .toString(36)
    .padStart(5, "0");
let v2TokenSequence = 0;
let v2ControlSends = 0;
let v2Pruner: number | null = null;
let v2Sender: ((frame: string) => Promise<void>) | null = null;
let pendingV2Hello: string | null = null;

export const V2_MAX_PEERS = V2_MANIFEST.negotiation.max_peers;
export const V2_MAX_CHALLENGES = V2_MANIFEST.negotiation.max_challenges;
export const V2_LEASE_MS = V2_MANIFEST.negotiation.lease_ms;
const V2_CHALLENGE_MS = V2_MANIFEST.negotiation.challenge_ms;
const V2_MAX_CONTROL_SENDS = V2_MANIFEST.negotiation.max_control_sends;

export const MAX_PRE_READY_REQUESTS = 64;
export const MAX_ACTIVE_REQUESTS = 64;

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

function extractRequestId(message: string): string | undefined {
  const parsed = parseBridgeRequest(message);
  return parsed.ok ? parsed.request.request_id : parsed.requestId;
}

function schedule(event: RouterEvent): void {
  if (activePending >= MAX_ACTIVE_REQUESTS) {
    droppedActiveRequests += 1;
    const requestId = extractRequestId(event.message);
    console.warn(
      `[bridge] active queue full: code=BRIDGE_ACTIVE_QUEUE_FULL, requestId=${requestId ?? "unknown"}, ` +
        `activePending=${activePending}`
    );
    return;
  }
  activePending += 1;
  processingTail = processingTail
    .then(async () => {
      try {
        await handleBridgeScriptEvent(event);
      } finally {
        activePending -= 1;
      }
    })
    .catch(logUnexpectedRouterFailure);
}

export function enqueueOrHandle(event: ScriptEventCommandMessageAfterEvent): void {
  if (event.id === V2_MANIFEST.negotiation.event) {
    if (event.message.length > 100) return;
    if (!bridgeActive) {
      const match = /^MCBEWS2H\|([A-Za-z0-9_-]{1,24})$/.exec(event.message);
      if (match) pendingV2Hello = match[1];
    } else {
      handleV2Control(event.message);
    }
    return;
  }
  if (event.id === V2_MANIFEST.carrier.event) {
    if (!bridgeActive) return;
    pruneV2Peers();
    const match = /^MCBEWS2\|p\.([A-Za-z0-9_-]{1,24})\.[1-9][0-9]{0,9}\|/.exec(event.message);
    const peer = match && v2Peers.get(match[1]);
    if (peer) {
      peer.expires = Date.now() + V2_LEASE_MS;
      void peer.endpoint.receive(event.message, event.sourceType).catch(logUnexpectedRouterFailure);
    }
    return;
  }
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

export async function activateBridge(sender: ResponseSender, sendV2?: (frame: string) => Promise<void>): Promise<void> {
  if (bridgeActive) return;
  responseSender = sender;
  v2Sender = sendV2 ?? null;
  bridgeActive = true;
  if (pendingV2Hello) {
    const epoch = pendingV2Hello;
    pendingV2Hello = null;
    handleV2Control(`${V2_MANIFEST.negotiation.hello_prefix}|${epoch}`);
  }
  while (preReadyQueue.length > 0) {
    const event = preReadyQueue.shift();
    if (event) schedule(event);
  }
  await processingTail;
}

function sendV2Control(frame: string, onFailure?: () => void): boolean {
  const send = v2Sender;
  if (!send || v2ControlSends >= V2_MAX_CONTROL_SENDS) return false;
  v2ControlSends += 1;
  try {
    void send(frame)
      .catch((error: unknown) => {
        onFailure?.();
        logUnexpectedRouterFailure(error);
      })
      .finally(() => {
        v2ControlSends -= 1;
      });
  } catch (error) {
    v2ControlSends -= 1;
    onFailure?.();
    logUnexpectedRouterFailure(error);
    return false;
  }
  return true;
}

function retireV2Peer(epoch: string): void {
  const peer = v2Peers.get(epoch);
  if (!peer) return;
  v2Peers.delete(epoch);
  peer.endpoint.close();
}

function pruneV2Peers(): void {
  const now = Date.now();
  v2Pool.prune();
  for (const [attempt, challenge] of v2Challenges) {
    if (now >= challenge.expires) v2Challenges.delete(attempt);
  }
  for (const [epoch, peer] of v2Peers) {
    if (now >= peer.expires) retireV2Peer(epoch);
  }
}

function handleV2Control(frame: string): void {
  pruneV2Peers();
  const parts = frame.split("|");
  const attempt = parts[1];
  if (!attempt || !matches("[A-Za-z0-9_-]{1,24}", attempt)) return;
  if (parts[0] === "MCBEWS2H" && parts.length === 2) {
    if (v2ControlSends >= V2_MAX_CONTROL_SENDS - 1) {
      sendV2Control(`MCBEWS2N|${attempt}|-|busy`);
      return;
    }
    let challenge = v2Challenges.get(attempt);
    if (!challenge) {
      if (v2Challenges.size >= V2_MAX_CHALLENGES || v2Peers.size >= V2_MAX_PEERS) {
        sendV2Control(`MCBEWS2N|${attempt}|-|busy`);
        return;
      }
      if (v2TokenSequence >= Number.MAX_SAFE_INTEGER) {
        sendV2Control(`MCBEWS2N|${attempt}|-|busy`);
        return;
      }
      challenge = {
        epoch: `${v2TokenSeed}_${(++v2TokenSequence).toString(36)}`,
        expires: Date.now() + V2_CHALLENGE_MS,
      };
      v2Challenges.set(attempt, challenge);
    }
    const expected = challenge;
    if (
      !sendV2Control(`MCBEWS2N|${attempt}|${challenge.epoch}|2|${V2_MANIFEST.negotiation.features.join(",")}`, () => {
        if (v2Challenges.get(attempt) === expected) v2Challenges.delete(attempt);
      })
    )
      v2Challenges.delete(attempt);
    return;
  }
  if (parts[0] === "MCBEWS2C" && parts.length === 3) {
    const challenge = v2Challenges.get(attempt);
    if (!challenge || challenge.epoch !== parts[2]) return;
    v2Challenges.delete(attempt);
    if (v2Peers.size >= V2_MAX_PEERS) {
      sendV2Control(`MCBEWS2N|${attempt}|-|busy`);
      return;
    }
    const epoch = challenge.epoch;
    const send = v2Sender;
    if (!send) return;
    activateV2Bridge(epoch, send);
    if (!sendV2Control(`MCBEWS2N|${attempt}|${epoch}|ready`, () => retireV2Peer(epoch))) retireV2Peer(epoch);
    return;
  }
  if (parts.length !== 2) return;
  const peer = v2Peers.get(attempt);
  if (!peer) return;
  if (parts[0] === "MCBEWS2R") {
    peer.expires = Date.now() + V2_LEASE_MS;
    sendV2Control(`MCBEWS2N|${attempt}|lease`);
  } else if (parts[0] === "MCBEWS2X") {
    retireV2Peer(attempt);
  }
}

/** Return the endpoint for a specific admitted routing epoch. */
export function getV2Endpoint(epoch: string): V2Endpoint | null {
  pruneV2Peers();
  return v2Peers.get(epoch)?.endpoint ?? null;
}

/** Activate an explicitly agreed v2 epoch without changing the legacy router. */
export function activateV2Bridge(epoch: string, send: (frame: string) => Promise<void>): V2Endpoint {
  pruneV2Peers();
  if (v2Peers.has(epoch) || v2Peers.size >= V2_MAX_PEERS) throw new Error("v2 peer limit or duplicate epoch");
  const endpoint = new V2Endpoint({
    epoch,
    registry: capabilityRegistry,
    pool: v2Pool,
    send,
    onClose: () => {
      v2Peers.delete(epoch);
    },
    timeout: (callback) => {
      const handle = system.runTimeout(callback, 100);
      return () => system.clearRun(handle);
    },
    dispatch: async (method, payload, context) => {
      if (!capabilityHandler) return capabilityRegistry.dispatch(method, payload, context);
      try {
        return await capabilityHandler(method, payload, context);
      } catch {
        return { ok: false, error: { code: "CAPABILITY_FAILED", message: "capability handler failed" } };
      }
    },
  });
  v2Peers.set(epoch, { endpoint, expires: Date.now() + V2_LEASE_MS });
  if (v2Pruner === null) v2Pruner = system.runInterval(pruneV2Peers, 100);
  return endpoint;
}

// ---------------------------------------------------------------------------
// setCapabilityHandler
// ---------------------------------------------------------------------------

/**
 * Replace the built-in registry with one aggregate handler (legacy entry).
 *
 * While set, every capability goes to this handler and the built-in defaults
 * are not consulted. Call with a fresh reset to return to the registry path.
 */
export function setCapabilityHandler(fn: CapabilityHandler): void {
  capabilityHandler = fn;
}

/** Register a capability alongside the built-ins without editing the router. */
export function registerCapability(
  name: string,
  handler: CapabilityHandler,
  options?: RegisterCapabilityOptions
): CapabilityRegistration {
  return capabilityRegistry.register(name, handler, options);
}

/** Remove a registration if it is still the current entry for its name. */
export function unregisterCapability(registration: CapabilityRegistration): boolean {
  return capabilityRegistry.unregister(registration);
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
    resultPayload = await capabilityRegistry.dispatch(request.capability, request.payload, context);
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
export function _testingGetActiveQueueSize(): number {
  return activePending;
}

/** @internal */
export function _testingGetDroppedCount(): number {
  return droppedActiveRequests;
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
  for (const epoch of v2Peers.keys()) retireV2Peer(epoch);
  v2Challenges.clear();
  if (v2Pruner !== null) system.clearRun(v2Pruner);
  v2Pruner = null;
  v2Sender = null;
  pendingV2Hello = null;
  preReadyQueue.length = 0;
  responseSender = null;
  capabilityHandler = null;
  capabilityRegistry.clear();
  registerDefaultCapabilities(capabilityRegistry);
  bridgeActive = false;
  isBridgeRouterRegistered = false;
  processingTail = Promise.resolve();
  lastResponseSendOutcome = null;
  activePending = 0;
  droppedActiveRequests = 0;
}
