/** Bounded request-stream lifecycle, independent of the frame carrier. */
import type { CapabilityContext, V2RequestOptions } from "../capabilityRegistry";
import { V2RequestCancellation } from "../capabilityRegistry";
import { utf8ByteLength } from "../chunking";
import { MANIFEST } from "./assets";
import { isObject, Message } from "./codec";

export type StreamHandler = (method: string, params: Message, context: CapabilityContext) => AsyncIterable<unknown>;

export type StreamLimits = {
  max_active: number;
  buffer_items: number;
  buffer_bytes: number;
  connection_bytes: number;
  idle_ms: number;
  tombstones: number;
  tombstone_ms: number;
};
export type StreamTimeout = (callback: () => void, milliseconds?: number) => () => void;

export class StreamError extends Error {
  constructor(
    readonly code: string,
    message = code
  ) {
    super(message);
  }
}

type Queued = { item: unknown; bytes: number };
type Waiter = { resolve: (result: IteratorResult<unknown>) => void; reject: (error: unknown) => void };

/** Consume each logical item once; return() cancels a partially consumed stream. */
export class IncomingStream implements AsyncIterableIterator<unknown> {
  readonly cancellation = new V2RequestCancellation();
  private readonly queue: Queued[] = [];
  private waiter?: Waiter;
  private terminal: "open" | "end" | StreamError = "open";
  private sequence = 0;
  private byteSize = 0;
  private cancelTimeout = () => {};
  private unsubscribe = () => {};

  constructor(
    readonly id: string,
    private readonly owner: StreamManager
  ) {}

  get active(): boolean {
    return this.terminal === "open";
  }

  get bufferedItems(): number {
    return this.queue.length;
  }

  get bufferedBytes(): number {
    return this.byteSize;
  }

  start(options: V2RequestOptions): void {
    this.unsubscribe = options.cancellation?.onCancel(() => this.cancel()) ?? (() => {});
    if (!this.active) this.unsubscribe();
    else this.refresh();
  }

  private refresh(): void {
    this.cancelTimeout();
    this.cancelTimeout = this.owner.timeout(() => this.fail(new StreamError("TIMEOUT"), true));
  }

  [Symbol.asyncIterator](): AsyncIterableIterator<unknown> {
    return this;
  }

  async next(): Promise<IteratorResult<unknown>> {
    const queued = this.queue.shift();
    if (queued) {
      this.byteSize -= queued.bytes;
      this.owner.releaseBytes(queued.bytes);
      if (!this.active && this.queue.length === 0) this.owner.release(this.id);
      return { done: false, value: queued.item };
    }
    if (this.terminal instanceof StreamError) throw this.terminal;
    if (this.terminal === "end") return { done: true, value: undefined };
    return new Promise<IteratorResult<unknown>>((resolve, reject) => {
      this.waiter = { resolve, reject };
    });
  }

  async return(): Promise<IteratorResult<unknown>> {
    this.cancel();
    return { done: true, value: undefined };
  }

  cancel(): void {
    this.fail(new StreamError("CANCELLED"), true);
    this.clear();
    this.owner.release(this.id);
  }

  receive(message: Message): void {
    if (!this.active) return;
    if (message.seq !== this.sequence) {
      this.fail(new StreamError("INVALID_MESSAGE", "stream sequence mismatch"), true);
      return;
    }
    if (message.kind === "stream_error") {
      const error = message.error as { code: string; message: string };
      this.fail(new StreamError(error.code, error.message));
      return;
    }
    if (message.kind === "stream_end") {
      this.terminal = "end";
      this.cancelTimeout();
      this.unsubscribe();
      this.waiter?.resolve({ done: true, value: undefined });
      this.waiter = undefined;
      if (this.queue.length === 0) this.owner.release(this.id);
      return;
    }
    this.sequence += 1;
    this.refresh();
    if (this.waiter) {
      this.waiter.resolve({ done: false, value: message.item });
      this.waiter = undefined;
      return;
    }
    const bytes = utf8ByteLength(JSON.stringify(message.item));
    const limits = this.owner.limits;
    if (
      this.queue.length >= limits.buffer_items ||
      this.byteSize + bytes > limits.buffer_bytes ||
      !this.owner.reserveBytes(bytes)
    ) {
      this.fail(new StreamError("LIMIT_EXCEEDED", "stream consumer buffer full"), true);
      return;
    }
    this.queue.push({ item: message.item, bytes });
    this.byteSize += bytes;
  }

