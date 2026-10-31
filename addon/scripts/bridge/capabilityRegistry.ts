/** Name-based capability registry shared by the router and hosts. */

export type CapabilityContext = {
  caller: { kind: "server"; source?: string };
  requestVersion: 1 | 2;
  metadata?: Record<string, unknown>;
  client?: V2CapabilityClient;
};

export type V2CallOptions = { metadata?: Record<string, unknown>; traceId?: string };
export type V2CapabilityClient = {
  request(method: string, params: unknown, options?: V2CallOptions): Promise<Record<string, unknown>>;
  notify(method: string, params: unknown, options?: V2CallOptions): Promise<void>;
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
