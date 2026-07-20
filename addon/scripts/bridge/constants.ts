export const BRIDGE_RESPONSE_PREFIX = "MCBEWS|BRIDGE";
export const BRIDGE_UI_CHAT_PREFIX = "MCBEWS|UI_CHAT";
export const BRIDGE_REQUEST_MESSAGE_ID = "mcbews:bridge_req";
export const BRIDGE_SENDER = "MCBEWS_BRIDGE";

/**
 * Addon -> Python（UI 聊天上行）单分片字符上限。
 *
 * 注意：上下行配额不同：
 *   - 上行 (Addon -> Python): 256 字符（受 say/tellraw 命令包装开销限制）
 *   - 下行 (Python -> Addon): 400 字符（由 FlowControlMiddleware 控制）
 */
export const BRIDGE_MAX_CHUNK_CONTENT_LENGTH = 256;

/** MCBE commandLine 实测安全字节上限 */
export const BRIDGE_COMMAND_LINE_BYTE_BUDGET = 461;

/** 单分片内容 code-point 上限（字符数，非字节数） */
export const BRIDGE_MAX_CHUNK_CONTENT_CODE_POINTS = 256;

export const TEXT_RESP_MESSAGE_ID = "mcbews:text_resp";