  fail(error: StreamError, notify = false): void {
    if (!this.active) return;
    this.terminal = error;
    this.cancellation.cancel();
    this.cancelTimeout();
    this.unsubscribe();
    this.clear();
    this.waiter?.reject(error);
    this.waiter = undefined;
    this.owner.release(this.id);
    if (notify) this.owner.cancelRemote(this.id);
  }

  dispose(): void {
    this.fail(new StreamError("CANCELLED", "endpoint closed"));
    this.clear();
    this.owner.release(this.id);
  }

  private clear(): void {
    this.queue.length = 0;
    this.owner.releaseBytes(this.byteSize);
    this.byteSize = 0;
  }
}

type Production = {
  cancellation: V2RequestCancellation;
  sequence: number;
  terminal: boolean;
  iterator?: AsyncIterator<unknown>;
};

type StreamServices = {
  epoch: string;
  handlers: Map<string, StreamHandler>;
  nextId: () => string;
  send: (message: Message, cancelled?: () => boolean) => Promise<void>;
  timeout: StreamTimeout;
  context: (message: Message, source: string, cancellation: V2RequestCancellation) => CapabilityContext;
  limits?: Partial<StreamLimits>;
};

export class StreamManager {
  readonly limits: StreamLimits;
  private readonly incoming = new Map<string, IncomingStream>();
  private readonly producers = new Map<string, Production>();
  private readonly tombstones = new Map<string, number>();
  private readonly tasks = new Set<Promise<void>>();
  private readonly controls = new Set<Promise<void>>();
  private bytes = 0;
  private closed = false;
  private readonly cancellation = new V2RequestCancellation();

  constructor(private readonly services: StreamServices) {
    this.limits = { ...MANIFEST.streams } as StreamLimits;
    for (const [key, value] of Object.entries(services.limits ?? {})) {
      if (
        !(key in this.limits) ||
        !Number.isSafeInteger(value) ||
        value <= 0 ||
        value > this.limits[key as keyof StreamLimits]
      ) {
        throw new Error(`invalid stream limit: ${key}`);
      }
      this.limits[key as keyof StreamLimits] = value;
    }
  }

  get stats(): { active: number; bufferedItems: number; bufferedBytes: number; producers: number; tasks: number } {
    return {
      active: this.incoming.size + this.producers.size,
      bufferedItems: [...this.incoming.values()].reduce((count, stream) => count + stream.bufferedItems, 0),
      bufferedBytes: this.bytes,
      producers: this.producers.size,
      tasks: this.tasks.size + this.controls.size,
    };
  }

  timeout(callback: () => void): () => void {
    return this.services.timeout(callback, this.limits.idle_ms);
  }

  reserveBytes(bytes: number): boolean {
    if (this.bytes + bytes > this.limits.connection_bytes) return false;
    this.bytes += bytes;
    return true;
  }

  releaseBytes(bytes: number): void {
    this.bytes -= bytes;
  }

  release(id: string): void {
    this.incoming.delete(id);
  }

  async open(method: string, params: unknown, meta: Message, options: V2RequestOptions): Promise<IncomingStream> {
    if (this.closed) throw new StreamError("CANCELLED", "endpoint closed");
    if (options.cancellation?.cancelled) throw new StreamError("CANCELLED");
    if (this.stats.active >= this.limits.max_active) throw new StreamError("LIMIT_EXCEEDED");
    const id = this.services.nextId();
    const stream = new IncomingStream(id, this);
    this.incoming.set(id, stream);
    stream.start(options);
    try {
      await this.operation(
        this.services.send({ v: "MCBEWS/2", kind: "stream_open", id, method, params, meta }, () => !stream.active),
        stream.cancellation
      );
    } catch (error) {
      stream.fail(error instanceof StreamError ? error : new StreamError("HANDLER_FAILED", "stream send failed"), true);
    }
    return stream;
  }

  receive(message: Message, source: string): void {
    const id = message.id as string;
    if (message.kind === "stream_open") {
      if (!id.startsWith(`p.${this.services.epoch}.`)) return;
      this.openProducer(message, source);
    } else if (message.kind === "stream_cancel") {
      const producer = this.producers.get(id);
      if (!producer) return;
      this.finishProducer(id, producer);
    } else if (id.startsWith(`a.${this.services.epoch}.`)) {
      this.incoming.get(id)?.receive(message);
    }
  }

