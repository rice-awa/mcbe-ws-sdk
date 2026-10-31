/** Name-based capability registry shared by the router and hosts. */
import type { IncomingStream } from "./v2/streams";

export type CapabilityContext = {
  caller: { kind: "server"; source?: string };
  requestVersion: 1 | 2;
  metadata?: Record<string, unknown>;
  client?: V2CapabilityClient;
  cancellation?: V2RequestCancellation;
};

export type V2CallOptions = { metadata?: Record<string, unknown>; traceId?: string; require?: readonly string[] };
export type V2RequestOptions = V2CallOptions & { cancellation?: V2RequestCancellation };

/** Use a separate handle per request for independent cancellation. */
export class V2RequestCancellation {
  private readonly listeners = new Set<() => void>();
  private stopped = false;

  get cancelled(): boolean {
    return this.stopped;
  }

  /** Cancel locally; a request already delivered may still execute. */
  cancel(): void {
    if (this.stopped) return;
    this.stopped = true;
    for (const listener of this.listeners) listener();
    this.listeners.clear();
  }

  /** Register a cancellation callback and return its unsubscribe function. */
  onCancel(listener: () => void): () => void {
    if (this.stopped) {
      listener();
      return () => {};
    }
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }
}

export type V2CapabilityClient = {
  request(method: string, params: unknown, options?: V2RequestOptions): Promise<Record<string, unknown>>;
  notify(method: string, params: unknown, options?: V2CallOptions): Promise<void>;
  stream(method: string, params: unknown, options?: V2RequestOptions): Promise<IncomingStream>;
};

export type CapabilityHandler = (
  capability: string,
  payload: Record<string, unknown>,
  context: CapabilityContext
) => Record<string, unknown> | Promise<Record<string, unknown>>;

/** Return a message to reject the payload, or null/undefined to accept it. */
export type CapabilityValidator = (
  payload: Record<string, unknown>,
  context: CapabilityContext
) => string | null | undefined;

/** Handle returned by {@link CapabilityRegistry.register}. */
export type CapabilityRegistration = {
  readonly name: string;
};

export type RegisterCapabilityOptions = {
  /** Explicitly replace an existing registration with the same name. */
  replace?: boolean;
  validate?: CapabilityValidator;
};

type Entry = {
  registration: CapabilityRegistration;
  handler: CapabilityHandler;
  validate?: CapabilityValidator;
};

function failure(code: string, message: string): Record<string, unknown> {
  return { ok: false, error: { code, message } };
}

/**
 * Maps one capability name to one validator and one handler.
 *
 * Dispatch captures the current entry before awaiting it: unregistering or
 * replacing it mid-call does not change that call, and later calls observe the
 * new state. Validators run before handlers; unknown names, invalid payloads
 * and handler failures return structured results instead of throwing.
 */
export class CapabilityRegistry {
  private readonly entries = new Map<string, Entry>();

  register(name: string, handler: CapabilityHandler, options: RegisterCapabilityOptions = {}): CapabilityRegistration {
    if (!name.trim()) {
      throw new Error("capability name must be a non-empty string");
    }
    if (this.entries.has(name) && !options.replace) {
      throw new Error(`capability already registered: ${name} (pass replace: true to override)`);
    }
    const registration: CapabilityRegistration = { name };
    this.entries.set(name, { registration, handler, validate: options.validate });
    return registration;
  }

  /** Remove the registration only if it is still the current entry. */
  unregister(registration: CapabilityRegistration): boolean {
    const entry = this.entries.get(registration.name);
    if (!entry || entry.registration !== registration) {
      return false;
    }
    this.entries.delete(registration.name);
    return true;
  }

  has(name: string): boolean {
    return this.entries.has(name);
  }

  names(): string[] {
    return [...this.entries.keys()];
  }

  clear(): void {
    this.entries.clear();
  }

  async dispatch(
    name: string,
    payload: Record<string, unknown>,
    context: CapabilityContext
  ): Promise<Record<string, unknown>> {
    const entry = this.entries.get(name);
    if (!entry) {
      return failure("UNSUPPORTED_CAPABILITY", `unsupported capability: ${name}`);
    }
    try {
      if (entry.validate) {
        const message = entry.validate(payload, context);
        if (message) {
          return failure("INVALID_REQUEST", message);
        }
      }
      return await entry.handler(name, payload, context);
    } catch {
      return failure("CAPABILITY_FAILED", "capability handler failed");
    }
  }
}
