# ws-sdk 重构：社区方案调研

调研日期：2026 年 10 月 31 日。本文是设计参考，不修改现有协议，也不代表已经决定升级为新协议。

## 先说结论

**最适合这个项目的方向，是保留 Minecraft 必须使用的外层通信格式，简化我们自己控制的 附加包协议。** 不建议为了“通用”而在游戏命令和聊天通道上完整实现另一个大型通信协议。

建议优先借鉴三件事：

1. **JSON-RPC 与 VSCode JSON RPC：** 用一套小而对称的接口处理请求、回应和通知；把等待结果、超时、取消和断连清理收在连接内部。
2. **RSocket：** 清楚区分“大消息拆成小包”和“结果持续产生”；如果需要真正的流式处理，再增加结束、取消、接收额度等规则。
3. **CloudEvents：** 把业务内容与可扩展的元数据分开；请求编号负责配对，追踪信息负责串起整个处理过程。

同类 Minecraft 项目可以借鉴使用体验，但没有找到一个经本次核查就能确认同时覆盖本项目所有要求的现成替代品。这不等于社区不存在，只表示本次检索和源码阅读没有证明存在。

## 为什么不能只看普通 WebSocket 框架

本项目的下行内容要放进 Minecraft 命令，上行内容要经过附加包和聊天事件。现有协议清单明确记录：下行 `commandLine` 默认预算是经验值 461 字节，上行内容限制使用的是 256 个码点，两者不是同一单位；清单还包含分片数量、重组总字节数和过期时间限制。[本项目协议清单](../../src/mcbe_ws_sdk/profiles/mcbews_v1/manifest.json)

因此，选型时应分别检查四件事：

- 一次业务操作怎样表达。
- 表达后的消息怎样编码、拆分、重组。
- 产生数据的速度超过消费速度时怎样减速。
- 底层游戏通道失败、断开或重连时怎样收尾。

这四件事可以属于不同模块。把它们合成一个“流控中间件”，会让扩展和测试重新变复杂。这是针对本项目的设计判断。

## 最值得看的六组参考

| 参考 | 最值得借鉴 | 不宜整套照搬的原因 | 本项目建议 |
| --- | --- | --- | --- |
| mcwss、bedrockpy | 执行游戏命令、事件订阅、请求结果配对的易用接口 | 同类项目也有维护和状态隔离方面的限制 | 看接口组织，逐项核实实现 |
| JSON-RPC 2.0、VSCode JSON RPC | 请求、回应、通知的基本规则；读写与分发分离 | JSON-RPC 本身没有完整流式、分片和背压规则 | 作为交互语义的第一参考 |
| RSocket | 对称交互、流的结束与取消、接收额度、分片与流分离 | 完整协议还包含建连、恢复、租约和二进制帧等机制 | 借鉴状态规则，按需实现 |
| CloudEvents | 标准字段与自定义元数据分离；追踪上下文 | 它描述事件，不替代请求管理和流控 | 借鉴扩展约定，不要求每包都完整封装 |
| WAMP | 扩展能力声明、分阶段返回、取消、动态过程注册 | 引入路由器、调用代理等角色，会扩大本项目职责 | 作为以后功能增长时的参考 |
| jsonrpc-bidirectional | 一条连接上双向发起调用；插件扩展 | 检查到的默认分支提交较旧，不能据此认定适合直接依赖 | 补充阅读，不作为首选依赖 |

以下各节给出对应的一手证据和适用范围。

### 1. 同类项目：接口简单，不等于内部已经解决所有问题

