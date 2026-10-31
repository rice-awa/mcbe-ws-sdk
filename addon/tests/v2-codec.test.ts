import { describe, expect, it } from "vitest";
import { VECTORS } from "../scripts/bridge/v2/assets";
import { decode, encode, validate, V2ProtocolError } from "../scripts/bridge/v2/codec";

describe("MCBEWS/2 codec", () => {
  for (const item of VECTORS.valid)
    it(item.name, () => {
      expect(decode(encode(item.message))).toEqual(item.message);
    });
  for (const item of VECTORS.invalid)
    it(item.name, () => {
      try {
        validate(item.message);
        throw new Error("accepted invalid");
      } catch (err) {
        expect(err).toBeInstanceOf(V2ProtocolError);
        expect((err as V2ProtocolError).code).toBe(item.expected);
      }
    });
  for (const item of VECTORS.raw_invalid)
    it(item.name, () => {
      try {
        decode(item.wire);
        throw new Error("accepted invalid");
      } catch (err) {
        expect(err).toBeInstanceOf(V2ProtocolError);
        expect((err as V2ProtocolError).code).toBe(item.expected);
      }
    });
});
