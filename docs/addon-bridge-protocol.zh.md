# Addon Bridge Protocol (mcbews v1)

## 目标

在 Python 宿主与 Minecraft Addon 之间建立稳定连接的桥接协议：

- 使用 `/scriptevent` 发起结构化请求与下行文本帧
- 通过模拟玩家聊天分片回传 Bridge 响应与 UI 聊天
- 命名空间统一为 `mcbews` / `MCBEWS`，语义清晰、大小写一致

本协议是 `mcbe-ws-sdk` 的默认协议 profile（`McbewsV1Profile` / `MCBEWS_V1`）。

!!! warning "世界要求：测试版 API"
    配套 Script addon 仅在世界开启 **实验 → 测试版 API**（Beta APIs）时运行。
    未开启时 `scriptEventReceive` 不会触发，能力请求会超时。
    详见 [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md#%E5%9C%A8%E4%B8%96%E7%95%8C%E4%B8%AD%E5%90%AF%E7%94%A8)。

## 命名规则

| 场景 | 规则 | 示例 |
|---|---|---|
| scriptevent messageId | 小写根 token + 通道 | `mcbews:bridge_req` |
| 聊天分片前缀 | 大写根 token + 类型 | `MCBEWS\|BRIDGE` |
| 模拟玩家名 | 大写根 token + 角色 | `MCBEWS_BRIDGE` |

**禁止**在 wire 值中使用 AI 品牌标识；命名空间与 delay kind 一律使用 mcbews / MCBEWS / `text_resp`（旧值对照见文末迁移表）。

## 当前实现概览

当前落地链路如下：

```text
Python host
  -> AddonBridgeService
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE 模拟玩家聊天分片
  -> WebSocket PlayerMessage
  -> Python 分片重组与 future 唤醒
```

当前实现三条独立通道：

### 通道 A：Python -> Addon 能力请求（Bridge）

```text
Python host
  -> AddonBridgeService
  -> scriptevent mcbews:bridge_req <json>
  -> Addon scriptEventReceive
  -> capability handler
  -> MCBEWS_BRIDGE 模拟玩家聊天分片 (MCBEWS|BRIDGE)
  -> WebSocket PlayerMessage
  -> Python 分片重组与 future 唤醒
```

### 通道 B：Addon UI -> Python 自动聊天（UI Chat）

```text
玩家打开 UI 面板输入消息
  -> Addon 发送 UI Chat 分片
  -> MCBEWS_BRIDGE 模拟玩家聊天分片 (MCBEWS|UI_CHAT)
  -> WebSocket PlayerMessage
  -> Python 分片重组
  -> hook.on_ui_chat_reassembled / EventBus UI_CHAT_REASSEMBLED
```

### 通道 C：Python -> Addon 文本响应下行（Text Response）

```text
Python host
  -> McbewsV1Delivery / encode_text_response_commands
  -> scriptevent mcbews:text_resp <json frame>
  -> Addon responseSync 分片重组
  -> UI / 宿主回调展示完整文本
```

其中：

- Python 请求入口位于 `mcbe_ws_sdk.addon.service.AddonBridgeService`。
- Addon 请求监听基于 `scriptEventReceive`，messageId 固定为 `mcbews:bridge_req`。
- Addon Bridge / UI Chat 回传不是直接写回 WebSocket，而是由模拟玩家 `MCBEWS_BRIDGE` 发送聊天分片。
- Python 侧会在 WebSocket `PlayerMessage` 流中识别并拦截这些桥接分片，不再把它们当作普通聊天消息继续处理。
- 文本响应下行使用独立 scriptevent `mcbews:text_resp`，帧格式为 JSON（`id/i/n/p/r/c`）。
- **UI 聊天**由 Addon UI 发起，通过模拟玩家 `MCBEWS_BRIDGE` 发送 `MCBEWS|UI_CHAT` 格式分片；Python 重组后交给宿主 hook，无需玩家在聊天框手动输入命令。

## Wire 常量一览

| 角色 | Profile 字段 | Wire 值 |
|---|---|---|
| Bridge 请求 messageId | `bridge_request_message_id` | `mcbews:bridge_req` |
| 文本响应 messageId | `response_message_id` | `mcbews:text_resp` |
| Bridge 响应前缀 | `bridge_response_prefix` | `MCBEWS\|BRIDGE` |
| UI Chat 前缀 | `ui_chat_prefix` | `MCBEWS\|UI_CHAT` |
| 模拟玩家 | `bridge_sender` | `MCBEWS_BRIDGE` |
| 请求体版本 | `request_version` | `2` |

Python 与 Addon 必须保持上表完全一致。仓库通过 `tools/check_protocol_names.py` 做对齐与禁用 token 扫描。

## 请求格式（Python -> Addon）

- 命令格式：`scriptevent mcbews:bridge_req <json>`
- `message_id` 固定为 `mcbews:bridge_req`
- `json` 结构：
  - `v`: number（当前固定为 `2`）
  - `request_id`: string
  - `capability`: string
  - `payload`: object

示例：

```text
scriptevent mcbews:bridge_req {"v":2,"request_id":"req-1","capability":"get_player_snapshot","payload":{"target":"@s"}}
```

## 响应分片格式（Addon -> Python）

### Bridge 响应（Python -> Addon 请求的回复）

- 前缀：`MCBEWS|BRIDGE`
- 单片格式：`MCBEWS|BRIDGE|<request_id>|<index>/<total>|<content>`
- `<index>` 从 1 开始
- `<content>` 是 JSON 响应字符串的片段
- 分片由模拟玩家 `MCBEWS_BRIDGE` 通过聊天消息发送

示例：

```text
MCBEWS|BRIDGE|req-1|1/2|{"ok":true,
MCBEWS|BRIDGE|req-1|2/2|"result":{"name":"Steve"}}
```

成功响应体（重组后）约定：

```json
{"ok": true, "result": { ... }}
```

失败响应体（重组后）约定：

```json
{"ok": false, "error": {"code": "UNSUPPORTED_CAPABILITY", "message": "..."}}
```

Addon 侧错误码（响应 JSON 内）：

- `MALFORMED_JSON`
- `INVALID_REQUEST`
- `UNSUPPORTED_VERSION`
- `UNSUPPORTED_CAPABILITY`
- `CAPABILITY_FAILED`

### UI Chat 消息（Addon UI -> Python 自动聊天）

- 前缀：`MCBEWS|UI_CHAT`
- 单片格式：`MCBEWS|UI_CHAT|<msg_id>|<index>/<total>|<content>`
- `<index>` 从 1 开始
- `<content>` 是 JSON 字符串的片段，完整 JSON 结构为 `{"player": "<玩家名>", "message": "<聊天内容>"}`
- 分片同样由模拟玩家 `MCBEWS_BRIDGE` 发送；实现上通常使用仅自己可见的 tell 包装，避免真实玩家聊天刷屏

示例（单分片）：

```text
MCBEWS|UI_CHAT|ui-1744876800000-1|1/1|{"player":"Steve","message":"你好世界"}
```

示例（多分片）：

```text
MCBEWS|UI_CHAT|ui-1744876800000-1|1/2|{"player":"Steve","mes
MCBEWS|UI_CHAT|ui-1744876800000-1|2/2|sage":"你好世界"}
```

## 文本响应格式（Python -> Addon）

- 命令格式：`scriptevent mcbews:text_resp <json>`
- 单帧 JSON 字段：

| 字段 | 含义 |
|---|---|
| `id` | 响应消息 ID |
| `i` | 分片序号（从 1 开始） |
| `n` | 分片总数 |
| `p` | 目标玩家名 |
| `r` | 角色（如 `assistant`） |
| `c` | 文本内容片段 |

示例：

```text
scriptevent mcbews:text_resp {"id":"resp-1","i":1,"n":2,"p":"Steve","r":"assistant","c":"你好，"}
scriptevent mcbews:text_resp {"id":"resp-1","i":2,"n":2,"p":"Steve","r":"assistant","c":"世界"}
```

Addon 按 `id` 缓存分片，收齐 `1..n` 后重组为完整文本并回调展示层。

## 能力清单（当前基线）

Addon 默认能力注册表当前包含：

- `get_player_snapshot`：获取玩家快照（位置、维度、朝向、基础状态）
- `get_inventory_snapshot`：获取背包快照（槽位、物品、数量、附加数据）

另外实现了可注册模块：

- `run_world_command`：受控执行世界命令并返回结果（需宿主/Addon 显式挂到注册表）

能力集合由 Addon 拥有；Python SDK **不**内置入站能力分发器。未注册 capability 时，Addon 返回 `UNSUPPORTED_CAPABILITY`。

## 请求关联与生命周期

- 每次 Python 发起桥接调用时，都会生成唯一 `request_id`。
- `request_id` 会同时出现在 `/scriptevent` 请求体和 Addon 聊天分片头部，用于关联同一轮调用。
- Python 侧按连接维度维护 pending request，并按 `request_id` 缓存分片。
- 当同一 `request_id` 的全部分片收齐后，Python 会重组 JSON payload，唤醒对应等待中的请求 future。
- 如果收到未知 `request_id` 的分片，当前实现会忽略，不会为其创建新请求。
- 发送方过滤：仅当 `PlayerMessage.sender == MCBEWS_BRIDGE` 且前缀匹配时，才进入桥接/UI Chat 重组路径。

## 超时行为

- Python 侧桥接服务当前默认超时时间为 5 秒（`AddonBridgeSettings.timeout_seconds`）。
- 如果 Python 已经发送 `/scriptevent`，但在超时窗口内没有收齐指定 `request_id` 的全部分片，请求会以“Addon 桥接响应超时”失败。
- 如果命令发送阶段本身失败，例如 `/scriptevent` 执行返回错误，则不会进入等待分片阶段，而是直接失败。
- 超时或失败后，Python 会清理该 `request_id` 对应的 pending request 与分片缓存。
- 分片缓冲区另有 TTL（默认 30 秒）与字节/数量上限，防止泄漏。

## 错误语义（协议级）

### Bridge 响应分片解码 / 重组

Python codec 在以下情况抛出错误（`ValueError`，消息语义如下）：

- 分片字段数量错误
- 分片命名空间 / 前缀不匹配（期望 `MCBEWS` + `BRIDGE`）
- 分片元数据非法（索引 / 总数 / request_id）
- 分片列表为空
- 分片序号缺失、重复或顺序不一致
- 同一批分片出现不同 `request_id` 或不同 `total`
- 重组后 JSON 反序列化失败或根类型不是 object

### UI Chat 分片解码 / 重组

- 分片字段数量错误
- 分片命名空间 / 前缀不匹配（期望 `MCBEWS` + `UI_CHAT`）
- 分片元数据非法
- 分片列表为空
- 分片序号缺失、重复或顺序不一致
- 重组后 JSON 非法
- JSON 中缺少非空 `message` 字段

### 诊断

若聊天内容以协议根前缀 `MCBEWS|` 开头，但因 sender 等条件未进入桥接处理，Python facade 应打出 mismatch 诊断日志（`bridge_prefix_not_matched`），避免请求静默超时且无线索。

## 约束与设计依据

- `/scriptevent <messageId> <message>` 中 `message` 最大 2048 字符，超长消息必须分片。
- 脚本侧可通过 `ScriptEventCommandMessageAfterEvent` 读取 `id` 与 `message`，因此保留显式命名空间路由 `mcbews:bridge_req` / `mcbews:text_resp`。
- 当前 Addon -> Python 回传依赖聊天通道，而不是独立二进制或自定义网络通道，因此需要考虑聊天消息长度与分片顺序问题。
- Python 侧只会在 WebSocket `PlayerMessage` 事件中拦截桥接分片，所以聊天事件订阅链路必须正常。
- MCBE `commandLine` 实测安全字节上限为 **461**；上下行分片都必须做真实 UTF-8 字节校验。
  - 上行（Addon -> Python，聊天包装）默认单分片内容 code-point 上限：256
  - 下行（Python -> Addon，scriptevent/文本）默认由 `FlowControlSettings.max_chunk_content_length` 控制（默认 400）
- 文本响应流控 delay kind 为 `text_resp`（旧 delay kind 已废弃，见文末迁移表）。
- 由于 `@minecraft/server` API 形态限制，`run_world_command` 基于同步 `runCommand` 实现（若已注册）。
- 本协议不绑定任何 LLM / Agent 产品语义；宿主如何解释 UI Chat 或文本响应由宿主决定。

## 当前基线实现

### Python 侧

- `McbewsV1Profile` / `MCBEWS_V1`：默认协议 profile
- `encode_bridge_request`：编码 Bridge 请求命令
- `decode_bridge_chat_chunk`：解析 Bridge 响应分片
- `reassemble_bridge_chunks`：重组并解析 JSON payload
- `decode_ui_chat_chunk`：解析 UI Chat 消息分片
- `reassemble_ui_chat_chunks`：重组 UI Chat 分片并提取玩家名与消息
- `encode_text_response_commands`：编码文本响应 scriptevent 帧列表
- `McbewsV1Delivery`：带 prelude / chunk delay 的文本响应投递
- `AddonBridgeService`：发送 `/scriptevent`、等待 future、处理超时、UI Chat 回调分发
- WebSocket facade 在 `PlayerMessage` 事件流中拦截 `MCBEWS_BRIDGE` 的桥接分片与 UI Chat 消息

### Addon 侧

- `constants.ts`：唯一 wire 常量源（messageId / 前缀 / 模拟玩家）
- `formatChunk`：通用分片格式化（支持自定义前缀）
- `formatResponseChunk`：格式化 Bridge 响应分片
- `chunkPayload`：通用分片分割（支持自定义前缀）
- `chunkBridgePayload`：按最大片段长度分割 Bridge 响应
- `chunkUiChatPayload`：按最大片段长度分割 UI Chat 消息
- 响应发送路径：驱动 `MCBEWS_BRIDGE` 发送 Bridge 响应分片
- UI Chat 发送路径：驱动 `MCBEWS_BRIDGE` 发送 UI Chat 消息
- `registerBridgeRouter`：订阅 `scriptEventReceive` 并分派 capability handler
- `responseSync`：订阅 `mcbews:text_resp` 并重组文本响应帧

## 与旧协议（mcbeai）的关系

本协议为**破坏性**替换，不提供双读兼容：

| 角色 | 旧值（已废弃） | 新值 |
|---|---|---|
| Bridge 请求 | `mcbeai:bridge_request` | `mcbews:bridge_req` |
| 文本响应 | `mcbeai:ai_resp` | `mcbews:text_resp` |
| Bridge 前缀 | `MCBEAI\|RESP` | `MCBEWS\|BRIDGE` |
| UI Chat 前缀 | `MCBEAI\|UI_CHAT` | `MCBEWS\|UI_CHAT` |
| 模拟玩家 | `MCBEAI_TOOL` | `MCBEWS_BRIDGE` |

Python 宿主与 Addon 必须同步升级；混用旧/新命名空间会导致桥接请求超时。
