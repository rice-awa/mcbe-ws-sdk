# 追踪与数据包诊断

诊断覆盖传输建立、协议协商、请求处理和关闭过程。
SDK 诊断通道默认记录摘要，完整数据包需要显式开启。

## 生命周期阶段

每条连接至少记录：

| 阶段 | 记录内容 |
| --- | --- |
| `WS_TRANSPORT_CONNECTED` | 连接标识与传输建立 |
| `NEGOTIATING` | 尝试编号、协议版本、功能集合、超时 |
| `READY_V2` | 已就绪代次、协商功能、会话 |
| `REJECTED` | 原因、关闭码，以及传输是否由本端关闭 |
| `SESSION_INVALID` | 撤销或过期原因 |
| `CLOSING` / `CLOSED` | 关闭码、有界关闭原因、发起方、清理摘要 |

稳定的协议协商原因包括 `UNSUPPORTED_PROTOCOL`、`NEGOTIATION_TIMEOUT`、
`NEGOTIATION_CONFIRMATION_TIMEOUT`、`NEGOTIATION_BUSY`、`INVALID_NEGOTIATION`
与 `SESSION_INVALID`。被拒绝的连接不是业务断连，不会产生业务
`on_connected` / `on_disconnected` 配对。

协议协商拒绝的关闭码为 `1008`，关闭原因最多 123 个 UTF-8 字节。
无法观测关闭发起方或原因时记录 `unknown`，不要猜测。

遇到没有关闭码或原因的 EOF 时，保留传输错误，并将原因记录为未知，直到能确认来源。

## 默认摘要与原始数据包

SDK 诊断通道默认记录标识、阶段、大小与帧分类，省略业务文本、命令和完整数据包。
示例服务另外在 INFO 级别记录命令、能力参数和结果；这些应用日志独立于诊断通道，
不受其脱敏配置控制。

示例的 `--log-raw` 会额外开启完整出站数据包日志，日常使用时保持关闭。
处理私人玩家数据前，也要检查应用层日志。

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

构造入口不会修改全局日志配置。宿主应显式调用 `configure_logging()` 或配置自己的日志。

## 关联与阶段

`current_trace()` 提供不可变的 `connection_id`、`player`、`request_id`、`trace_id`。
业务、事件、发送任务及 `enqueue_response()` 继承提交时的上下文。
直接写入公开的 `response_queue` 保持可用，但不携带原操作上下文。

每条原生命令有独立 `request_id`。能力请求的逻辑编号与外层命令请求 UUID
（`wire_request_id`）分开。同一追踪编号可串联多个请求；回复先恢复待处理请求的玩家和
追踪上下文，再完成关联。无关联的收包会建立新的本地追踪上下文。

| 阶段 | 含义 |
| --- | --- |
| `received` | 收到包，包含非法输入与外部输出 |
| `processing_started/completed/rejected/failed` | 业务处理开始、完成、拒绝或失败 |
| `request_registered` | 请求已在发送前登记 |
| `enqueued` | 调度器接受排队，不表示送达 |
| `prepared` | 即将调用传输，不表示送达 |
| `transport_sent/transport_failed` | 传输调用返回或抛错 |
| `remote_success/remote_failure` | 原生 `statusCode` 或远端协议结果 |
| `remote_response` | 原生回复没有 `statusCode`，不宣称成功 |
| `request_failed/request_timeout` | 发送、远端错误帧、取消、断连或超时等失败 |

传输完成不等于远端业务成功。非零 `statusCode` 仍原样返回；MCBEWS/2 远端错误抛出带
`code` 的 `RemoteCallError`。诊断不改变调用合同。回复可能早于传输调用返回；
超时或取消不保证远端回滚。

## 脱敏与资源

`full_packets=True` 在 `received`、`prepared` 阶段提供双向数据包。
`connection_ids` 与 `request_ids` 同时生效；请求筛选匹配逻辑编号或线格式编号，空集合表示不筛选。

`secret_keys` 按不区分大小写的 JSON 键遮盖值，默认包括
`password`、`token`、`secret`、`authorization`。
`secrets` 替换字符串中的指定字面量，命令或桥接 JSON 嵌在字符串中时也适用。
脱敏先于日志和观察者，每个观察者收到独立副本。

完整包默认最大 1 MiB，超限保留元信息并标记 `packet_omitted="size_limit"`。
准备记录时，意外的解析、编码或脱敏失败会丢弃整条记录，不记录其内容，也不回退到未脱敏原文。
脱敏嵌套上限为 64 层，包含诊断记录本身。

`state.diagnostics` 提供 `dropped_count`、`preparation_failures`、`observer_failures`、
`task_count`。准备失败也计入丢弃数量。最多保留 `queue_maxsize` 条等待记录与一条执行中记录；
队列满时立即丢弃诊断，不等待。

日志与观察者异常被隔离。慢异步观察者只延迟后续诊断，不阻塞读取、回复关联或传输。
观察者按注册顺序在单条通道执行。关闭会丢弃等待项，取消并等待观察任务，不使用后台线程。
同步重计算或阻塞日志处理器仍会阻塞事件循环，宿主应自行移出；观察者须配合异步任务取消。

最近传输确认的上下文缓存有界。淘汰、重复或迟到回复可能失去诊断上下文；
待处理请求表始终独立且权威，诊断丢失不影响回复关联。

## 回声分类

只按 `PlayerMessage` 来源分类：发送者为 `External` 或“外部”，且 `type` 未明确为
`player` 或 `chat`。此分类不比较内容、不证明身份，也不匹配某一条下行消息；
玩家发送相同文本仍是普通输入。

命令响应与错误始终先按类型关联，不能被当作回声吞掉。
完整原始诊断保留 `external_echo` 记录，示例通过 `is_external_echo()` 过滤外部输出，
避免自己回复自己。

`RAW_INBOUND`、`RAW_OUTBOUND` 的原参数、顺序和未脱敏数据保持不变；
`RAW_OUTBOUND` 仍是旧的发送前事件。需要有界脱敏与明确发送结果时，使用独立诊断通道。

## 日志故障

构建或投递诊断记录失败不得中断请求处理。丢弃记录与准备失败会被计数，并可通过
`state.diagnostics` 导出。协议或传输错误仍然可见；诊断不替代错误处理。
