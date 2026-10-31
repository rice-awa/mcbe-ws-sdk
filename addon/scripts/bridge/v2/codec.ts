import { utf8ByteLength } from "../chunking";
import { MANIFEST } from "./assets";

export type Message = Record<string, unknown>;
export class V2ProtocolError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}
export function fail(code = "INVALID_MESSAGE"): never {
  throw new V2ProtocolError(code);
}
export function isObject(value: unknown): value is Message {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
export function matches(pattern: string, value: unknown): value is string {
  if (typeof value !== "string") return false;
  const match = new RegExp("^(?:" + pattern + ")$").exec(value);
  return match !== null && match[0].length === value.length;
}
function tree(value: unknown, depth = 0, maxDepth = MANIFEST.limits.depth): unknown {
  if (depth > maxDepth) fail("LIMIT_EXCEEDED");
  if (value === null || typeof value === "boolean") return value;
  if (typeof value === "string") {
    for (const ch of value) {
      const n = ch.codePointAt(0)!;
      if (n >= 0xd800 && n <= 0xdfff) fail();
    }
    return value;
  }
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) fail();
    return value;
  }
  if (Array.isArray(value)) return value.map((item) => tree(item, depth + 1, maxDepth));
  if (isObject(value)) {
    const obj: Message = Object.create(null);
    for (const [key, item] of Object.entries(value)) {
      tree(key);
      obj[key] = tree(item, depth + 1, maxDepth);
    }
    return obj;
  }
  return fail();
}
export function validate(message: unknown, resourceLimits = MANIFEST.limits): Message {
  const obj = tree(message, 0, resourceLimits.depth);
  if (!isObject(obj) || !("v" in obj)) fail();
  if (obj.v !== MANIFEST.protocol) fail("UNSUPPORTED_VERSION");
  if (typeof obj.kind !== "string" || !["request", "response", "notification"].includes(obj.kind)) fail();
  const fields = MANIFEST.fields[obj.kind],
    common = MANIFEST.fields.common;
  const required = [...common.required, ...fields.required];
  const allowed = [...required, ...common.optional!, ...(fields.exclusive ?? [])];
  if (required.some((k) => !(k in obj)) || Object.keys(obj).some((k) => !allowed.includes(k))) fail();
  if (obj.kind === "response") {
    if ("result" in obj === "error" in obj) fail();
  } else if (!matches(MANIFEST.method_pattern, obj.method)) fail();
  if ("id" in obj && !matches(MANIFEST.id_pattern, obj.id)) fail();
  if ("error" in obj) {
    const err = obj.error;
    if (
      !isObject(err) ||
      !("code" in err) ||
      !("message" in err) ||
      Object.keys(err).some((k) => !["code", "message", "data"].includes(k))
    )
      fail();
    if (typeof err.code !== "string" || !MANIFEST.errors.includes(err.code)) fail();
    if (typeof err.message !== "string" || utf8ByteLength(err.message) > 256) fail();
  }
  if ("meta" in obj) {
    const meta = obj.meta;
    if (!isObject(meta)) fail();
    for (const [key, value] of Object.entries(meta)) {
      if (key.startsWith("sdk.") && !MANIFEST.metadata.reserved.includes(key)) fail();
      if (key === "sdk.trace_id" && !matches(MANIFEST.token_pattern, value)) fail();
      if (key === "sdk.identity" && !isObject(value)) fail();
      if (key === "sdk.capabilities") {
        if (
          !Array.isArray(value) ||
          value.length > 16 ||
          !value.every((v) => matches(MANIFEST.token_pattern, v)) ||
          new Set(value).size !== value.length
        )
          fail();
      }
    }
    if (utf8ByteLength(JSON.stringify(meta)) > resourceLimits.metadata_bytes) fail("LIMIT_EXCEEDED");
  }
  if (utf8ByteLength(JSON.stringify(obj)) > resourceLimits.message_bytes) fail("LIMIT_EXCEEDED");
  return obj;
}
export function encode(message: unknown, resourceLimits = MANIFEST.limits): string {
  return JSON.stringify(validate(message, resourceLimits));
}
export function decode(wire: string, resourceLimits = MANIFEST.limits): Message {
  if (utf8ByteLength(wire) > resourceLimits.message_bytes) fail("LIMIT_EXCEEDED");
  let value: unknown;
  try {
    value = JSON.parse(wire);
  } catch {
    fail("MALFORMED_JSON");
  }
  return validate(value, resourceLimits);
}
