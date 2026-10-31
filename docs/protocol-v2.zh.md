# MCBEWS/2 合同（显式使用）

权威来源是安装包内 profiles/mcbews_v2/manifest.json、vectors.json。
独立 MCBEWS/2 标识；借鉴交互模型但不兼容 JSON-RPC。
MCBEWS/1 资产、标识、导入、构造参数、默认行为不变。
v2 为显式接入；持续流要求协商 `stream/1`。

## 逻辑消息

按清单严格区分 request、response、notification 及下文流消息的字段；未知核心键拒绝。
v固定MCBEWS/2，method为清单ASCII模式，params/result可为任意合法JSON（含null）。
回复必须且只能有result/error之一。error含清单code、最多256 UTF-8字节的message、
可选任意JSON data；其他键拒绝。只允许±9007199254740991内有限整数，布尔不当数字。
科学记数表示的安全整数接受。字符串拒绝孤立代理项；重复对象键按最后值处理。
数值token按JSON.parse一致的IEEE-754解析结果校验：1.0000000000000001变为1，
1e-4000变为0。超长整数token归类INVALID_MESSAGE，不关闭进程级解析保护。
根深度0、最多16；整条紧凑JSON最多65536 UTF-8字节。

编号为origin.session.sequence：p=Python，a=addon；session每连接新建1–24位ASCII令牌，
sequence为1–10位正十进制。回复回显请求编号，双向发起不冲突。通知无逻辑编号、无等待。
所有传输（含通知/回复）另用实际发送方的新传输编号；回复逻辑编号可属对端。
重连不恢复编号、片段，不自动重放副作用。

meta最多4096紧凑JSON UTF-8字节；未知扩展递归保留。sdk.为核心保留前缀，仅允许
sdk.trace_id（清单令牌）、sdk.identity（不可信对象）、sdk.capabilities（最多16个不重复令牌）。
`stream/1` 启用下文的流式消息。身份从不自动授权；未知保留键拒绝。深度/数字规则同上。

## 双向载体和资源

下行是 scriptevent mcbews:packet_v2 FRAME，上行聊天内容是FRAME；
FRAME=MCBEWS2|发送方.session.sequence|index/total|content。只在Unicode标量边界切分，
内容可含竖线/换行。序号/总数是规范正十进制，从1起最多1024片；齐全才拼接解码。
同片重复忽略且不刷新TTL；重复冲突/变更总数释放并拒绝该编号。相同连接/来源/编号
在墓碑窗口只完成一次。分片不执行业务，缺片不交付部分消息，原生命令继续原子拒绝超长。

清单是硬上限，可降低。下行最终commandLine默认461 UTF-8字节、不可提高。
降低metadata_bytes/depth后，发送前和接收重组后的逻辑校验均生效。
上行独立限制整个FRAME：256码点/1024字节，不套用461。单消息65536字节。
来源16编号/131072字节，连接64/262144，进程/世界共享池256/1048576；所有连接共享池。
来源来自传输归属，不能来自meta身份。空闲TTL30000ms；完成/冲突/超限/过期/取消
释放字节并留30000ms墓碑；活动编号+墓碑最多2048，满时拒绝新编号、不提前淘汰墓碑。
断连清该连接缓存/墓碑，调用方使用新epoch并拒绝旧帧；接收和生命周期定时tick清理，
取消/关闭立即释放。这是有界重复抑制，不是重传或恰好一次执行。
每次接收只采样一次毫秒时钟，准确到达空闲TTL即过期。可归属到合法前缀/发送方传输编号
的超预算或非标量帧也释放活动传输并留墓碑；无法归属的输入不分配状态。

## 格式选择与兼容

执行 python tools/compare_v2_formats.py；相同7组样本、同一最坏头部、最终命令字节：
对象JSON/8位置数组分别为：中文请求292/261，null回复151/146，反向请求205/186，
通知238/217，错误217/213，最长编号250/231，4096字节meta 5189/5168（均12命令）；
其余均1命令。选择对象JSON：数组未减少命令数，还需存在位掩码区分缺失/null，验证扩展更复杂。
这是确定性字节实验，不是时延或实机结论；真实分片器可用更短头部。
旧入口默认继续使用v1；显式v2桥需要可靠分发，不静默丢弃必需元数据降级。

## 独立接入点

Python：profiles.mcbews_v2.codec 的 encode/decode；transport 的 fragment、
downstream_payloads、send_downstream（使用现有连接scheduler）和ReassemblyPool。
TS：bridge/v2/codec、transport 的对应函数、sendUpstream与ReassemblyPool。
发送循环每次等待一片、失败/取消停止余片，不建立无界任务链。
创建 `V2Bridge` 并作为 `v2` 传给可靠模式的 `McbeServerFacade`。每个连接在后台
探测一次；`await bridge.ready(state.id)` 返回选定版本、功能和 v2 endpoint。通过
`bridge.request(state.id, ...)` 发起协商后的调用，Python 处理器注册到
`bridge.registry`。附加包路由提供 `getV2Endpoint(epoch)` 和现有能力注册表。

