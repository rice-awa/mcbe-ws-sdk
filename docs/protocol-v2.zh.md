# MCBEWS/2 协议

当前 SDK 与配套附加包使用 **V2 协议（MCBEWS/2）**。
连接要先完成协议协商，之后才会处理业务消息；连接就绪之前收到的业务帧会被丢弃，不会缓存后再重放。

安装包内的 `profiles/mcbews_v2/manifest.json` 与 `vectors.json` 是标识、字段规则、
错误、限制和协商帧的权威来源。本页负责解释协议，不会覆盖清单。

MCBEWS/2 借鉴了 JSON-RPC 的请求/响应形态，但与 JSON-RPC 并不兼容。

下文会反复用到三个术语：

- **代次**（epoch）标识一次协商成功的会话，每次重连都会得到新的代次。
- **载体**（carrier）帧是真正经由 Minecraft 传输的文本，每个载体帧装着一条逻辑消息的一个分片：
  下行是 script event，上行是聊天消息。
- **终止编号**（tombstone）是传输编号完成或被拒绝之后保留 30000 毫秒的一条记录，
  接收方据此识别并忽略迟到的重复帧。

## 连接建立

```text
WS_TRANSPORT_CONNECTED → NEGOTIATING → READY_V2 → CLOSING → CLOSED
                              └────────→ REJECTED ────────→ CLOSED
```

| 状态 | 行为 |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | 传输已经建立，但连接还没有为业务做好准备 |
| `NEGOTIATING` | 只接受协商帧、必要的控制帧，以及已匹配请求的原生 `commandResponse` / `error`；业务帧会被丢弃，不会缓存 |
| `READY_V2` | 发布业务 `CONNECTED`，调用 `on_connected`，并开始路由玩家消息和载体帧 |
| `REJECTED` | 记录原因，最多尽力发送一次简短提示，然后关闭 |
| `CLOSING` / `CLOSED` | 释放请求、队列、任务、租约和分片 |

协议协商失败时，WebSocket 以 `1008` 关闭，关闭原因是一个简短的标识，最多 123 个 UTF-8 字节。
原因标识是固定的：

| 原因 | 含义 |
| --- | --- |
| `UNSUPPORTED_PROTOCOL` | 对端使用已退休协议或不支持的版本 |
| `NEGOTIATION_TIMEOUT` | 没有附加包、没有回复，或脚本未加载 |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | 已应答挑战，但最终确认未完成 |
| `NEGOTIATION_BUSY` | 协商容量或临时挑战名额已满 |
| `INVALID_NEGOTIATION` | 控制帧格式错误、不匹配或已过期 |
| `SESSION_INVALID` | 已就绪会话被撤销或过期 |

被拒绝的连接不会触发业务 `on_connected`，因此也不会出现单独、没有配对的业务 `on_disconnected`。
重连会开始新的协商尝试并得到新的代次；已关闭的尝试迟到的帧，无法让已关闭的连接恢复。

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

`attempt` 与 `epoch` 都是新生成的路由令牌，由 1–24 个 ASCII 字符组成。代次用来标识会话，并不是认证。
每条连接同一时间只处理一个未完成的协商尝试，挑战必须匹配之后才能激活会话。
繁忙或已过期的尝试会被终止，重试次数有上限。

### 已退休路由

`mcbews:bridge_req`、`mcbews:session_req` 与 `mcbews:text_resp` 已经退休。
附加包只会记录并拒绝它们，不会触发任何业务逻辑：不解析、不排队、不回复，也不分发。
`MCBEWS|BRIDGE`、`MCBEWS|UI_CHAT`、`MCBEWS|SESSION` 与 `MCBEWS|TOOL_APPROVE`
既不再产生，也不再接受。

附加包无法关闭旧服务持有的 WebSocket，只能拒绝这个旧服务发来的业务帧。
新服务遇到旧附加包或没有附加包时，会通过超时拒绝连接，并关闭自己持有的传输。

原生基岩 WebSocket 里的 `header.version = 1` 属于游戏自己的协议，与 MCBEWS/1 无关，
不能用它来识别旧客户端。

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

回复沿用请求的编号。由于编号里包含发起方，双方同时发起请求也不会冲突。
通知没有逻辑编号，也不会创建回复等待项。
每条载体消息，包括回复和通知，都使用实际发送方新生成的传输编号，
所以一条回复的逻辑编号可能属于对端。重连后不会恢复编号或分片，也不会自动重放有副作用的操作。

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

未知的保留字段会被拒绝，元数据同样遵守上面的深度和数值规则。
调用方不能自己设置保留字段，追踪编号需要通过专用的参数传入。

## 双向载体与资源

下行帧通过 `scriptevent mcbews:packet_v2 FRAME` 发送；上行帧由模拟玩家 `MCBEWS_BRIDGE`
以聊天内容 `FRAME` 的形式发出：

```text
MCBEWS2|sender.session.sequence|index/total|content
```

- `MCBEWS_BRIDGE` 只是传输归属，不是认证。
- 分片前，将 JSON 中所有字面量 `@` 编码为 `\u0040`，避免 Minecraft 的
  `scriptevent` 与 `tell` 的消息解析器把它展开成选择器。接收方先把分片收齐，再解码 JSON，
  从而还原原始字符串。转义之后的字节同样计入消息、分片和缓存的限制。