  private openProducer(message: Message, source: string): void {
    const now = Date.now();
    for (const [key, expires] of this.tombstones) if (now >= expires) this.tombstones.delete(key);
    const id = message.id as string;
    if (this.closed || this.producers.has(id) || this.tombstones.has(id)) return;
    if (
      this.producers.size + this.tombstones.size >= this.limits.tombstones ||
      this.stats.active >= this.limits.max_active
    ) {
      if (this.producers.size + this.tombstones.size < this.limits.tombstones) {
        this.tombstones.set(id, now + this.limits.tombstone_ms);
      }
      this.control({
        v: "MCBEWS/2",
        kind: "stream_error",
        id,
        seq: 0,
        error: { code: "LIMIT_EXCEEDED", message: "stream capacity full" },
      });
      return;
    }
    const production: Production = { cancellation: new V2RequestCancellation(), sequence: 0, terminal: false };
    this.producers.set(id, production);
    const handler = this.services.handlers.get(message.method as string);
    const task = this.produce(message, source, production, handler).finally(() => {
      this.tasks.delete(task);
    });
    this.tasks.add(task);
    void task.catch(() => {});
  }

  private async produce(
    message: Message,
    source: string,
    production: Production,
    handler?: StreamHandler
  ): Promise<void> {
    const id = message.id as string;
    try {
      if (!handler) throw new StreamError("UNSUPPORTED_METHOD");
      if (!isObject(message.params)) throw new StreamError("INVALID_PARAMS", "parameters must be an object");
      const context = this.services.context(message, source, production.cancellation);
      production.iterator = handler(message.method as string, message.params, context)[Symbol.asyncIterator]();
      while (!production.terminal) {
        const item = await this.operation(production.iterator.next(), production.cancellation);
        if (production.terminal) break;
        if (item.done) {
          await this.operation(
            this.services.send(
              { v: "MCBEWS/2", kind: "stream_end", id, seq: production.sequence },
              () => production.terminal
            ),
            production.cancellation
          );
          break;
        }
        await this.operation(
          this.services.send(
            { v: "MCBEWS/2", kind: "stream_item", id, seq: production.sequence, item: item.value },
            () => production.terminal
          ),
          production.cancellation
        );
        production.sequence += 1;
      }
    } catch (error) {
      if (!production.terminal && !this.closed) {
        const code = error instanceof StreamError ? error.code : "HANDLER_FAILED";
        await this.operation(
          this.services.send(
            { v: "MCBEWS/2", kind: "stream_error", id, seq: production.sequence, error: { code, message: code } },
            () => production.terminal
          ),
          production.cancellation
        ).catch(() => {});
      }
    } finally {
      this.finishProducer(id, production);
    }
  }

  private finishProducer(id: string, production: Production): void {
    if (production.terminal) return;
    production.terminal = true;
    production.cancellation.cancel();
    this.producers.delete(id);
    if (!this.closed) this.tombstones.set(id, Date.now() + this.limits.tombstone_ms);
    // An async generator must cooperate with cancellation while awaiting external work.
    void production.iterator?.return?.().catch(() => {});
  }

  private operation<T>(promise: Promise<T>, cancellation?: V2RequestCancellation): Promise<T> {
    return new Promise<T>((resolve, reject) => {
      let active = true;
      let cancelTimeout = () => {};
      let unsubscribe = () => {};
      const finish = (complete: () => void) => {
        if (!active) return;
        active = false;
        cancelTimeout();
        unsubscribe();
        complete();
      };
      cancelTimeout = this.timeout(() => finish(() => reject(new StreamError("TIMEOUT"))));
      unsubscribe = cancellation?.onCancel(() => finish(() => reject(new StreamError("CANCELLED")))) ?? unsubscribe;
      if (!active) {
        cancelTimeout();
        unsubscribe();
      }
      void promise.then(
        (value) => finish(() => resolve(value)),
        (error: unknown) => finish(() => reject(error))
      );
    });
  }

  cancelRemote(id: string): void {
    this.control({ v: "MCBEWS/2", kind: "stream_cancel", id });
  }

  private control(message: Message): void {
    if (this.closed || this.controls.size >= this.limits.max_active) return;
    const cancellation = new V2RequestCancellation();
    const unsubscribe = this.cancellation.onCancel(() => cancellation.cancel());
    const task = this.operation(
      this.services.send(message, () => cancellation.cancelled),
      cancellation
    )
      .catch(() => {})
      .finally(() => {
        cancellation.cancel();
        unsubscribe();
        this.controls.delete(task);
      });
    this.controls.add(task);
  }

  async drain(): Promise<void> {
    await Promise.allSettled([...this.tasks, ...this.controls]);
  }

  close(): void {
    if (this.closed) return;
    this.closed = true;
    this.cancellation.cancel();
    for (const stream of this.incoming.values()) stream.dispose();
    for (const [id, producer] of this.producers) this.finishProducer(id, producer);
    this.tombstones.clear();
  }
}
