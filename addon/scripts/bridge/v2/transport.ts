import { utf8ByteLength } from "../chunking";
import { FragmentAssembler } from "../reassembly";
import { MANIFEST } from "./assets";
import { decode, encode, fail, matches, Message } from "./codec";

export type Limits = Record<string, number>;
export function limits(overrides: Limits = {}): Limits {
  const result = { ...MANIFEST.limits };
  for (const [key, value] of Object.entries(overrides)) {
    if (
      !Object.prototype.hasOwnProperty.call(result, key) ||
      !Number.isInteger(value) ||
      value < 1 ||
      value > MANIFEST.limits[key]
    )
      fail("LIMIT_EXCEEDED");
    result[key] = value;
  }
  return result;
}
export type Direction = "down" | "up";
const commandPrefix = "scriptevent " + MANIFEST.carrier.event + " ";
function identity(value: string): void {
  if (!matches("[A-Za-z0-9_.:-]{1,128}", value)) fail("INVALID_FRAGMENT");
}
function transportId(tid: string, direction: Direction): void {
  if (
    !["up", "down"].includes(direction) ||
    !matches(MANIFEST.id_pattern, tid) ||
    tid[0] !== (direction === "down" ? "p" : "a")
  )
    fail("INVALID_FRAGMENT");
}
function scalar(value: string): boolean {
  for (const ch of value) {
    const n = ch.codePointAt(0)!;
    if (n >= 0xd800 && n <= 0xdfff) return false;
  }
  return true;
}
function fits(frame: string, direction: Direction, cfg: Limits): boolean {
  return direction === "down"
    ? utf8ByteLength(commandPrefix + frame) <= cfg.command_bytes
    : Array.from(frame).length <= cfg.upstream_code_points && utf8ByteLength(frame) <= cfg.upstream_bytes;
}
export function fragment(message: unknown, tid: string, direction: Direction, overrides: Limits = {}): string[] {
  const cfg = limits(overrides);
  transportId(tid, direction);
  const wire = encode(message, cfg);
  if (utf8ByteLength(wire) > cfg.message_bytes) fail("LIMIT_EXCEEDED");
  const header = MANIFEST.carrier.prefix + "|" + tid + "|" + cfg.chunks + "/" + cfg.chunks + "|";
  const parts: string[] = [];
  let current = "";
  for (const ch of wire) {
    if (fits(header + current + ch, direction, cfg)) current += ch;
    else {
      if (!current || !fits(header + ch, direction, cfg)) fail("LIMIT_EXCEEDED");
      parts.push(current);
      current = ch;
    }
  }
  parts.push(current);
  if (parts.length > cfg.chunks) fail("LIMIT_EXCEEDED");
  const frames = parts.map(
    (content, i) => MANIFEST.carrier.prefix + "|" + tid + "|" + (i + 1) + "/" + parts.length + "|" + content
  );
  if (!frames.every((f) => fits(f, direction, cfg))) fail("LIMIT_EXCEEDED");
  return frames;
}
export function downstreamCommands(message: unknown, tid: string, overrides: Limits = {}): string[] {
  return fragment(message, tid, "down", overrides).map((frame) => commandPrefix + frame);
}
type Owner = { connection: string; source: string; tid: string };

