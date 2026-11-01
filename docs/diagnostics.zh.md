# 追踪与数据包诊断

诊断会覆盖一条连接从传输建立、协议协商、请求处理到关闭的全过程。
SDK 的诊断通道默认只记录摘要，要记录完整数据包，需要显式开启。

## 生命周期阶段

每条连接至少记录：

| 阶段 | 记录内容 |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | 连接标识与传输建立 |
| `NEGOTIATING` | 尝试编号、协议版本、功能集合、超时 |
| `READY_V2` | 已就绪代次、协商功能、会话 |
| `REJECTED` | 原因、关闭码，以及传输是否由本端关闭 |
| `SESSION_INVALID` | 撤销或过期原因 |
| `CLOSING` / `CLOSED` | 关闭码、限长的关闭原因、发起方、清理摘要 |

协议协商失败时使用的原因标识是固定的：`UNSUPPORTED_PROTOCOL`、`NEGOTIATION_TIMEOUT`、
`NEGOTIATION_CONFIRMATION_TIMEOUT`、`NEGOTIATION_BUSY`、`INVALID_NEGOTIATION`
与 `SESSION_INVALID`。被拒绝的连接不算业务层面的断连，因此不会触发成对的业务
`on_connected` / `on_disconnected`。

因协议协商被拒绝而关闭连接时，关闭码为 `1008`，关闭原因最多 123 个 UTF-8 字节。
如果观察不到是谁发起的关闭，或者关闭的原因是什么，就记录为 `unknown`，不要自行猜测。

遇到既没有关闭码也没有原因的 EOF 时，请保留传输错误，并把原因记录为未知，直到能确认来源为止。

## 默认摘要与原始数据包

SDK 的诊断通道默认记录标识、阶段、大小和帧的分类，不记录业务文本、命令和完整数据包。
示例服务还会在 INFO 级别另外记录命令、能力参数和结果。这些属于应用日志，
独立于诊断通道，不受诊断脱敏配置的约束。

示例的 `--log-raw` 选项会额外开启完整的出站数据包日志，日常使用时请保持关闭。
在处理玩家的私人数据之前，也请检查应用层日志。

```python
from mcbe_ws_sdk import DiagnosticSettings, McbeServer, trace_scope

async def inspect_record(record):
    pass  # 交给宿主收集器；同步工作必须简短

server = McbeServer(
    diagnostics=DiagnosticSettings(
        full_packets=False,           # 需要时显式开启
        connection_ids=frozenset({"connection-uuid"}),
        secrets=("a-secret-value",),
        queue_maxsize=256,
    ),
    diagnostic_observers=(inspect_record,),
)

with trace_scope(trace_id="host-operation"):
    pass  # await state.command_client.run(...) 或 server.v2.request(...)
```

创建服务实例不会改动全局日志配置。宿主应显式调用 `configure_logging()`，或自行配置日志。

## 关联与阶段

`current_trace()` 返回不可变的追踪信息，包括 `connection_id`、`player`、`request_id` 和 `trace_id`。
托管的业务任务、事件任务、发送任务以及 `enqueue_response()` 都会继承提交时的上下文。
直接写入公开的 `response_queue` 仍然可用，但不会带上原操作的上下文。

每条原生命令都有自己的 `request_id`。能力请求使用逻辑编号，它与外层命令请求的 UUID
（`wire_request_id`）是两个不同的编号，二者都继承当前的追踪上下文。
同一个追踪编号可以串联多个请求。收到回复时，SDK 先恢复该待处理请求原来的玩家和追踪上下文，
再完成请求与回复的配对。没有对应请求的主动收包，会建立新的本地追踪上下文。

| 阶段 | 含义 |
| --- | --- |
| `received` | 收到数据包，包括格式非法的输入和外部输出 |
| `processing_started/completed/rejected/failed` | SDK 托管的业务处理开始、完成、被拒绝或失败 |
| `request_registered` | 发送之前，原生命令或能力请求的等待项已登记 |
| `enqueued` | 调度器已接受入队，不表示已送达 |
| `prepared` | 即将调用传输层发送，不表示已送达 |
| `transport_sent/transport_failed` | 传输调用正常返回或抛出错误 |
| `remote_success/remote_failure` | 原生 `statusCode` 或远端协议结果 |
| `remote_response` | 原生回复没有 `statusCode`，不判定成功 |
| `request_failed/request_timeout` | 本地发送失败、远端错误帧、取消、断连或超时 |

