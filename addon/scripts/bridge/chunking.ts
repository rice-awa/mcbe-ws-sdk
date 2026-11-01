/** Count UTF-8 bytes without relying on TextEncoder in the Bedrock runtime. */
export function utf8ByteLength(value: string): number {
  let byteLength = 0;
  for (const symbol of value) {
    const codePoint = symbol.codePointAt(0)!;
    if (codePoint <= 0x7f) byteLength += 1;
    else if (codePoint <= 0x7ff) byteLength += 2;
    else if (codePoint <= 0xffff) byteLength += 3;
    else byteLength += 4;
  }
  return byteLength;
}
