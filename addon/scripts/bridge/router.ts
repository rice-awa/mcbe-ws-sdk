import type { ScriptEventCommandMessageAfterEvent } from "@minecraft/server";
import { system } from "@minecraft/server";

import { createDefaultCapabilityRegistry, registerDefaultCapabilities } from "./capabilities";
import type {
  CapabilityContext,
  CapabilityHandler,
  CapabilityRegistration,
  RegisterCapabilityOptions,
} from "./capabilityRegistry";
import { MANIFEST as V2_MANIFEST } from "./v2/assets";
import { matches } from "./v2/codec";
import { V2Endpoint } from "./v2/runtime";
import type { StreamHandler } from "./v2/streams";
import { ReassemblyPool } from "./v2/transport";

export type {
  CapabilityContext,
  CapabilityHandler,
  CapabilityRegistration,
  CapabilityValidator,
  RegisterCapabilityOptions,
} from "./capabilityRegistry";

/** Script-event namespace owned by this bridge; anything else is not ours. */
const PROTOCOL_EVENT_NAMESPACE = "mcbews:";

let isBridgeRouterRegistered = false;
const capabilityRegistry = createDefaultCapabilityRegistry();
let bridgeActive = false;
let v2Sender: ((frame: string) => Promise<void>) | null = null;
let pendingV2Hello: string | null = null;

const v2Pool = new ReassemblyPool();
const v2StreamHandlers = new Map<string, StreamHandler>();
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

export const V2_MAX_PEERS = V2_MANIFEST.negotiation.max_peers;
export const V2_MAX_CHALLENGES = V2_MANIFEST.negotiation.max_challenges;
export const V2_LEASE_MS = V2_MANIFEST.negotiation.lease_ms;
const V2_CHALLENGE_MS = V2_MANIFEST.negotiation.challenge_ms;
const V2_MAX_CONTROL_SENDS = V2_MANIFEST.negotiation.max_control_sends;
const V2_HELLO_PATTERN = /^MCBEWS2H\|([A-Za-z0-9_-]{1,24})$/;

function logUnexpectedRouterFailure(error: unknown): void {
  console.error("[bridge] unexpected router failure", error instanceof Error ? error.constructor.name : typeof error);
}

function logUnsupportedEvent(event: ScriptEventCommandMessageAfterEvent): void {
  console.warn(
    `[bridge] rejected unsupported protocol event: expected=MCBEWS/2, reason=UNSUPPORTED_PROTOCOL, event=${event.id}`
  );
}

/**
 * Accept the transport's script events.
 *
 * Before {@link activateBridge} only one sanitized hello is retained; business
 * frames are dropped without buffering. After activation, control frames drive
 * MCBEWS/2 negotiation and carrier frames route to an admitted peer by epoch.
 */
export function enqueueOrHandle(event: ScriptEventCommandMessageAfterEvent): void {
  if (event.id === V2_MANIFEST.negotiation.event) {
    if (event.message.length > 100) return;
    if (!bridgeActive) {
      const match = V2_HELLO_PATTERN.exec(event.message);
      if (match) pendingV2Hello = match[1];
      return;
    }
    handleV2Control(event.message);
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

  if (event.id === V2_MANIFEST.admission.reject_notice) {
    // The server classified this session as unusable. The reason is the
    // manifest's admission token; never echo it back.
    console.warn(`[bridge] admission rejected by server: reason=${event.message} expected=MCBEWS/2`);
    return;
  }

  // Any other event in the bridge namespace is a retired or unknown protocol
  // message. Log it by id without decoding, answering, or dispatching it.
  if (event.id.startsWith(PROTOCOL_EVENT_NAMESPACE)) {
    logUnsupportedEvent(event);
  }
}

/**
 * Open the world-ready gate and adopt the retained hello, if any.
 *
 * No business event is replayed: anything that arrived earlier was dropped.
 */
export async function activateBridge(sendV2Frame: (frame: string) => Promise<void>): Promise<void> {
  if (bridgeActive) return;
  v2Sender = sendV2Frame;
  bridgeActive = true;
  const attempt = pendingV2Hello;
  pendingV2Hello = null;
  if (attempt) handleV2Control(`${V2_MANIFEST.negotiation.hello_prefix}|${attempt}`);
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
    createV2Peer(epoch, send);
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

/** Register a stream producer for current and future negotiated peers. */
export function registerV2Stream(name: string, handler: StreamHandler): () => void {
  if (v2StreamHandlers.has(name)) throw new Error(`stream already registered: ${name}`);
  const registered: StreamHandler = (...args) => handler(...args);
  v2StreamHandlers.set(name, registered);
  return () => {
    if (v2StreamHandlers.get(name) === registered) v2StreamHandlers.delete(name);
  };
}

/**
 * Create and admit a negotiated peer.
 *
 * Callers reach this only through a fresh challenge/confirmation pair; there is
 * no exported manual-activation path.
 */
function createV2Peer(epoch: string, send: (frame: string) => Promise<void>): V2Endpoint {
  pruneV2Peers();
  if (v2Peers.has(epoch) || v2Peers.size >= V2_MAX_PEERS) throw new Error("v2 peer limit or duplicate epoch");
  const endpoint = new V2Endpoint({
    epoch,
    registry: capabilityRegistry,
    pool: v2Pool,
    streamHandlers: v2StreamHandlers,
    send,
    onClose: () => {
      v2Peers.delete(epoch);
    },
    timeout: (callback, milliseconds = 5000) => {
      const handle = system.runTimeout(callback, Math.max(1, Math.ceil(milliseconds / 50)));
      return () => system.clearRun(handle);
    },
  });
  v2Peers.set(epoch, { endpoint, expires: Date.now() + V2_LEASE_MS });
  if (v2Pruner === null) v2Pruner = system.runInterval(pruneV2Peers, 100);
  return endpoint;
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

/** Subscribe the script-event router once per Bedrock script host. */
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
export function _testingGetPendingHello(): string | null {
  return pendingV2Hello;
}

/** @internal */
export function _testingGetPeerCount(): number {
  return v2Peers.size;
}

/** @internal */
export async function _testingFlush(): Promise<void> {
  await Promise.allSettled([...v2Peers.values()].map((peer) => peer.endpoint.drain()));
}

/** @internal */
export function _testingReset(): void {
  for (const epoch of [...v2Peers.keys()]) retireV2Peer(epoch);
  v2Challenges.clear();
  v2StreamHandlers.clear();
  if (v2Pruner !== null) system.clearRun(v2Pruner);
  v2Pruner = null;
  v2Sender = null;
  pendingV2Hello = null;
  capabilityRegistry.clear();
  registerDefaultCapabilities(capabilityRegistry);
  bridgeActive = false;
  isBridgeRouterRegistered = false;
}
