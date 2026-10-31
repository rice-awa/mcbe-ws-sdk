import type { CapabilityContext, CapabilityRegistry, V2CallOptions, V2CapabilityClient } from "../capabilityRegistry";
import { isObject, Message, V2ProtocolError } from "./codec";
import { fragment, ReassemblyPool } from "./transport";

type Timeout = (callback: () => void) => () => void;
type Pending = { resolve: (value: Message) => void; reject: (reason: Error) => void };

export type V2EndpointOptions = {
  epoch: string;
  registry: CapabilityRegistry;
  send: (frame: string) => Promise<void>;
  timeout: Timeout;
  pool?: ReassemblyPool;
  maxPending?: number;
  maxActive?: number;
  dispatch?: (method: string, params: Message, context: CapabilityContext) => Promise<Message>;
};

/** A bounded endpoint whose reader pairs replies before dispatching capabilities. */
export class V2Endpoint implements V2CapabilityClient {
  readonly epoch: string;
  private readonly pool: ReassemblyPool;
  private readonly pending = new Map<string, Pending>();
  private readonly methods = new Set<string>();
  private readonly tasks = new Set<Promise<void>>();
  private sequence = 0;
  private sending = 0;
  private closed = false;

  constructor(private readonly options: V2EndpointOptions) {
    if (!/^[A-Za-z0-9_-]{1,24}$/.test(options.epoch)) throw new Error("invalid epoch");
    this.epoch = options.epoch;
    this.pool = options.pool ?? new ReassemblyPool();
  }

  private nextId(): string {
    if (this.closed) throw new Error("endpoint closed");
    this.sequence += 1;
    if (this.sequence > 9999999999) throw new Error("epoch exhausted");
    return `a.${this.epoch}.${this.sequence}`;
  }

  private metadata(options: V2CallOptions): Message {
    const meta = { ...options.metadata };
    if (Object.keys(meta).some((key) => key.startsWith("sdk."))) throw new Error("sdk.* metadata is reserved");
    if (options.traceId) meta["sdk.trace_id"] = options.traceId;
    return meta;
  }

  private async send(message: Message, cancelled: () => boolean = () => false): Promise<void> {
    if (this.sending >= (this.options.maxActive ?? 64)) throw new Error("send limit exceeded");
    this.sending += 1;
    try {
      for (const frame of fragment(message, this.nextId(), "up")) {
        if (this.closed || cancelled()) throw new Error("endpoint closed or request cancelled");
        await this.options.send(frame);
      }
    } finally {
      this.sending -= 1;
    }
  }

  async request(method: string, params: unknown, options: V2CallOptions = {}): Promise<Message> {
    if (this.pending.size >= (this.options.maxPending ?? 128)) throw new Error("pending limit exceeded");
    const id = this.nextId();
    let entry!: Pending;
    const response = new Promise<Message>((resolve, reject) => {
      entry = { resolve, reject };
    });
    void response.catch(() => {});
    this.pending.set(id, entry);
    let cancelTimeout = () => {};
    let cancelled = false;
    try {
      cancelTimeout = this.options.timeout(() => entry.reject(new Error("request timed out")));
      const sent = this.send(
        { v: "MCBEWS/2", kind: "request", id, method, params, meta: this.metadata(options) },
        () => cancelled
      );
      await Promise.race([sent, response.then(() => undefined)]);
      const result = await response;
      if (isObject(result.error)) throw new Error(`${String(result.error.code)}: ${String(result.error.message)}`);
      return result;
    } finally {
      cancelled = true;
      cancelTimeout();
      this.pending.delete(id);
    }
  }

  async notify(method: string, params: unknown, options: V2CallOptions = {}): Promise<void> {
    await this.send({ v: "MCBEWS/2", kind: "notification", method, params, meta: this.metadata(options) });
  }

  async receive(frame: string, source: string): Promise<void> {
    if (this.closed || !frame.startsWith(`MCBEWS2|p.${this.epoch}.`)) return;
    const message = this.pool.receive(this.epoch, source, frame, "down");
    if (!message) return;
    if (message.kind === "response") {
      if (typeof message.id === "string" && message.id.startsWith(`a.${this.epoch}.`)) {
        this.pending.get(message.id)?.resolve(message);
      }
      return;
    }
    if (message.kind === "request" && !(message.id as string).startsWith(`p.${this.epoch}.`)) return;
    const method = message.method as string;
    if (this.methods.has(method) || this.methods.size >= (this.options.maxActive ?? 64)) {
      if (message.kind === "request") await this.replyError(message, "LIMIT_EXCEEDED", "ordering group busy");
      return;
    }
    this.methods.add(method);
    const task = this.dispatch(message, source).finally(() => {
      this.methods.delete(method);
      this.tasks.delete(task);
    });
    this.tasks.add(task);
    void task.catch(() => {});
  }

  private async dispatch(message: Message, source: string): Promise<void> {
    if (!isObject(message.params)) {
      if (message.kind === "request") await this.replyError(message, "INVALID_PARAMS", "parameters must be an object");
      return;
    }
    const meta = isObject(message.meta) ? message.meta : {};
    const traceId = typeof meta["sdk.trace_id"] === "string" ? meta["sdk.trace_id"] : undefined;
    const client: V2CapabilityClient = {
      request: (method, params, options = {}) => this.request(method, params, { traceId, ...options }),
      notify: (method, params, options = {}) => this.notify(method, params, { traceId, ...options }),
    };
    const context: CapabilityContext = {
      caller: { kind: "server", source },
      requestVersion: 2,
      metadata: meta,
      client,
    };
    const result = this.options.dispatch
      ? await this.options.dispatch(message.method as string, message.params, context)
      : await this.options.registry.dispatch(message.method as string, message.params, context);
    if (message.kind === "notification" || this.closed) return;
    if (result.ok === false && isObject(result.error)) {
      const codes: Record<string, string> = {
        UNSUPPORTED_CAPABILITY: "UNSUPPORTED_METHOD",
        INVALID_REQUEST: "INVALID_PARAMS",
        CAPABILITY_FAILED: "HANDLER_FAILED",
      };
      const code = codes[String(result.error.code)];
      if (code) {
        await this.replyError(message, code, "capability failed");
        return;
      }
    }
    try {
      await this.send({ v: "MCBEWS/2", kind: "response", id: message.id, result, meta });
    } catch (error) {
      if (!(error instanceof V2ProtocolError)) throw error;
      await this.replyError(message, "HANDLER_FAILED", "invalid handler result");
    }
  }

  private async replyError(message: Message, code: string, detail: string): Promise<void> {
    await this.send({
      v: "MCBEWS/2",
      kind: "response",
      id: message.id,
      error: { code, message: detail },
      meta: message.meta ?? {},
    });
  }

  /** Wait for admitted handlers; useful for graceful shutdown and tests. */
  async drain(): Promise<void> {
    await Promise.allSettled([...this.tasks]);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    for (const entry of this.pending.values()) entry.reject(new Error("endpoint closed"));
    this.pending.clear();
    this.pool.close(this.epoch);
  }
}
