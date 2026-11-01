# R18A 旧接口处置清单

日期：2026-11-01。依据[V2-only 整改方案](../v2-only-remediation.zh.md) D05—D07。
本表记录“符号／路由 → 调用方 → 处置 → 验收节点”，实施时按此迁移共用依赖后再删目录。

## 1. 目标公开面

Python 只保留一个入口 `McbeServer`，它始终使用可靠调度并强制 MCBEWS/2 准入。
`McbeServerFacade`、串行回调路径、`negotiate=False` 与手动 `activate` 都不再公开。

- **连接生命周期**：`WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED`，失败为 `REJECTED → CLOSED`。
- **READY 前**：只处理协商帧、必要控制帧和原生 `commandResponse`/`error` 关联；不发出业务 `CONNECTED`、不调用 `on_connected`、不执行玩家消息。
- **READY 后**：发出一次业务 `CONNECTED` 并调用 `on_connected`；`MCBEWS2|` 由 `V2Bridge` 路由。
- **失败**：明确原因后关闭 WS；不产生无配对的业务 `on_disconnected`。

Addon 只识别 `mcbews:negotiate_v2` 与 `mcbews:packet_v2`；旧 `mcbews:bridge_req`/
`mcbews:session_req`/`mcbews:text_resp` 只记录拒绝，不产生任何业务响应或副作用。

## 2. Python

| 符号／路由 | 调用方 | 处置 | 验收 |
| --- | --- | --- | --- |
| `profiles/mcbews_v1/*` | facade、addon service、根导出、工具、测试 | 删除；历史样本仅留在负向测试 | 旧路径导入失败；旧帧被拒绝 |
| `extensions/mcbews_v1/*` | 根导出 | 删除 | 无旧导出 |
| `AddonBridgeService` / `AddonBridgeClient` / `ConnectionAddonBridgeClient` | facade、示例、测试 | 删除；业务改用 `V2Bridge.request` | 示例只走 V2 |
| `AddonBridgeSettings` / `GatewaySettings.addon` | config、facade | 删除 | 配置无 addon 段 |
| `UiChatMessage` / `ToolPlayerMessage` / `AddonControlHook` | hook、facade | 删除；通用 V2 交给宿主 | hook 只剩五个生命周期回调 |
| `SessionRequest` / `SessionResponse` / `ApprovalDecision` / `TextResponse*` | 根导出、addon service | 删除 | 无旧 DTO 导出 |
| `LegacyUiChatHook` / `LegacyUiChatHookAdapter` | hook、facade | 删除 | 无旧适配器 |
| `McbeServerFacade` + `DispatchSettings.mode="legacy"` | 公开入口、测试 | 迁移到 `McbeServer`；删除旧类与 mode | 只有一个公开入口 |
| `V2Bridge(negotiate=False)` / 公开 `activate()` | facade、测试 | 删除；协商是唯一准入路径 | 测试不能绕过协商 |
| `tools/generate_protocol_assets.py` / `check_protocol_names.py` | CI、质量检查 | 删除或改为 V2 名称检查 | CI 不再要求 V1 资产 |
| `tools/check_dist.py` / `probe_installed_contract.py` V1 资产 | dist、contract | 改为只校验 V2 权威资产 | 包内无 V1 实现与资产 |
| `tests/unit/test_addon_bridge.py` 等旧正向用例 | pytest | 删除正向能力；保留旧帧拒绝回归 | 旧成功路径不再存在 |

保留：461 B 预算改为从 `profiles/mcbews_v2/manifest.json` 的 `limits.command_bytes` 读取；
原生 MCBE `header.version=1` 与 `body.version` 不属于 MCBEWS/1，保留。

## 3. Addon

| 符号／路由 | 调用方 | 处置 | 验收 |
| --- | --- | --- | --- |
| `bridge/protocol.ts` 全文件 | chunking、router、toolPlayer、extensions、tests | 删除；中性常量迁入 `bridge/constants.ts` | 生产包无 `MCBEWS/1` |
| `CAPABILITY_REQUEST_SCRIPT_EVENT_ID` / `mcbews:bridge_req` | router | 只保留拒绝日志 | 无旧响应、无业务副作用 |
| `parseBridgeRequest` / `handleBridgeScriptEvent` / `responseSender` | router、tests | 删除 | 无旧解析器与发送器 |
| `preReadyQueue` 业务积压 | router | 删除；只保留协商 hello | READY 前不缓存业务 |
| `setCapabilityHandler` 聚合入口 | router、tests | 删除；只保留 `registerCapability` | 无聚合兼容入口 |
| `activateV2Bridge` 公开手动激活 | router、tests | 删除；协商内部创建 endpoint | 无生产绕过入口 |
| `responseSync.ts` / `extensions/textResponse.ts` | bootstrap、main、tests | 删除 | 不再订阅 `mcbews:text_resp` |
| `sendBridgeResponseChunks` / `sendV2Frame` | main、router | 删除旧发送器，保留 `sendV2Frame` | 只走 V2 载体 |
| `chunkBridgePayload` / `chunkUiChatPayload` | toolPlayer、tests | 删除；保留 `utf8ByteLength` | 无旧信封分片 |
| 旧 UI_CHAT / SESSION / TOOL_APPROVE 前缀 | 无 | 删除 | 不再产出 |

默认能力仍为 `get_player_snapshot`、`get_inventory_snapshot`；完整演示显式注册
`demo.echo`、`demo.ticks`、受限 `run_world_command`，世界命令默认关闭。

## 4. 验收节点

- 双端 V2 正常：协商一次 READY，请求／通知／流正常（A01、A10）。
- 旧端：旧 addon 无回复限时关闭；旧服务帧被 addon 拒绝（A02、A03）。
- 无 addon：限时关闭，`!wscmd` 不执行（A04）。
- 早到业务、迟 ready、伪造 epoch、混入旧帧：无业务副作用、无重放（A05）。
- 关闭竞争与资源清理：等待、任务、队列、租约、分片归零（A06）。
