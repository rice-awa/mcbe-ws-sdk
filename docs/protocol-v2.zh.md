# MCBEWS/2 协议

当前 SDK 与配套附加包使用 **V2 协议（MCBEWS/2）**。
连接完成协议协商后开始处理业务消息；就绪前收到的业务帧会被丢弃，不会缓存重放。

安装包内的 `profiles/mcbews_v2/manifest.json` 与 `vectors.json` 是标识、字段规则、
错误、限制与协商帧的权威来源。本页解释合同，不覆盖清单。

MCBEWS/2 借鉴 JSON-RPC 的请求/响应形状，但不兼容 JSON-RPC。

## 连接建立

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

| 状态 | 行为 |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | 仅传输建立，不表示业务就绪 |
| `NEGOTIATING` | 接受协商、必要控制帧，以及已关联的原生 `commandResponse` / `error`；业务帧丢弃，不缓存 |
| `READY_V2` | 发布业务 `CONNECTED`，调用 `on_connected`，路由玩家消息与载体帧 |
| `REJECTED` | 记录原因，最多发送一次有界提示，然后关闭 |
| `CLOSING` / `CLOSED` | 释放请求、队列、任务、租约与分片 |

协议协商失败以 `1008` 关闭 WebSocket。关闭原因是有界标识，最多 123 个 UTF-8 字节。
稳定原因：

| 原因 | 含义 |
| --- | --- |
| `UNSUPPORTED_PROTOCOL` | 对端使用已退休协议或不支持的版本 |
| `NEGOTIATION_TIMEOUT` | 没有附加包、没有回复，或脚本未加载 |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | 已应答挑战，但最终确认未完成 |
| `NEGOTIATION_BUSY` | 协商容量或临时挑战名额已满 |
| `INVALID_NEGOTIATION` | 控制帧格式错误、不匹配或已过期 |
| `SESSION_INVALID` | 已就绪会话被撤销或过期 |

被拒绝的连接不会产生业务 `on_connected`，也不会产生无配对的业务 `on_disconnected`。
重连会建立新尝试与新代次；已关闭尝试的迟到帧不能恢复已关闭的连接。

### 控制帧

附加包只接受两个 scriptevent 标识：`mcbews:negotiate_v2` 与 `mcbews:packet_v2`。

```text
Python → 附加包   MCBEWS2H|attempt
附加包 → Python   MCBEWS2N|attempt|epoch|2|逗号分隔的功能
Python → 附加包   MCBEWS2C|attempt|epoch
附加包 → Python   MCBEWS2N|attempt|epoch|ready
附加包 → Python   MCBEWS2N|attempt|-|busy
Python → 附加包   MCBEWS2R|epoch          （租约续期）
附加包 → Python   MCBEWS2N|epoch|lease
Python → 附加包   MCBEWS2X|epoch          （关闭）
```

`attempt` 与 `epoch` 是新的 1–24 字符 ASCII 路由令牌。代次标识会话，不是认证。
每条连接同时只处理一个未完成的协商尝试，且挑战必须匹配后才能激活。繁忙或过期的尝试会被终止，
重试次数有界。

### 已退休路由

`mcbews:bridge_req`、`mcbews:session_req` 与 `mcbews:text_resp` 已退休。
附加包只记录并拒绝，零业务副作用：不解析、不排队、不回复、不分发。
`MCBEWS|BRIDGE`、`MCBEWS|UI_CHAT`、`MCBEWS|SESSION` 与 `MCBEWS|TOOL_APPROVE`
不再产出或接受。

附加包无法关闭旧服务持有的 WebSocket，只拒绝其业务帧。
新服务对旧附加包或缺失附加包按超时拒绝，并关闭自己持有的传输。

原生基岩 WebSocket 的 `header.version = 1` 属于游戏协议，与 MCBEWS/1 无关，
不能用来识别旧客户端。

## 逻辑消息

每条消息都是严格校验的 JSON 对象：

```json
{"v": "MCBEWS/2", "kind": "request", "id": "p.session.1", "method": "demo.echo", "params": {}}
```

- `v` 必须为 `MCBEWS/2`。
- `kind` 为 `request`、`response`、`notification`、`stream_open`、
  `stream_item`、`stream_end`、`stream_error` 或 `stream_cancel`。
- 每种消息规定必需、可选与禁止字段；未知核心字段会被拒绝。
- `method` 遵守清单中的 ASCII 名称模式。
- `params` 与 `result` 可以是任意合法 JSON 值，包括 `null`。
- 回复必须且只能包含 `result`、`error` 之一。
- `error` 必须含清单允许的 `code` 和最多 256 个 UTF-8 字节的字符串 `message`，
  可选 `data` 接受任意合法 JSON 值，其他字段拒绝。