/** Share this pool across runtime connections; lifecycle owners call prune and close. */
export class ReassemblyPool {
  readonly cfg: Limits;
  private readonly core: FragmentAssembler<string>;
  private readonly owners = new Map<string, Owner>();
  private readonly done = new Map<string, { connection: string; expires: number }>();
  constructor(
    overrides: Limits = {},
    private readonly now: () => number = Date.now
  ) {
    this.cfg = limits(overrides);
    this.core = new FragmentAssembler<string>(
      {
        ttlMs: this.cfg.ttl_ms,
        maxBuffers: this.cfg.global_buffers,
        maxChunksPerMessage: this.cfg.chunks,
        maxMessageBytes: this.cfg.message_bytes,
        maxTotalBufferBytes: this.cfg.global_bytes,
      },
      now
    );
  }
  get stats(): { buffers: number; bytes: number; tombstones: number } {
    return { buffers: this.core.bufferCount, bytes: this.core.bufferedBytes, tombstones: this.done.size };
  }
  private drop(key: string, now = this.now()): void {
    const owner = this.owners.get(key);
    this.owners.delete(key);
    this.core.drop(key);
    if (owner) this.done.set(key, { connection: owner.connection, expires: now + this.cfg.ttl_ms });
  }
  prune(): void {
    this.pruneAt(this.now());
  }
  private pruneAt(now: number): void {
    for (const [key, state] of this.done) if (now >= state.expires) this.done.delete(key);
    const before = Array.from(this.core.buffers.keys());
    this.core.pruneExpired(now);
    for (const key of before) if (!this.core.buffers.has(key)) this.drop(key, now);
  }
  cancel(connection: string, source: string, tid: string): void {
    this.drop(JSON.stringify([connection, source, tid]));
  }
  close(connection: string): void {
    for (const [key, owner] of this.owners) if (owner.connection === connection) this.drop(key);
    for (const [key, state] of this.done) if (state.connection === connection) this.done.delete(key);
  }
  receive(connection: string, source: string, frame: string, direction: Direction): Message | null {
    const now = this.now();
    this.pruneAt(now);
    identity(connection);
    identity(source);
    if (!["up", "down"].includes(direction) || typeof frame !== "string") fail("INVALID_FRAGMENT");
    const first = frame.indexOf("|"),
      second = frame.indexOf("|", first + 1);
    if (first < 0 || second < 0 || frame.slice(0, first) !== MANIFEST.carrier.prefix) fail("INVALID_FRAGMENT");
    const tid = frame.slice(first + 1, second);
    transportId(tid, direction);
    const key = JSON.stringify([connection, source, tid]);
    const third = frame.indexOf("|", second + 1);
    if (third < 0) {
      this.drop(key, now);
      fail("INVALID_FRAGMENT");
    }
    const indexes = frame.slice(second + 1, third),
      content = frame.slice(third + 1);
    if (!scalar(frame) || !fits(frame, direction, this.cfg)) {
      this.drop(key, now);
      fail("INVALID_FRAGMENT");
    }
    if (this.done.has(key)) return null;
    if (!matches("[1-9][0-9]{0,3}/[1-9][0-9]{0,3}", indexes)) {
      this.drop(key, now);
      fail("INVALID_FRAGMENT");
    }
    const [index, total] = indexes.split("/").map(Number);
    if (!content || index < 1 || index > total || total > this.cfg.chunks) {
      this.drop(key, now);
      fail("INVALID_FRAGMENT");
    }
    if (!this.owners.has(key)) {
      if (this.owners.size + this.done.size >= this.cfg.tombstones) fail("LIMIT_EXCEEDED");
      this.owners.set(key, { connection, source, tid });
    }
    const relevant = Array.from(this.core.buffers).filter(([k]) => this.owners.get(k)!.connection === connection);
    const sameSource = relevant.filter(([k]) => this.owners.get(k)!.source === source);
    const current = this.core.buffers.get(key);
    const extra = current?.chunks.has(index) ? 0 : utf8ByteLength(content);
    for (const [entries, countLimit, byteLimit] of [
      [relevant, "connection_buffers", "connection_bytes"],
      [sameSource, "source_buffers", "source_bytes"],
    ] as const) {
      if (
        entries.length + (current ? 0 : 1) > this.cfg[countLimit] ||
        entries.reduce((n, [, b]) => n + b.byteLength, 0) + extra > this.cfg[byteLimit]
      ) {
        this.drop(key, now);
        fail("LIMIT_EXCEEDED");
      }
    }
    // The shared core returns null for both incomplete and rejected input: classify
    // conflict/quotas before push, then use its accounting and completion ordering.
    if (
      current &&
      (current.total !== total || (current.chunks.has(index) && current.chunks.get(index)!.content !== content))
    ) {
      this.drop(key, now);
      fail("FRAGMENT_CONFLICT");
    }
    if (current?.chunks.has(index)) return null;
    if (
      (!current && this.core.bufferCount >= this.cfg.global_buffers) ||
      (current?.byteLength ?? 0) + extra > this.cfg.message_bytes ||
      this.core.bufferedBytes + extra > this.cfg.global_bytes
    ) {
      this.drop(key, now);
      fail("LIMIT_EXCEEDED");
    }
    const buffer = this.core.push(key, index, total, content, content, now);
    if (!buffer) return null;
    const parts: string[] = [];
    for (let i = 1; i <= total; i++) parts.push(buffer.chunks.get(i)!.content);
    this.drop(key, now);
    return decode(parts.join(""), this.cfg);
  }
}

/** The owner supplies its existing bounded sender; never build an unbounded promise chain. */
export async function sendUpstream(
  send: (frame: string) => Promise<void>,
  message: unknown,
  tid: string,
  overrides: Limits = {}
): Promise<void> {
  for (const frame of fragment(message, tid, "up", overrides)) await send(frame);
}