同一方法的顺序组正在处理时，新调用会被拒绝；业务容量耗尽时也会拒绝。不同组允许
反向调用，不等待祖先已占用的组。通知不产生错误回复。处理器收到独立的传输归属与
元数据；元数据身份不可信。调用方不能设置保留的 `sdk.*` 键，可通过专用参数传入
trace ID。回复保留元数据。断连会失败所有等待请求并释放该连接的分片；空闲未完成
消息按 TTL 过期。
Python v2 请求的 `V2Bridge(timeout_seconds=...)` 为调度器准入、分片发送和等待回复
设置一个总期限；探测使用单独期限。超时、关闭和取消会停止未发送的分片；已经交给
传输层的帧仍可能完成，无法撤回。

## 探测与回退

v2 清单定义 `mcbews:negotiate_v2` 控制事件。Python 发送 `MCBEWS2H|attempt`，
附加包返回新路由 epoch 和功能；Python 确认挑战后，附加包准入并回复 `ready`。
读取下一帧前，facade 原子安装匹配的 endpoint 和功能集合。完整控制帧和限制见清单。

单次探测期限覆盖发送和等待回复。没有响应时选择 v1；版本不支持、容量繁忙或确认
未完成时明确报错。普通聊天和原生命令不受探测阻塞。v1 回退只允许清单中的已知方法，
必需元数据和功能不会静默丢弃；流式调用必须协商 `stream/1`。

世界共享池最多同时容纳 32 个活动连接和 32 个临时挑战。各连接的 endpoint、分片和
等待请求独立；新连接不会替换其他连接。60 秒租约每 20 秒续期；facade 断连时有界地
尝试发送关闭消息，失联连接等待租约过期。旧帧和确认不能重新激活旧 epoch。已开始的
处理器可能在关闭后结束，但未发送的回复会被抑制。请求和世界修改不会自动重放。

载体来源类型和路由令牌不能证明 WebSocket 身份，也不提供授权；宿主须独立完成认证
和授权。关闭消息丢失时依赖租约清理；已经交给传输层的命令不能撤回。

## 持续请求流

Python 用 `bridge.streams.register(name, handler)` 注册异步生成器，接收
`(name, params, CapabilityContext)`，逐项产生 JSON。调用
`await bridge.stream(connection_id, name, params)` 或 `await endpoint.stream(name, params)`
得到异步迭代器。可能提前退出时用 `async with stream:` 包住消费过程，或调用
`await stream.aclose()`；取消正在等待下一项的任务也会取消该流。流注册与原子能力注册独立。

附加包通过路由模块的 `registerV2Stream(name, handler)` 或 endpoint 的 `registerStream`
注册返回 `AsyncIterable` 的生产者。`await endpoint.stream(name, params, options)` 返回
异步迭代器；提前退出 `for await` 会调用 `return()` 并取消。也可用原有
`V2RequestCancellation` 取消单条流。注销只影响后续调用。

| 消息种类 | v/kind 之外的必需字段 | 方向 |
| --- | --- | --- |
| stream_open | id、method、params | 调用方到生产者 |
| stream_item | id、seq、item | 生产者到调用方 |
| stream_end | id、seq | 生产者到调用方 |
| stream_error | id、seq、error | 生产者到调用方 |
| stream_cancel | id | 调用方到生产者 |

均允许可选 meta，其他字段拒绝。id 属于调用方，所有流消息回显它；每项逻辑消息另用
发送方的新传输编号，可通过 R11 拆成多片。seq 与片段序号分离，第一项从 0 起，
end/error 携带下一项预期序号；空流结束序号为 0，不需要事先提供总长度。乱序、重复逻辑项
或结束时缺项以 INVALID_MESSAGE 失败并取消生产者。未知、迟到消息不创建流；墓碑窗口内
重复 open 不重启生产者。墓碑表本身已满时，新 open 明确拒绝而不再存储编号；
此时重复抑制与 R11 一样受墓碑容量边界限制。

正常结束保留已缓冲项供消费；失败、取消、超载会清空缓冲，终止结果只确定一次。
生产异常返回 HANDLER_FAILED，空闲超时返回 TIMEOUT。取消停止后续发送并关闭生产迭代器，
不撤销已经执行的世界修改。Python 生产者须允许任务取消；JavaScript 生产者应响应
`context.cancellation`，任意永不完成的 Promise 无法被强制解除。

清单上限：每 endpoint 两个方向合计最多 16 条流，含已结束但仍有未读项的流；每消费者
最多 32 项／131072 字节紧凑 item JSON，连接总缓冲 262144 字节。生产者逐项等待发送。
open、生产下一项、发送单条逻辑消息各有 30000ms 空闲期限；接收一项刷新消费者计时，
不是整条流的总时长。生产者及重复 open 墓碑合计最多 2048，墓碑保留 30000ms。
Python `V2Bridge(stream_limits=...)` 与 TS `V2EndpointOptions.streamLimits` 只能降低上限。

慢消费者超限会以 LIMIT_EXCEEDED 明确结束并发送取消；固定间隔和传输节奏不属于接收方背压。
控制发送也有容量上限，控制通道或传输耗尽时由对端空闲超时兜住生命周期。尚未完成的传输
分片继续受 R11 TTL 与配额限制，流取消无需解码不完整分片；断连清理整个连接池。
旧端不支持流式时明确报能力不支持，不缓存完整结果伪装流式。接收额度的测量与决定见
[第四阶段任务记录](https://github.com/Rice-awa/mcbe-ws-sdk/blob/main/docs/plans/ws-sdk-refactor/records/R16.zh.md)。