`Sandertv/mcwss` 用每个连接对应的 `Player` 承载事件处理器、命令回调表和发送队列，并通过独立发送循环排出数据。`World` 则把设置方块、广播等操作变成易用方法。这种“调用入口简单、底层细节藏起来”的组织方式值得参考。[连接源码](https://github.com/Sandertv/mcwss/blob/0244b379697bf0021212c2587d6234eba26aef48/player.go)、[世界操作源码](https://github.com/Sandertv/mcwss/blob/0244b379697bf0021212c2587d6234eba26aef48/world.go)

但它把连接命名为玩家，不应成为本项目“一个连接等于一个玩家”的依据。你这里仍然需要区分连接身份与消息发送者身份。默认分支本次查到的提交时间为 2020 年 7 月 31 日；仓库较晚的推送时间不能代替实际代码提交时间。[提交记录](https://github.com/Sandertv/mcwss/commit/0244b379697bf0021212c2587d6234eba26aef48)

`bedrock-ws/bedrockpy` 的事件装饰器、`run()` 和 `send()` 很容易理解。源码通过请求编号和等待对象配对结果，并用信号量限制正在处理的命令。但 `Server` 使用单个 `self._ws` 保存连接，相关等待状态也放在服务器对象上；不能把它直接当成本项目一对多隔离的模板。本次查询时仓库已经归档。[服务器源码](https://github.com/bedrock-ws/bedrockpy/blob/88ca76aa436e3e2110b1a9ca715c34e7a9a11e15/src/bedrock/server.py)、[仓库状态](https://github.com/bedrock-ws/bedrockpy)

**适用结论：** 简洁的公共接口可以学，连接管理、错误收尾和多人隔离必须按自己的约束设计。

### 2. JSON-RPC 与 VSCode JSON RPC：最接近“简洁而可扩展”的基础

JSON-RPC 2.0 不绑定 HTTP；它定义请求、回应、错误和通知。请求带方法名和参数，需要结果时带编号；回应沿用编号完成配对；没有编号的通知不要求回应。这些概念可以放在双向连接上使用。[JSON-RPC 2.0 规范](https://www.jsonrpc.org/specification)

VSCode JSON RPC 的源码展示了更具体的组织方法：读消息和写消息通过接口传入；连接提供发送请求、注册请求处理器、发送通知和注册通知处理器；内部维护等待回应的表；释放连接时拒绝尚未完成的等待。取消通过独立通知表达。这让业务处理器不需要自己管理回调字典和断连清理。[独立使用说明](https://github.com/microsoft/vscode-languageserver-node/blob/a0981e3d9c68397553696b0fb2bebb9dd024e4ba/jsonrpc/README.md)、[连接实现](https://github.com/microsoft/vscode-languageserver-node/blob/a0981e3d9c68397553696b0fb2bebb9dd024e4ba/jsonrpc/src/common/connection.ts)

对本项目，可借鉴的最小接口是“调用一个方法、发送一条通知、注册一个处理器”。例如，第三方注册 `inventory.inspect`，只需要提供处理函数和数据校验规则，不必让 SDK 核心增加一个分支。这里的方法名仅是设计示例，并非现有接口。

需要注意：JSON-RPC 2.0 本身没有规定流式结果、取消、接收额度或分片。若选择严格兼容，就要明确扩展约定；若只借鉴概念而重新设计消息格式，则应称为自定义协议，避免向开发者承诺不存在的兼容性。[规范覆盖范围](https://www.jsonrpc.org/specification)

### 3. RSocket：把“分片”和“流式”彻底分开

RSocket 明确区分普通请求回应、单向发送、请求流式回应和双向数据流。双方都可以发起请求，不把建立连接的一方永久限定为业务调用方。协议还把业务数据与元数据分开。[协议正文](https://github.com/rsocket/rsocket/blob/0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8/Protocol.md)

最重要的借鉴点，是以下两种情况不能混在一起：

- **分片：** 已经有一条完整消息，只是太大，需要拆成多个小包，接收端凑齐后再交给业务。
- **流式：** 结果还没产生完；每产生一项就可以交给业务，不需要提前知道总项数。

RSocket 规定，一项结果即使拆成多个片段，也只消耗一项结果的接收额度。接收方通过 `REQUEST_N` 告知还能接收多少项；额度耗尽后生产方等待；`CANCEL` 用于停止；结束状态表示以后不再产生数据。[分片规则](https://github.com/rsocket/rsocket/blob/0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8/Protocol.md#fragmentation-and-reassembly)、[流控规则](https://github.com/rsocket/rsocket/blob/0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8/Protocol.md#flow-control)

对本项目，第一步可以先做好有界队列、并发上限、超时和取消。只有确认附加包与服务端确实需要持续流式交换，再加入双方都理解的接收额度。单纯增加每包之间的等待时间，是发送限速，不能证明接收方有足够处理能力。最后一句是根据这些规则得出的设计判断。

不建议直接把完整 RSocket 搬进聊天通道：规范假设有可靠、有序的底层传输，并提供二进制帧、建连、恢复等机制；本项目经过游戏命令和聊天事件这一层，是否满足相同保证尚未验证。此外，其路由元数据扩展本次查到仍标记为孵化中，应借鉴设计而不是把它当作必须采用的稳定格式。[底层要求](https://github.com/rsocket/rsocket/blob/0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8/Protocol.md#transport-protocol)、[路由扩展](https://github.com/rsocket/rsocket/blob/0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8/Extensions/Routing.md)

### 4. CloudEvents：自定义字段应有固定的放置位置和规则

CloudEvents 区分事件内容和上下文属性，并明确允许扩展属性，类似自定义请求头；扩展需定义名称、类型和含义。它还把事件身份定义为来源与编号的组合。[事件规范](https://github.com/cloudevents/spec/blob/102551014f04c3ff2171eca5f6f918975fb500f7/cloudevents/spec.md)

这对“开发者不改 SDK 也能添加字段”的启发是：**固定少量协议字段，提供明确的元数据容器，业务字段放在业务内容里。** 还需规定未知字段是否保留、保留多久、大小上限、保留字段不可覆盖，以及是否影响授权。不能仅仅允许任意顶层键，就认为已经有稳定扩展机制。这个元数据容器是本项目建议，不是照抄 CloudEvents 的标准格式。

CloudEvents 的追踪扩展使用 W3C Trace Context，区分追踪标识与跨度标识。因此，不应把请求编号直接当作追踪编号：一次玩家操作可能产生多个请求，每个请求要分别配对，却属于同一条处理链路。[追踪扩展](https://github.com/cloudevents/spec/blob/102551014f04c3ff2171eca5f6f918975fb500f7/cloudevents/extensions/distributed-tracing.md)

受小包限制，建议允许追踪字段缺省，并控制元数据总大小；如果按流的首次消息传递元数据，再在后续消息中引用流编号，就必须同时规定上下文丢失、断连和超时处理。这是空间与状态复杂度之间的取舍，不能只计算节省了多少字节。

### 5. WAMP：功能很全，也提示了复杂度会增长到哪里

WAMP 把过程注册、调用、回应和取消作为明确的协议行为，注册时有可扩展的选项字典；它还支持分阶段返回结果，并要求相关角色声明能力。分阶段返回必须有最终结果或错误；调用者离开后，需要中止对应工作。[过程调用](https://github.com/wamp-proto/wamp-proto/blob/76fc32ca6d7f5f397927ff9d0fd0999df1fae9d6/rfc/text/basic/remote_procedure_call.md)、[分阶段返回](https://github.com/wamp-proto/wamp-proto/blob/76fc32ca6d7f5f397927ff9d0fd0999df1fae9d6/rfc/text/advanced/rpc_progressive_call_results.md)、[取消规则](https://github.com/wamp-proto/wamp-proto/blob/76fc32ca6d7f5f397927ff9d0fd0999df1fae9d6/rfc/text/advanced/rpc_call_canceling.md)

值得学的是“扩展能力先声明，双方确认后再使用”和“流一定要有收尾规则”。不建议现阶段照搬它的路由器与调用代理角色：你的需求目前是服务端与游戏端之间的通信，加入完整消息路由基础设施会扩大 SDK 职责。此处引用默认分支的规范源码，不代表所有实现或某个已发布版本都支持这些能力。

### 6. jsonrpc-bidirectional：验证双向调用与插件并不矛盾

该项目明确支持同一条 WebSocket 连接两端都导出方法，异步请求分别等待正确的回应，并允许用插件处理验证、授权、日志和路由等事项。这可以作为“连接方向与业务调用方向分离”的补充案例。[项目说明](https://github.com/bigstepinc/jsonrpc-bidirectional/blob/ef09346ef409482a4219b7f9478fc7177b4ee765/README.MD)、[插件接口](https://github.com/bigstepinc/jsonrpc-bidirectional/blob/ef09346ef409482a4219b7f9478fc7177b4ee765/src/ServerPluginBase.js)

不过，本次查到默认分支提交时间为 2020 年 9 月 24 日，不能仅凭功能介绍认定它适合作为新的生产依赖。相比直接引入它，优先参考 VSCode JSON RPC 的连接生命周期更合适；这是结合本项目语言和职责范围作出的建议。[提交记录](https://github.com/bigstepinc/jsonrpc-bidirectional/commit/ef09346ef409482a4219b7f9478fc7177b4ee765)

## 给本项目的设计建议

以下是调研后的建议，不是外部协议的统一规定：

1. **外层适配与内层消息分开。** Minecraft 原生的命令请求、命令回应和事件订阅继续由适配层负责；自定义附加包消息才采用新的统一表达。
2. **先统一语义，再压缩字段。** 先规定请求、回应、通知、流数据、完成、错误和取消各表示什么；线上字段长度由编码层决定。不要让开发者面对满屏单字母字段。
3. **状态归连接所有。** 待回应请求、重组缓存、发送队列、取消任务和订阅释放句柄都应有清楚的连接归属；玩家身份放在消息上下文，玩家业务历史由宿主管理。
4. **扩展通过注册完成。** 新方法、新事件和新元数据不应要求修改核心分发器；核心只处理协议共性，具体数据校验交给注册项。
5. **事件观察与处理拦截各有职责。** 事件订阅适合通知观察者；真正能阻止、替换或修改处理过程的钩子，应明确顺序、返回值、异常规则，避免同一消息被多套机制重复处理。
6. **逐层收窄流控。** 分片器负责字节预算；重组器负责缓存和过期；发送调度器负责队列与限速；流会话负责接收额度和取消。内部可以分工，公共接口不必暴露每一层。
7. **先定义失败，再宣传完整能力。** 超时、断连、取消与完成同时发生时只能有一个最终结果；已执行的游戏命令不会因为取消而自动撤销；重连后也不应默认重试有副作用的操作。
8. **日志能逐级打开。** 默认记录方向、连接、请求编号、类型、字节数和结果；完整原始包作为可选调试能力；识别出的桥接回声在相应类别过滤，不交给业务处理器反复清理。

## 可复现的检索记录

本次实际使用了以下 GitHub CLI 检索，未仅凭项目名称或星标数量作判断：

```text
gh search repos 'minecraft bedrock websocket' --limit 20 --json fullName,description,stargazersCount,pushedAt,isArchived,url
gh search repos 'mcwss' --limit 8 --json fullName,description,stargazersCount,pushedAt,isArchived,url
gh search repos 'minecraft websocket language:Python' --limit 12 --json fullName,description,pushedAt,isArchived,url
gh search repos 'mcbe-ws' --limit 12 --json fullName,description,pushedAt,isArchived,url
gh search repos 'bidirectional json rpc websocket' --limit 12 --json fullName,description,stargazersCount,pushedAt,isArchived,url
gh search repos 'jsonrpc bidirectional' --limit 12 --json fullName,description,pushedAt,isArchived,url
gh search repos 'rsocket protocol' --limit 8 --json fullName,description,stargazersCount,pushedAt,isArchived,url
gh search repos 'vscode-languageserver-node' --owner microsoft --limit 5 --json fullName,description,pushedAt,isArchived,url
gh search repos 'CloudEvents' --owner cloudevents --limit 5 --json fullName,description,pushedAt,isArchived,url
gh search repos 'wamp-proto' --owner wamp-proto --limit 5 --json fullName,description,pushedAt,isArchived,url
```

其中 `bidirectional json rpc websocket` 没有命中，改用 `jsonrpc bidirectional` 后找到了相关项目。随后使用 `gh api` 获取默认分支、提交编号和目录树，再按固定提交读取源码；没有把搜索摘要直接作为实现证据。

| 来源 | 本次核查的默认分支提交 | 提交时间（UTC） |
| --- | --- | --- |
| Sandertv/mcwss | `0244b379697bf0021212c2587d6234eba26aef48` | 2020-07-31 |
| bedrock-ws/bedrockpy | `88ca76aa436e3e2110b1a9ca715c34e7a9a11e15` | 2026-07-06 |
| microsoft/vscode-languageserver-node | `a0981e3d9c68397553696b0fb2bebb9dd024e4ba` | 2026-10-30 |
| rsocket/rsocket | `0f6e5554a5f9abbb1c6c7ec2138d2f3e0ab280e8` | 2023-05-02 |
| cloudevents/spec | `102551014f04c3ff2171eca5f6f918975fb500f7` | 2026-10-22 |
| wamp-proto/wamp-proto | `76fc32ca6d7f5f397927ff9d0fd0999df1fae9d6` | 2026-10-10 |
| bigstepinc/jsonrpc-bidirectional | `ef09346ef409482a4219b7f9478fc7177b4ee765` | 2020-09-24 |

GitHub CLI 用法和 RSocket 的流式调用规则还通过 Context7 查询了官方资料。Context7 对 `vscode-jsonrpc` 没有返回准确匹配，因此没有使用不相关结果，改为直接检查微软仓库的源码。内置网页检索未返回可用内容；规范和仓库内容由官方站点与 GitHub API 补足。

本次没有运行这些第三方项目，也没有在真实 Minecraft 世界进行吞吐、丢包、取消和顺序测试；“适合借鉴”是代码与规范层面的判断，不是性能或兼容性认证。后续设计需在现有 461 字节默认预算下，测量完整命令编码后的大小、每次操作的包数、重组内存上限、慢处理器下的队列长度，以及断连后是否仍有未结束任务。
