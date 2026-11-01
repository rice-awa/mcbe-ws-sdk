# 升级到当前 SDK

本指南适用于使用 V1 接口的宿主，也涵盖开发期间的双协议版本。
当前 SDK 有破坏性变化：移除了 MCBEWS/1 和旧服务入口。
Python 包与配套附加包需要配套部署；新旧协议不匹配时连接会被拒绝。

下文标记为**已删除**的符号与路由在当前版本中已不存在，仅用于帮助现有宿主查找替代项。

## 变化概览

| 领域 | 旧宿主 | 当前版本 |
| --- | --- | --- |
| Python 入口 | `McbeServerFacade`；部分开发版本还提供 `McbeServer` | 只保留 `McbeServer` |
| 调度 | `legacy` 串行回调或 `reliable` 队列 | 始终使用受管理队列 |
| 协议 | 默认 MCBEWS/1，显式启用 MCBEWS/2 | 当前使用 V2（MCBEWS/2），业务在协商后运行 |
| 协议协商 | 探测完成前业务仍可运行 | 必须 `READY_V2` 后才执行业务 |
| 回退 | 旧附加包继续使用已知 v1 方法 | 无回退；旧端直接拒绝 |
| 附加包路由 | `mcbews:bridge_req`、`mcbews:text_resp`、UI/会话路由 | 只识别 `mcbews:negotiate_v2`、`mcbews:packet_v2` |
| 宿主业务 DTO | SDK 自带 UI 聊天、会话、审批、文本响应 | 已删除；改为宿主自己的 V2 方法 |
| 流式 | 在 v2 路径上协商 `stream/1` | 唯一路径上的 `stream/1` |

## 连接就绪时机

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

- `WS_TRANSPORT_CONNECTED` 只表示传输建立，不表示业务就绪。
- `NEGOTIATING` 只处理协商、必要控制帧，以及已关联的原生
  `commandResponse` / `error`；业务帧会被丢弃，不会缓存后重放。
- `on_connected` 在 `READY_V2` 时只触发一次。被拒绝的连接不会产生业务
  `on_connected`，也不会产生无配对的业务 `on_disconnected`。
- 失败会以 `1008` 关闭 WebSocket，并附带长度有界的原因标识。

| 对端组合 | 结果 | 业务副作用 |
| --- | --- | --- |
| 新服务 + 新附加包 | 完成一次 MCBEWS/2 协议协商 | `READY_V2` 后允许 |
| 新服务 + 旧附加包 | `UNSUPPORTED_PROTOCOL` 或 `NEGOTIATION_TIMEOUT`，随后关闭 | 无 |
| 新服务 + 无附加包 | 限时超时后关闭 | 无；`!wscmd` 不执行 |
| 旧服务 + 新附加包 | 附加包记录并拒绝旧事件 | 无；附加包无法关闭不属于它的 WebSocket |
| 就绪后混入旧业务帧 | 拒绝 | 无 |

常见拒绝原因：`UNSUPPORTED_PROTOCOL`、`NEGOTIATION_TIMEOUT`、
`NEGOTIATION_CONFIRMATION_TIMEOUT`、`NEGOTIATION_BUSY`、`INVALID_NEGOTIATION`、
`SESSION_INVALID`。

原生基岩 WebSocket 的 `header.version = 1` 属于游戏协议，与 MCBEWS/1 无关，
保持 Bedrock 的定义不变。

## 已删除的 Python API

### 入口与调度

| 已删除 | 替代 |
| --- | --- |
| `McbeServerFacade` | `McbeServer` |
| `DispatchSettings(mode="legacy" \| "reliable")` 开关 | 已删除；`McbeServer` 始终使用受管理队列 |
| `ConnectionHook.on_ui_chat_reassembled` | 已删除；使用原生事件或宿主自己的 V2 方法 |
| `AddonControlHook` | 已删除 |
| `LegacyUiChatHook`、`LegacyUiChatHookAdapter` | 已删除 |
| `V2Bridge(negotiate=False)` | 已删除；连接建立时自动协商协议 |
| 公开手动激活 `V2Bridge.activate(...)` | 已删除；端点由协议协商流程安装 |

