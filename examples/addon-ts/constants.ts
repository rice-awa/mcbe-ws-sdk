/**
 * Protocol constants for the mcbe-ws-sdk bridge.
 *
 * These match the Python-side defaults in `AddonProtocolConfig`
 * (`mcbe_ws_sdk.config.AddonProtocolConfig`). Keep them in sync.
 */

/** The scriptevent message ID for bridge requests (Python -> Addon). */
export const BRIDGE_MESSAGE_ID = "mcbeai:bridge_request";

/**
 * Chat-message prefix used when the Python side sends a response
 * fragment to the bridged tool player.
 *
 * Format: `MCBEAI|RESP|<requestId>|i/n|<content>`
 */
export const BRIDGE_RESPONSE_PREFIX = "MCBEAI|RESP";

/**
 * Chat-message prefix used when the UI (addon side) sends a chat
 * message back to the Python side.
 *
 * Format: `MCBEAI|UI_CHAT|<msgId>|i/n|<content>`
 */
export const BRIDGE_UI_CHAT_PREFIX = "MCBEAI|UI_CHAT";

/** The fake player name the Python side impersonates for tool execution. */
export const TOOL_PLAYER_NAME = "MCBEAI_TOOL";

/**
 * The scriptevent message ID the Python side uses to deliver
 * streamed AI response chunks to the addon.
 */
export const AI_RESP_MESSAGE_ID = "mcbeai:ai_resp";

/**
 * Maximum number of characters in a single *upstream* chunk
 * (Addon -> Python). The downstream budget is larger and
 * controlled by the Python-side flow-control settings.
 */
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;