### 编号

逻辑编号格式为 `origin.session.sequence`：

- `p` 表示 Python 发起，`a` 表示附加包发起。
- `session` 是每条连接代次新建的 1–24 个 ASCII 字符令牌。
- `sequence` 是 1–10 位正十进制数。

回复沿用请求编号，双方同时发起不会冲突。通知没有逻辑编号，也不创建回复等待项。
每条载体消息另用实际发送方的新传输编号，包括回复和通知；因此回复的逻辑编号可属于对端。
重连不恢复编号或片段，不自动重放副作用。

### JSON 规则

- 数字必须是 ±9007199254740991 内的有限安全整数，布尔值保持布尔类型。
  可接受以科学记数法表示的安全整数。
- 数值按与 `JSON.parse` 一致的 IEEE-754 解析结果校验：
  `1.0000000000000001` 变为 `1`，`1e-4000` 变为 `0`。
  超长整数归为 `INVALID_MESSAGE`，不会关闭进程级解析保护。
- 字符串必须由 Unicode 标量组成，拒绝孤立代理项。
- 对象字段重复时取最后一个值。
- 根深度为 0，最大深度为 16。
- 整条紧凑 JSON 编码后最多 65536 个 UTF-8 字节。

### 元数据

`meta` 必须是对象，紧凑 JSON 编码后最多 4096 个 UTF-8 字节。
未知可选键及其嵌套值保留。`sdk.` 是核心保留前缀：

| 字段 | 约束 |
| --- | --- |
| `sdk.trace_id` | 符合清单规则的令牌 |
| `sdk.identity` | 不可信的身份对象，不自动授予权限 |
| `sdk.capabilities` | 最多 16 个不重复的清单令牌，`stream/1` 启用流消息 |

未知保留字段会被拒绝，深度与数值规则同上。
调用方不能设置保留字段；追踪编号通过专用参数传入。

## 双向载体与资源

下行通过 `scriptevent mcbews:packet_v2 FRAME`，上行由模拟玩家 `MCBEWS_BRIDGE`
发送聊天内容 `FRAME`：

```text
MCBEWS2|sender.session.sequence|index/total|content
```

- `MCBEWS_BRIDGE` 只是传输归属，不是认证。
- 分片前，将 JSON 中所有字面量 `@` 编码为 `\u0040`，避免 Minecraft 的
  `scriptevent` 与 `tell` 消息解析器展开选择器。收齐后再解码 JSON，恢复原始字符串。
  转义后的字节计入消息、分片与缓存限制。
- 仅在 Unicode 标量边界切分，内容可包含竖线和换行。
- `index` 与 `total` 必须是规范正十进制数，从 1 开始，最多 1024 片。
- 收齐后才拼接解码。相同重复片忽略且不刷新空闲期限；冲突重复或总数变化会释放消息并拒绝该传输编号。
- 同一连接、来源和编号在终止编号保留期内只完成一次。

清单限制是硬上限，只能降低：

| 范围 | 默认值 |
| --- | --- |
| 下行最终 `commandLine` | 461 个 UTF-8 字节 |
| 上行完整载体帧 | 256 个 Unicode 码点且不超过 1024 个 UTF-8 字节 |
| 单条逻辑消息 | 65536 字节 |
| 单来源未完成消息 | 16 个传输编号 / 131072 字节 |
| 单连接未完成消息 | 64 个传输编号 / 262144 字节 |
| 进程或世界共享池 | 256 个传输编号 / 1048576 字节 |
| 活动编号与终止编号记录总数 | 2048 |

下行预算是经验值，部署可以降低。上行独立测量完整帧，不继承 461 字节预算。

空闲期限为 30000 毫秒，每次接收只采样一次毫秒时钟，恰好到期即过期。
完成、冲突、容量拒绝、过期、取消都会释放字节，并保留终止编号 30000 毫秒。
活动编号加终止记录达到上限时，新编号被拒绝，不提前淘汰旧记录。
断连清除该连接的缓存与终止编号。

缺片不交付部分消息。这是有界重复抑制，不是重传，也不保证恰好执行一次。
重组不执行业务；原生命令保持原子，超长即拒绝。

## 格式选择

在相同样本、同一最坏载体头部和最终命令字节统计下比较后，选择对象 JSON，
而非八位置数组。数组在这些样本中没有减少命令数，还需要存在位掩码来区分字段缺失与
`null`，验证和扩展更复杂。这是确定性的字节比较，不代表时延或实机性能。

当前对照表由 `python tools/compare_v2_formats.py` 生成。

## 接入点

Python：

