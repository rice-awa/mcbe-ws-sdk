import { initializeAfterWorldLoad, initializeEarly } from "./bootstrap";
import { activateBridge } from "./bridge/router";
import { initializeToolPlayer, sendV2Frame } from "./bridge/toolPlayer";
import { registerDemoCapabilities } from "./demoCapabilities";

// ── 早期执行阶段：仅注册事件订阅，不访问 world 状态 ──
initializeEarly();

// ── 延迟初始化：注册完整演示能力后开启 MCBEWS/2 承载 ──
initializeAfterWorldLoad(async () => {
  initializeToolPlayer();
  registerDemoCapabilities();
  await activateBridge(sendV2Frame);
});
