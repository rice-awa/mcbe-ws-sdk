/**
 * Bridge chunk encoding/decoding utilities.
 *
 * Pure TypeScript — no `@minecraft/server` imports.
 * Encodes/decodes the wire format
 *  ``MCBEAI|RESP|<requestId>|i/n|<content>``
 * which matches the Python codec in ``mcbe_ws_sdk.addon.protocol``.
 */

/**
 * A decoded bridge response chunk.
 *
 * ``chunkIndex`` and ``totalChunks`` are 1-based.
 */
export interface BridgeChunk {
  requestId: string;
  chunkIndex: number;
  totalChunks: number;
  content: string;
}

// ── public API ────────────────────────────────────────────────

/**
 * Encode ``payload`` into one or more ``MCBEAI|RESP`` fragments.
 *
 * Each fragment will contain at most ``maxLen`` characters of
 * payload content.  Chunk indexes are 1-based.
 *
 * @param requestId  The request ID used to correlate chunks.
 * @param payload    The full response payload to split.
 * @param maxLen     Maximum content characters per fragment.
 * @returns          Array of wire-format strings.
 */
export function encodeBridgeChunks(
  requestId: string,
  payload: string,
  maxLen: number,
): string[] {
  if (!requestId) {
    throw new Error("encodeBridgeChunks: requestId must not be empty");
  }
  if (maxLen <= 0) {
    throw new Error("encodeBridgeChunks: maxLen must be positive");
  }

  const totalChunks = Math.ceil(payload.length / maxLen);
  if (totalChunks === 0) {
    // Empty payload → single empty-content chunk.
    return [`MCBEAI|RESP|${requestId}|1/1|`];
  }

  const fragments: string[] = [];
  for (let i = 0; i < totalChunks; i++) {
    const start = i * maxLen;
    const content = payload.slice(start, start + maxLen);
    fragments.push(
      `MCBEAI|RESP|${requestId}|${i + 1}/${totalChunks}|${content}`,
    );
  }
  return fragments;
}

/**
 * Decode a single wire-format line into a ``BridgeChunk``.
 *
 * Expected format: ``MCBEAI|RESP|<requestId>|i/n|<content>``
 *
 * @throws {Error} on malformed input.
 */
export function decodeBridgeChunk(line: string): BridgeChunk {
  const parts = line.split("|", 5);
  if (parts.length !== 5) {
    throw new Error(
      `decodeBridgeChunk: expected 5 pipe-delimited parts, got ${parts.length}`,
    );
  }

  const [namespace, prefix, requestId, indexPair, content] = parts;

  if (namespace !== "MCBEAI") {
    throw new Error(
      `decodeBridgeChunk: invalid namespace "${namespace}", expected "MCBEAI"`,
    );
  }
  if (prefix !== "RESP") {
    throw new Error(
      `decodeBridgeChunk: invalid prefix "${prefix}", expected "RESP"`,
    );
  }
  if (!requestId) {
    throw new Error("decodeBridgeChunk: requestId must not be empty");
  }

  const slashIdx = indexPair.indexOf("/");
  if (slashIdx === -1) {
    throw new Error(
      `decodeBridgeChunk: invalid index pair "${indexPair}", expected "i/n"`,
    );
  }

  const chunkIndex = parseInt(indexPair.slice(0, slashIdx), 10);
  const totalChunks = parseInt(indexPair.slice(slashIdx + 1), 10);

  if (isNaN(chunkIndex) || isNaN(totalChunks)) {
    throw new Error(
      `decodeBridgeChunk: invalid index pair "${indexPair}", expected integer values`,
    );
  }
  if (chunkIndex <= 0 || totalChunks <= 0) {
    throw new Error(
      `decodeBridgeChunk: chunk index and total must be positive (got ${chunkIndex}/${totalChunks})`,
    );
  }
  if (chunkIndex > totalChunks) {
    throw new Error(
      `decodeBridgeChunk: chunk index ${chunkIndex} exceeds total ${totalChunks}`,
    );
  }

  return { requestId, chunkIndex, totalChunks, content };
}

/**
 * Reassemble a sorted array of chunks into the full payload string.
 *
 * Validates that chunks are complete and sequential before joining.
 *
 * @throws {Error} if chunks are empty, inconsistent, or missing indexes.
 */
export function reassembleBridgeChunks(chunks: BridgeChunk[]): string {
  if (chunks.length === 0) {
    throw new Error("reassembleBridgeChunks: chunks must not be empty");
  }

  // Sort to be safe — callers SHOULD pre-sort, but we guarantee it.
  const sorted = [...chunks].sort((a, b) => a.chunkIndex - b.chunkIndex);

  const requestId = sorted[0].requestId;
  const totalChunks = sorted[0].totalChunks;

  for (const c of sorted) {
    if (c.requestId !== requestId) {
      throw new Error("reassembleBridgeChunks: requestId mismatch");
    }
    if (c.totalChunks !== totalChunks) {
      throw new Error("reassembleBridgeChunks: totalChunks mismatch");
    }
  }

  if (sorted.length !== totalChunks) {
    throw new Error(
      `reassembleBridgeChunks: expected ${totalChunks} chunks, got ${sorted.length}`,
    );
  }

  for (let i = 0; i < sorted.length; i++) {
    if (sorted[i].chunkIndex !== i + 1) {
      throw new Error(
        `reassembleBridgeChunks: missing chunk at index ${i + 1} (got ${sorted[i].chunkIndex})`,
      );
    }
  }

  return sorted.map((c) => c.content).join("");
}