```python
server = McbeServer()
bridge = server.v2

response = await bridge.request(connection_id, "demo.echo", {"text": "hi"})
await bridge.notify(connection_id, "demo.event", {"value": 1})
stream = await bridge.stream(connection_id, "demo.ticks", {"count": 3})
```

`bridge.ready(connection_id)` 等待协议协商并返回协商连接（版本、功能与端点）。
钩子已经运行在 `READY_V2` 之后，通常不需要再调用。

底层仍可使用 `profiles.mcbews_v2.codec` 编解码、`transport` 分片、下行载荷与
`ReassemblyPool`。附加包对应模块位于 `bridge/v2/codec` 与 `bridge/v2/transport`。
发送循环每次等待一片，保持顺序，失败或取消后停止。

附加包路由器提供 `getV2Endpoint(epoch)`、`registerCapability` 与
`registerV2Stream`。同一方法的顺序组不能重入；组执行期间的普通调用以
`LIMIT_EXCEEDED` 拒绝，但发往其他组的反向调用仍允许。通知不产生错误回复。

Python 的 `V2Bridge(timeout_seconds=...)` 对调度器排队、分片发送和等待回复设置一个总期限，
协商使用独立期限。超时、关闭、取消会停止未发送片段；已经交给传输层的帧可能继续完成，
无法撤回。

处理器分别接收传输归属与元数据；元数据身份不可信。断连使所有等待请求失败，
释放连接分片；空闲未完成消息按期限过期。

## 持续请求流 {#request-streams}

Python 用 `bridge.streams.register(name, handler)` 注册异步生成器，
接收 `(name, params, CapabilityContext)` 并逐项产生 JSON。

```python
stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

附加包通过路由器的 `registerV2Stream(name, handler)` 注册返回 `AsyncIterable`
的生产者。`await endpoint.stream(name, params, options)` 返回异步迭代器；
提前退出 `for await` 会调用 `return()` 并取消。注销只影响后续调用。

| 消息种类 | `v`、`kind` 以外的必需字段 | 方向 |
| --- | --- | --- |
| `stream_open` | `id`、`method`、`params` | 调用方到生产者 |
| `stream_item` | `id`、`seq`、`item` | 生产者到调用方 |
| `stream_end` | `id`、`seq` | 生产者到调用方 |
| `stream_error` | `id`、`seq`、`error` | 生产者到调用方 |
| `stream_cancel` | `id` | 调用方到生产者 |

所有种类都允许可选 `meta`，拒绝其他字段。编号属于调用方，回复沿用它。
每条逻辑消息另用发送方的新传输编号，可经通用分片层拆成多片。
逻辑项序号 `seq` 与片段序号独立，第一项从 0 开始；结束或失败消息携带下一项预期序号。
空流以序号 0 结束，不需要预先提供总长度。

乱序、重复逻辑项或结束时缺项会以 `INVALID_MESSAGE` 失败，并取消生产者。
未知、迟到消息不创建新流；终止编号保留期内，重复开流不会重启生产者。
终止编号表已满时，新开流明确拒绝但不再存储编号。

正常结束保留已缓存项供消费。失败、取消或超载清空缓存，终止结果只确定一次。
生产异常映射为 `HANDLER_FAILED`，空闲超时映射为 `TIMEOUT`。
取消停止后续发送并关闭生产迭代器，不撤销已经执行的世界修改。

| 流资源 | 默认上限 |
| --- | --- |
| 单端点活跃流 | 双向合计 16 条，包含已结束但仍有未读项的流 |
| 单消费者缓存 | 32 项，且逻辑项紧凑 JSON 合计不超过 131072 个 UTF-8 字节 |
| 单端点缓存 | 262144 字节 |
| 活动生产者与重复开流终止记录 | 合计 2048，终止记录保留 30000 毫秒 |
| 空闲期限 | 开流、产生下一项、发送单条逻辑消息各 30000 毫秒 |

生产者逐项等待发送，收到一项会刷新消费者计时；这不是整条流的总时长限制。
Python 的 `V2Bridge(stream_limits=...)` 与 TypeScript 的 `V2EndpointOptions.streamLimits`
只能降低上限。

慢消费者超限会以 `LIMIT_EXCEEDED` 明确结束并发送取消。
固定发送间隔与传输节奏不等于接收方背压，当前没有接收额度机制。
控制发送同样有容量上限；控制容量或传输耗尽时，对端空闲超时仍会限制生命周期。
未完成的传输分片继续受通用分片层的空闲期限与配额约束，逻辑流取消无需先解码不完整分片；
断连清理连接资源。

## 信任边界

载体来源类型与路由令牌不能证明 WebSocket 身份，也不授予权限，宿主须独立认证与授权。
关闭消息丢失时依赖租约清理；已经交给传输层的命令不能撤回。