- 只在 Unicode 标量的边界处切分，内容里可以包含竖线和换行。
- `index` 与 `total` 必须是规范的正十进制数，从 1 开始计数，一条消息最多 1024 片。
- 必须收齐所有分片，才会拼接并解码。内容相同的重复分片会被忽略，也不会刷新空闲期限；
  内容冲突的重复分片，或者总数发生变化，会释放该消息并拒绝这个传输编号。
- 同一个连接、来源和编号，在终止编号的保留期内只会完成一次。

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

下行预算是实测得出的经验值，部署时可以调低。上行则独立测量完整的帧，不继承 461 字节的预算。

空闲期限为 30000 毫秒。每次接收只读取一次毫秒时钟，恰好到达期限时就算过期。
消息完成、冲突、因容量被拒绝、过期或被取消时，都会释放所占的字节，
并把终止编号保留 30000 毫秒。活动编号加上终止记录达到上限后，新编号会被拒绝，
但不会为了腾出空间而提前淘汰旧记录。断连会清除该连接的缓存和终止编号。

缺少分片的消息不会被部分交付。这一层做的是在固定限额内抑制重复，
并不负责重传，也不保证恰好执行一次。重组本身不会执行业务；原生命令不会被拆分，超长时直接拒绝。

## 格式选择

协议采用对象 JSON，而不是八位置数组。这个选择来自一次对比：用相同的样本，
在同样的最坏情况载体头部和同样的最终命令字节统计下比较。结果是数组并没有减少命令数，
还需要用存在位掩码来区分“字段缺失”与 `null`，验证起来也更复杂。
这是一次确定性的字节开销比较，不代表时延或实机上的性能。

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

`bridge.ready(connection_id)` 会等待协议协商完成，并返回协商得到的连接（版本、功能和端点）。
钩子本来就运行在 `READY_V2` 之后，所以通常不需要再调用它。

底层的部件仍然可以直接使用：`profiles.mcbews_v2.codec` 负责编码和解码，
`transport` 负责分片、下行载荷和 `ReassemblyPool`。附加包里对应的模块是
`bridge/v2/codec` 与 `bridge/v2/transport`。发送循环每次只等待一个帧，保持顺序，
遇到失败或取消就停止。

附加包的路由器提供 `getV2Endpoint(epoch)`、`registerCapability` 与 `registerV2Stream`。
同一个方法所在的顺序组不能重入：该组执行期间，对它的新调用会以 `LIMIT_EXCEEDED` 拒绝，
但发往其他组的反向调用仍然允许。通知不会产生错误回复。

Python 的 `V2Bridge(timeout_seconds=...)` 为一次请求设置一个总期限，
覆盖进入调度器队列、发送分片和等待回复这几个阶段；协商则使用独立的期限。
超时、关闭或取消会停止尚未发送的分片，但已经交给传输层的帧仍可能送达，无法撤回。

处理器会分别收到传输归属和元数据，其中元数据里的身份不可信。断连会让所有等待中的请求失败，
并释放该连接的分片；空闲的未完成消息在超过期限后过期。

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

所有种类都允许带可选的 `meta`，其他字段一律拒绝。编号属于调用方，回复沿用这个编号。
每条逻辑消息另外使用发送方新生成的传输编号，并可以经过通用的分片层拆成多片。
逻辑项的序号 `seq` 与分片序号相互独立：第一项从 0 开始，结束或失败消息携带下一项预期的序号。
流的总长度不需要预先提供，空流以序号 0 结束。

乱序、重复的逻辑项，或结束时缺少某一项，都会以 `INVALID_MESSAGE` 失败，并取消生产者。
未知的、迟到的消息不会创建新流；在终止编号的保留期内，重复的开流请求不会让生产者重新启动。
终止编号表已满时，新的开流请求会被拒绝，并且不再存储这个编号。

正常结束时，已经缓存的项仍可供消费者读取。失败、取消或超载则会清空缓存，
消费者只会得到一次最终结果。生产者抛出异常对应 `HANDLER_FAILED`，空闲超时对应 `TIMEOUT`。
取消会停止后续发送并关闭生产者的迭代器，但不会撤销已经执行的世界修改。

| 流资源 | 默认上限 |
| --- | --- |
| 单端点活跃流 | 双向合计 16 条，包含已结束但仍有未读项的流 |
| 单消费者缓存 | 32 项，且逻辑项紧凑 JSON 合计不超过 131072 个 UTF-8 字节 |
| 单端点缓存 | 262144 字节 |
| 活动生产者与重复开流终止记录 | 合计 2048，终止记录保留 30000 毫秒 |
| 空闲期限 | 开流、产生下一项、发送单条逻辑消息各 30000 毫秒 |

生产者每次只发送一个逻辑项，消费者每收到一项都会刷新自己的空闲计时，
所以空闲期限并不是对整条流总时长的限制。上限只能调低：
Python 用 `V2Bridge(stream_limits=...)`，TypeScript 用 `V2EndpointOptions.streamLimits`。

消费过慢而超出限制时，流会以 `LIMIT_EXCEEDED` 明确结束，并向生产者发送取消。
固定的发送间隔和传输节奏并不等于接收方的背压，当前也没有接收额度机制。
控制帧的发送同样有容量上限；控制容量或传输耗尽时，对端的空闲超时仍会限制流的生命周期。
尚未完成的传输分片依然受通用分片层的空闲期限和配额约束，取消逻辑流时也不必先解码不完整的分片。
断连会清理该连接的资源。

## 信任边界

载体的来源类型和路由令牌既不能证明 WebSocket 对端的身份，也不授予任何权限，
宿主必须自行完成认证和授权。关闭消息丢失时，依靠租约到期来清理；
已经交给传输层的命令无法撤回。
