# 追踪与数据包诊断

本文描述尚未发布的当前源码；PyPI `0.2.1` 不包含本轮新增接口。
安装与兼容说明见[迁移指南](migration.md)。

两种入口及调度模式都为每条连接创建独立的有界诊断通道。
默认调试日志只记录标识、阶段、大小及分类，不记录业务文本、命令或完整数据包。
构造入口不会修改全局日志配置，宿主应显式调用 `configure_logging()` 或配置自己的日志。

```python
from mcbe_ws_sdk import DiagnosticSettings, McbeServer, trace_scope

async def inspect_record(record):
    pass

server = McbeServer(
    diagnostics=DiagnosticSettings(
        full_packets=True,
        connection_ids=frozenset({"connection-uuid"}),
        secrets=("a-secret-value",),
        queue_maxsize=256,
    ),
    diagnostic_observers=(inspect_record,),
)

with trace_scope(trace_id="host-operation"):
    pass
```

观察者可将记录交给宿主收集器，同步工作必须简短。
在同一个 `trace_scope` 中连续等待命令或能力调用，可共用追踪上下文。

## 关联与阶段

`current_trace()` 提供不可变的 `connection_id`、`player`、`request_id`、`trace_id`。
业务、事件、发送任务及 `enqueue_response()` 继承提交时的上下文。
直接写入公开的 `response_queue` 保持旧行为，但不携带原操作上下文。

每条原生命令有独立 `request_id`。能力请求的逻辑编号与外层命令请求 UUID
（`wire_request_id`）分开。同一追踪编号可串联多个请求；回复先恢复待处理请求的玩家和
追踪上下文，再完成关联。Python 与日志中的 `trace_id` 对应 `traceId`，不加入 MCBEWS/1 线格式。
无关联的收包会建立新的本地追踪上下文。

| 阶段 | 含义 |
| --- | --- |
| `received` | 收到包，包含非法输入与外部输出 |
| `processing_started/completed/rejected/failed` | 业务处理开始、完成、拒绝或失败 |
| `request_registered` | 请求已在发送前登记 |
| `enqueued` | 可靠调度器接受排队，不表示送达 |
| `prepared` | 即将调用传输，不表示送达 |
| `transport_sent/transport_failed` | 传输调用返回或抛错 |
| `remote_success/remote_failure` | 原生 `statusCode` 或能力 `ok` 的业务结果 |
| `remote_response` | 原生回复没有 `statusCode`，不宣称成功 |
| `request_failed/request_timeout` | 发送、远端错误帧、取消、断连或超时等失败 |

传输完成不等于远端业务成功。非零 `statusCode` 仍原样返回，v1 能力的 `ok: false`
仍保留在结果字典中；v2 请求的远端协议错误抛出 `RemoteCallError`。
诊断不改变调用合同。回复可能早于传输调用返回，超时或取消不保证远端回滚。

## 隐私与资源

`full_packets=True` 在 `received`、`prepared` 阶段提供双向数据包。
`connection_ids` 与 `request_ids` 同时生效；请求筛选匹配逻辑编号或线格式编号，空集合表示不筛选。
`secret_keys` 按不区分大小写的 JSON 键遮盖值，默认包括
`password`、`token`、`secret`、`authorization`。
`secrets` 替换字符串中的指定字面量，命令或桥接 JSON 嵌在字符串中时也适用；
宿主应配置这些业务密钥值。脱敏先于日志和观察者，每个观察者收到独立副本。
完整包默认最大 1 MiB，超限保留元信息并标记 `packet_omitted="size_limit"`。

准备记录时，意外的解析、编码或脱敏失败会丢弃整条记录，不记录其内容，也不回退到未脱敏原文。
脱敏嵌套上限为 64 层，包含诊断记录本身。

`state.diagnostics` 提供 `dropped_count`、`preparation_failures`、`observer_failures`、`task_count`。
准备失败也计入丢弃数量。最多保留 `queue_maxsize` 条等待记录与一条执行中记录；
队列满时立即丢弃诊断，不等待。日志与观察者异常被隔离。
慢异步观察者只延迟后续诊断，不阻塞读取、回复关联或传输。
关闭会丢弃等待项，取消并等待观察任务，不使用后台线程。
同步重计算或阻塞日志处理器仍会阻塞事件循环，宿主应自行移出；观察者须配合异步任务取消。

最近传输确认的上下文缓存最多 `queue_maxsize` 项。
淘汰、重复或迟到回复可能失去诊断上下文；待处理请求表始终独立且权威，诊断丢失不影响回复关联。

## 回声与兼容

只按 `PlayerMessage` 来源分类：发送者为 `External` 或“外部”，且 `type` 未明确为 `player` 或 `chat`。
此分类不比较内容、不证明身份，也不匹配某一条下行消息；玩家发送相同文本仍是普通输入。
命令响应与错误始终先按类型关联，不能被当作回声吞掉。
完整原始诊断保留 `external_echo` 记录，示例通过 `is_external_echo()` 过滤外部输出，避免自己回复自己。

`RAW_INBOUND`、`RAW_OUTBOUND` 的原参数、顺序和未脱敏数据保持不变；
`RAW_OUTBOUND` 仍是旧的发送前事件。旧模式的内联观察行为不变。
需要有界脱敏与明确发送结果时，使用独立诊断通道。