`ConnectionHook` 现在只有五个生命周期钩子：`on_connected`、`on_disconnected`、
`on_player_message`、`on_command_response`、`on_error`。`NoOpHook` 实现全部五个。

### 旧协议表面

| 已删除 | 说明 |
| --- | --- |
| `mcbe_ws_sdk.profiles.mcbews_v1.*` | 不再有 v1 profile、清单、向量、编解码与投递 |
| `MCBEWS_V1`、`McbewsV1Profile`、`McbewsV1Protocol` | 已删除 |
| `McbewsV1Delivery` | 使用 `McbeOutboundDelivery` / `V2Endpoint` |
| `mcbe_ws_sdk.extensions.mcbews_v1.*` | 已删除 |
| v1 桥编解码与线向量 | 旧帧只被拒绝，不再解码执行 |

461 字节经验命令预算保留，但现在来自 MCBEWS/2 清单的 `limits.command_bytes`，只能降低。

### 附加包桥与宿主业务辅助

| 已删除 | 替代 |
| --- | --- |
| `AddonBridgeService` | `server.v2`（`V2Bridge`） |
| `AddonBridgeClient`、`ConnectionAddonBridgeClient` | `V2Bridge.request` / `V2Bridge.stream` |
| `AddonBridgeSettings`、`AddonMessageResult`、`GatewaySettings.addon` | 已删除；传输设置保留在 `GatewaySettings` |
| `UiChatMessage`、`ToolPlayerMessage` | 已删除 |
| `SessionRequest`、`SessionResponse`、`ApprovalDecision`、`SessionError` | 已删除 |
| `TextResponseChunk`、`TextResponseMessage`、`TokenUsage`、文本响应编解码 | 已删除 |
| `mcbews:bridge_req`、`mcbews:session_req`、`mcbews:text_resp` | 已删除；只记录拒绝，零副作用 |

如果宿主仍需要会话、审批、UI 聊天或文本响应语义，请把它们实现为普通的 MCBEWS/2
方法与通知，放进自己的能力注册表。SDK 不再提供这些业务 DTO、通道或聚合入口。

## 已删除的附加包 API 与路由

| 已删除 | 替代 |
| --- | --- |
| `setCapabilityHandler` 聚合入口 | `registerCapability(name, handler, options)` |
| `mcbews:bridge_req` 解析器与响应发送器 | `mcbews:negotiate_v2` + `mcbews:packet_v2` 载体 |
| `preReadyQueue` 业务积压 | 已删除；就绪前业务直接丢弃 |
| `activateV2Bridge` 公开手动激活 | 已删除；世界加载后自动激活 |
| `mcbews:text_resp` 订阅 | 已删除 |
| `responseSync.ts` / `extensions/textResponse.ts` | 已删除 |
| `chunkBridgePayload`、`chunkUiChatPayload` | 已删除；保留 `utf8ByteLength` 与中性限制常量 |
| `MCBEWS\|BRIDGE`、`MCBEWS\|UI_CHAT`、`MCBEWS\|SESSION`、`MCBEWS\|TOOL_APPROVE` | 已删除；不再产出或接受 |

`getV2Endpoint(epoch)`、`registerCapability`、`unregisterCapability`、
`registerV2Stream` 与只读能力注册表保留。上行走传输仍使用模拟玩家
`MCBEWS_BRIDGE`；该名称只是传输归属，不是认证身份。

## 迁移步骤

### 1. 替换入口

```python
# 旧
server = McbeServerFacade(hook=hook)

# 新
server = McbeServer(hook=hook)
```

不要在钩子外自行创建后台任务。受管理队列会让读取循环在钩子等待命令或能力回复时继续工作。

### 2. 替换能力调用

```python
# 旧
service = AddonBridgeService(AddonBridgeSettings())
response = await service.request(state, "get_player_snapshot", params)

# 新
response = await server.v2.request(
    state.id, "get_player_snapshot", {"target": player_name}
)
```

新调用返回响应信封，成功数据在 `result` 中。远端协议失败抛出带 `code` 的
`RemoteCallError`。不再有 v1 结果包装。

### 3. 替换能力注册

Python：

```python
registration = server.v2.registry.register("demo.echo", handler, validate=validate)
```

