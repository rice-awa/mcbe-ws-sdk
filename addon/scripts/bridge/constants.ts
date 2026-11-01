/** Neutral limits shared by the MCBEWS/2 addon paths. */
import { MANIFEST } from "./v2/assets";

/** Empirical Bedrock commandLine budget; deployments may lower it. */
export const COMMAND_LINE_BYTE_BUDGET = MANIFEST.limits.command_bytes;

/** Maximum Unicode scalar values in one upstream carrier frame. */
export const MAX_CONTENT_CODE_POINTS = MANIFEST.limits.upstream_code_points;

/** Protocol wire limit for an error message, in UTF-8 bytes. */
export const MAX_ERROR_MESSAGE_BYTES = 256;