传输层发送完成，不代表游戏已经执行了命令，也不代表附加包的业务处理成功。原生命令返回非零 `statusCode`
时，调用方仍会原样收到；MCBEWS/2 的远端错误则会抛出带 `code` 的 `RemoteCallError`。
诊断只负责给这些结果分类，不会改变调用本身的返回值和异常。回复有可能比传输调用的返回更早到达；
超时或取消也不意味着远端已经回滚。

## 脱敏与资源

`full_packets=True` 会在 `received`（入站）和 `prepared`（出站）阶段提供双向的完整数据包。
`connection_ids` 与 `request_ids` 同时生效，也就是两个条件都要满足；
请求筛选既可以匹配逻辑编号，也可以匹配线格式编号，空集合表示不做筛选。

`secret_keys` 按 JSON 键名遮盖对应的值，键名不区分大小写，默认包括
`password`、`token`、`secret`、`authorization`。
`secrets` 则替换字符串中出现的指定字面量，即使命令或桥接 JSON 被嵌在字符串里也同样有效。
脱敏发生在写日志和调用观察者之前，每个观察者拿到的都是各自独立的副本。

完整数据包默认最大为 1 MiB。超过上限时只保留元信息，并标记 `packet_omitted="size_limit"`。
准备记录的过程中，如果出现意外的解析、编码或脱敏失败，整条记录都会被丢弃：
既不会记录其内容，也不会退回去记录未脱敏的原文。
脱敏处理的嵌套深度上限为 64 层，诊断记录本身也计入其中。

`state.diagnostics` 提供 `dropped_count`、`preparation_failures`、`observer_failures` 和
`task_count` 四个计数。准备失败的记录也会计入丢弃数量。队列里最多保留 `queue_maxsize` 条等待中的记录，
外加一条正在执行的记录；队列已满时，新的诊断记录会被立即丢弃，不会等待。

日志和观察者抛出的异常都会被隔离。较慢的异步观察者只会延迟之后的诊断，不会阻塞读取、
回复配对或传输。观察者按注册顺序在同一条通道上执行。关闭时，等待中的记录会被丢弃，
SDK 会取消并等待观察任务结束，不使用后台线程。
同步的重计算，或会阻塞的日志处理器，仍然会阻塞事件循环，需要由宿主自行移出；
观察者也必须配合异步任务的取消。

用于关联最近传输确认的上下文缓存有大小上限。条目被淘汰，或者遇到重复、迟到的回复时，
可能丢失诊断上下文；但待处理请求表始终是独立且权威的，诊断记录丢失不会影响回复配对。

## 回声分类

回声只根据 `PlayerMessage` 的来源判断：发送者是 `External` 或其本地化名称“外部”，
并且 `type` 没有明确标为 `player` 或 `chat`。这个分类只是用来过滤噪声：它不比较消息内容，
不能证明身份，也不会与某一条具体的下行消息对应。玩家发送了相同的文本，仍然属于普通输入。

命令响应和错误总是先按类型完成请求配对，不会被当成回声丢掉。
完整的原始诊断会保留 `external_echo` 记录；示例通过 `is_external_echo()` 过滤外部输出，
避免自己回复自己。

`RAW_INBOUND` 和 `RAW_OUTBOUND` 的原有参数、触发顺序以及未脱敏的数据都保持不变，
`RAW_OUTBOUND` 仍然是沿用至今的“发送前”事件。如果需要经过脱敏、有大小限制，
并且带有明确发送结果的观察数据，请使用独立的诊断通道。

## 日志故障

构建或投递诊断记录时出现故障，不能中断请求处理。被丢弃的记录和准备失败都会计数，
并可以通过 `state.diagnostics` 导出。协议错误和传输错误依然可见，诊断不能代替错误处理。
