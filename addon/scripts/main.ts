import { initializeEarly, initializeAfterWorldLoad } from "./bootstrap";

// ── 早期执行阶段：仅注册事件订阅，不访问 world 状态 ──
initializeEarly();

// ── 延迟初始化：在世界就绪后注册能力处理器 ──
initializeAfterWorldLoad(() => {
  // 宿主在此注册 CapabilityHandler / ResponseSender 和 AiRespHandler。
  // 示例:
  //   import { setCapabilityHandler, setResponseSender } from "./bridge/router";
  //   import { setAiRespHandler } from "./bridge/responseSync";
  //   import { ensureToolPlayer, sendBridgeResponseChunks } from "./bridge/toolPlayer";
});
