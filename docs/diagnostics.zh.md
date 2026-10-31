# 追踪与数据包诊断

facade 在两种调度模式下均为每条连接创建独立有界诊断通道。默认 DEBUG
仅记录标识、阶段、大小及分类，不记录业务文本、命令或完整包。
构造不会修改全局日志配置；宿主显式调用 configure_logging() 或配置自己的日志。

~~~python
from mcbe_ws_sdk import DiagnosticSettings, McbeServerFacade, trace_scope

async def inspect_record(record):
    pass  # 转交宿主收集器，同步工作需简短。

facade = McbeServerFacade(
    diagnostics=DiagnosticSettings(
        full_packets=True,
        connection_ids=frozenset({"connection-uuid"}),
        secrets=("a-secret-value",),
        queue_maxsize=256,
    ),
    diagnostic_observers=(inspect_record,),
)

with trace_scope(trace_id="host-operation"):
    pass  # 在回调中连续等待原生命令或能力请求，共用 trace。
~~~

## 关联与阶段

current_trace() 提供不可变 connection_id、player、request_id、trace_id。
业务、事件、发送任务及 enqueue_response() 继承提交时上下文；直接写入公开
response_queue 保留旧行为，但无法携带原操作上下文。

每个原生命令有独立 request_id；能力请求的逻辑编号与外层 commandRequest UUID
（wire_request_id）分开。同一 trace 可串联多个请求；回复先恢复待处理请求的玩家、
trace，再完成配对。Python 与日志的 trace_id 对应 traceId，不加入 MCBEWS/1 线格式。
无关联的收包建立新的本地 trace。

| 阶段 | 含义 |
| --- | --- |
| received | 收到包，含非法输入及外部输出 |
| processing_started/completed/rejected/failed | 业务处理开始／完成／拒绝／失败 |
| request_registered | 请求已在发送前登记 |
| enqueued | 可靠调度器接受排队，不表示送达 |
| prepared | 即将调用传输，不表示送达 |
| transport_sent/transport_failed | 传输调用返回／抛错 |
| remote_success/remote_failure | 原生 statusCode／能力 ok 的业务结果 |
| remote_response | 原生回复未含 statusCode，不宣称成功 |
| request_failed/request_timeout | 发送、远端 error 帧、取消、断连或超时等失败 |

传输完成不等于远端业务成功。非零 statusCode 仍原样返回，能力 ok=False 仍返回
原字典；诊断不改变旧合同。回复可能早于传输调用返回；超时或取消不保证远端回滚。

## 隐私与资源

full_packets=True 在 received／prepared 中提供双向包。connection_ids 与
request_ids 同时生效；请求筛选可匹配逻辑或线端编号，空集合表示不筛选。
secret_keys 按大小写无关 JSON 键遮盖值（默认 password/token/secret/authorization）；
secrets 替换字符串内的指定字面量，命令或桥接 JSON 嵌在字符串时也适用，宿主应配置
这些业务密钥值。脱敏先于日志和观察者，每个观察者收到独立副本。
完整包默认最大 1 MiB；超限保留元信息并标记 packet_omitted="size_limit"。

准备记录时的意外解析、编码或脱敏失败会丢弃整条记录，不记录其内容、不回退到未脱敏原文；
脱敏嵌套上限为 64 层，包含诊断记录本身。

state.diagnostics 提供 dropped_count、preparation_failures、observer_failures、task_count；
准备失败也计入 dropped_count。最多
queue_maxsize 条等待记录加一条执行中记录，满时立即丢诊断、不等待；日志／观察者
异常被隔离。慢异步观察者仅延迟后续诊断，不阻塞读取、配对或传输。关闭丢弃等待项，
取消并等待观察任务，无后台线程。同步重计算或阻塞日志处理器仍会阻塞事件循环，宿主
应自行移出；观察者必须配合 asyncio 取消。

最近线端确认上下文缓存至多 queue_maxsize 项；淘汰、重复或迟到回复可能失去诊断
上下文。待处理原生／能力请求表始终独立且权威，诊断丢失不影响配对。

## 回声与兼容

只按 PlayerMessage 来源分类：External／外部发送者且 type 未明确为 player/chat。
不比较内容，不证明身份或匹配某条下行；玩家相同文本仍是普通输入。
commandResponse/error 总是按类型先配对，不能被当成回声吞掉。完整原始诊断保留
external_echo 记录，示例统一用 is_external_echo() 过滤外部输出，避免自己回复自己。

RAW_INBOUND／RAW_OUTBOUND 原参数、顺序、未脱敏数据保持不变；RAW_OUTBOUND
仍是旧的发送前事件。旧模式内联观察行为不变，需要有界脱敏及明确发送结果时使用
独立诊断通道。