附加包：

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("demo.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
```

同名注册必须传 `replace: true` 才覆盖。注销只影响后续调用。

### 4. 替换 UI / 会话 / 审批 / 文本响应流程

把每个流程建模为 V2 方法或通知：

- UI 提交是附加包发往 Python 处理器的请求；
- 审批决定是带有自定义稳定方法名与 schema 的请求或通知；
- 流式文本使用 `stream/1` 生产者/消费者，而不是分片的 `mcbews:text_resp`；
- 面向玩家的渲染仍由宿主的 `ResponseSink` 负责。

SDK 不再解释这些载荷，也不再提供对应的类型化 DTO。

### 5. 重新构建附加包

从新源码构建并重新导入附加包，更新 Minecraft 侧的协议实现：

```bash
cd addon
npm ci
npm run build
npm run mcaddon
```

演示能力随默认包发布。只有需要两个只读快照的精简包时，才使用
`npm run build:base` / `npm run mcaddon:base`。

## 版本号分别表示什么

| 版本轴 | 当前值 | 含义 |
| --- | --- | --- |
| Python SDK | `0.3.0rc1` | 发布候选，验收后再转 `0.3.0` |
| npm 项目 | `0.3.0-rc.1` | 附加包构建项目，标记为 private，不向 npm 发布 |
| Pack header 与 modules | `1.1.0` | 行为包、资源包的部署版本 |
| 线协议 | `MCBEWS/2` | 消息与协商合同 |
| Script API 依赖 | server `2.8.0`、server-ui `2.1.0` | 游戏提供的 API |
| Gametest | pack `1.0.0-beta`；npm `1.0.0-beta.1.26.30-stable` | 引擎模块声明与配套开发包 |
| 最低引擎 | `[1, 26, 30]` | 声明的游戏目标，实际游戏构建仍需单独验证 |

Pack 版本使用三个整数，不携带 SDK 的 `rc1` 后缀。请把同一候选的 Python 包与附加包一起保存。
Script API 依赖升级不自动改变线协议，也不决定 SDK 发布版本。

## 回退与部署

1. 把当前部署的 Python wheel、附加包文件、宿主配置和世界备份保存为一套回退资料，
   记录版本和来源。
2. 在独立环境安装候选 wheel：

   ```bash
   python -m venv .venv-next
   .venv-next/bin/pip install ./dist/0.3.0rc1/python/mcbe_ws_sdk-0.3.0rc1-py3-none-any.whl
   ```

   Windows 使用 `.venv-next\Scripts\pip`。在新环境安装宿主自己的依赖，
   并先完成宿主 API 调用迁移。
3. 断开旧服务，在目标世界启用候选配套附加包，开启 **实验 → 测试版 API（Beta APIs）**，
   重新加载世界，再用新环境启动更新后的宿主。
4. 重新执行 `/wsserver`。使用附加包示例核对连接就绪、`!inv`、`!echo` 和
   `!stream 5`，确认后再接入业务。
5. 需要回退时，断开连接并停止新宿主，停用新包、启用保存的旧附加包，重新加载世界。
   用保存的旧环境启动旧宿主并重连；如需回退世界状态，再恢复对应世界备份。

当前版本没有 v1 回退。回退意味着重新部署**Python 包与附加包**的完整旧版本，然后重新连接。

- 新旧版本无法协商：新服务拒绝旧附加包，新附加包拒绝旧服务的业务帧。
- 重连会建立新代次和新会话。旧请求、流、审批与重组状态不会恢复。
- 已执行的世界命令不会撤销，副作用也不会自动重放。
- 如果回退必须恢复 v1 行为，就要同时恢复旧 Python 包与旧附加包资产；
  当前版本无法通过配置切回 v1 模式。

## 没有变化的部分

- `PlayerMessageEvent.sender` 仍是权威玩家身份。
- `NoOpHook`、`ConnectionHook`、`ResponseSink`、`OutboundText`、
  `SystemNotification` 与 `McbeOutboundDelivery` 的职责不变。
- 原生事件订阅与拦截器合同不变。
- 461 字节命令预算、句子优先分片与原生命令原子性不变。
- 自动化测试不等于实机支持；请单独验证目标游戏版本、平台与附加包加载情况。
