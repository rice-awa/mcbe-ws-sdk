# Addon 冒烟测试审查与整改依据

日期：2026-11-01。审查基线：`d1d82d9` 与用户已有的行为包 manifest 改动。
范围：`logs/20261101.log`、Python 服务与示例、TypeScript addon、构建包及相关测试。
本次只修改文档，整改尚未实施。状态统一见[任务清单](../plans/ws-sdk-refactor/tasks.zh.md)，实施与验收合同见[V2-only 整改方案](../plans/ws-sdk-refactor/v2-only-remediation.zh.md)。

## 1. 结论与证据边界

玩家快照、背包快照和原生 WS 命令已有成功记录。世界命令、回声和流的失败，与默认构建未注册对应处理器一致；普通请求路径又隐藏了具体错误信息。
当前程序仍主动支持 MCBEWS/1，不满足用户新增的“彻底移除 V1、服务端明确拒绝旧客户端”要求。
用户另要求对齐 manifest 的 Script API 依赖，并让聊天示例只响应 `!` 前缀。

| 测试项 | 日志位置 | 审查结果 |
| --- | --- | --- |
| `!player` | 66—72 行 | 玩家快照成功 |
| `!inv` | 73—79 行 | 背包快照成功 |
| `!wscmd say …` | 122—127 行 | 原生命令 `statusCode=0` |
| `!cmd say …` | 80—121、128—148 行 | 三次失败，只展示 `capability failed` |
| `!echo aaa` | 149—169 行 | 同样隐藏了可读原因 |
| `!stream 5` | 176—178 行 | `UNSUPPORTED_METHOD`，不等于流引擎损坏 |
| 普通聊天 | 170—175 行 | `stream 5`、`a` 被回复“未知命令” |
| 取消、多人、字节边界 | 无对应完成记录 | 仍须实机验收 |

失败请求的栈进入 `selection.endpoint.request()`，且流收到 V2 方法错误，支持该连接已进入 V2 请求路径；快照成功行本身没有协商版本字段。
本地 `dist/scripts/main.js` 与 `.mcpack`、`.mcaddon` 内行为包的 `scripts/main.js` 内容相同，SHA-256：

```text
cdeee0a1112c030b12c8c7397c0f71647fd6743bc2630ed8e9e5eb2ddc9edfc8
```

在模拟 Minecraft API 的环境中执行这个实际 bundle，复现了两个快照成功、普通能力未注册错误和流方法错误。
这验证了本地产物，不证明游戏实际加载文件摘要相同，也不代替游戏完整版本与内容日志。

## 2. 原报告覆盖情况与修正

原报告覆盖缺注册、错误展示、分片、依赖和日志问题，但缺少连接准入、双端旧路由、旧公开 API、发布合同清理及产物测试差异。

| 原表述 | 修正 |
| --- | --- |
| V2 response 丢弃错误码 | 线上响应和 Python `exc.code` 仍有错误码；TS 覆盖说明，异常字符串和示例展示未显示码 |
| 三个失败命令各属于不同根因类别 | 三者均涉及未注册；错误展示缺陷同时影响普通请求，不能按命令数量划分根因 |
| 被劈开的是 `!stream-cancel` | 实际是 `!stream`：第一帧末尾 `!`，第二帧开头 `stream [count]` |
| 快照 traceback 证明协商 | traceback 来自失败的 `!cmd` / `!echo`，快照成功没有 traceback |
| EOF 是 Minecraft 正常探测 | 只有零字节 EOF 与随后连接成功的证据；来源及是否预期行为仍未知 |
| V2 完全没有观测 | 缺少明确协商结果日志；共用发送器、追踪和请求设施仍可提供观测 |
| `!cmd` 错误码只能修复后确认 | 当前真实 bundle 模拟运行已返回 `UNSUPPORTED_METHOD`；原实机日志仍未直接记录此字段 |
| 时间戳和 token 次数证明最新构建 | 使用产物内容摘要与实际 bundle 运行证据，不依赖易变计数 |
| 2.8.0 与游戏版本无官方对应资料 | 本次经 Context7 检索并核对官方 1.26.30 更新说明，找到 server 2.8.0 / server-ui 2.1.0 的发布对应关系 |

## 3. 已确认的问题

### F01：默认构建没有安装全部演示能力（P2）

默认注册表只有 `get_player_snapshot` 和 `get_inventory_snapshot`。
`run_world_command` 故意需要显式启用；`demo.echo` 和 `demo.ticks` 位于独立示例模块，默认 `main.ts` 不注册它们。
这符合原配置合同，但“构建默认包后测试帮助中的全部命令”的路径不完整。
整改需提供可重复的完整演示包步骤和缺能力提示；不能为了让示例全绿而在通用 SDK 默认开放世界命令。

源码：`addon/scripts/bridge/capabilities/index.ts`、`addon/scripts/main.ts`、`examples/addon-server/capabilities.ts`。

### F02：错误说明与展示丢失（P2）

TS `v2/runtime.ts:211` 映射错误码后把说明写死为 `capability failed`。
Python `errors.py:131` 的 `RemoteCallError` 保存 code，但字符串仅带 request ID 和 message；示例普通异常分支原样展示，旧 `ok: false` 安装提示无法覆盖此路径。
整改需保留有界且可公开的错误说明，显式展示方法名、错误码和行动提示；关联 ID 留给诊断，玩家不直接接收内部 ID 或堆栈。
未知方法和参数错误应可诊断；任意处理器异常不能泄露内部实现或私密数据。

### F03：服务端没有 V2-only 准入（P1，新要求阻塞项）

探测无回复时，`profiles/mcbews_v2/runtime.py:114` 返回 MCBEWS/1 selection。
`gateway/server_facade.py:278` 在协商完成前发布 CONNECTED；明确不支持版本仅让 ready() 失败，不关闭 WS 或阻断业务。
本地分别模拟无回复和回复版本 1，两者连接均存活并执行玩家回调。
这是旧兼容设计与新要求冲突。整改必须强制 V2、就绪前禁止业务、失败关闭；不能在启动读取前等待协商，否则回包无法被读到。

### F04：旧路由、公开接口与发布合同仍存在（P1，新要求阻塞项）

Python 默认创建旧 AddonBridgeService，继续处理 bridge/UI/session/approval 通道。
addon 独立接受 `mcbews:bridge_req`，缺省版本按 1 处理，旧信封数字 `v: 2` 也被接受；旧文本响应仍自动订阅。
真实 bundle 在完成 V2 协商后仍成功处理 V1 快照请求，所以只删 fallback 不够。
根导出、V1 profile/extension、兼容回调、配置、生成器和安装探针仍依赖 V1；通用 461 B 预算也从 V1 清单读取。
整改需双端、API、配置与生产资产一起清理，只保留拒绝识别和负向样本；原生 MCBE WS `header.version=1` 不能误删。

### F05：依赖和版本基线不一致（P2）

| 位置 | @minecraft/server | @minecraft/server-ui |
| --- | --- | --- |
| 当前 manifest 与本地 mcpack | 2.8.0 | 2.1.0 |
| package.json、lockfile、原安装基线 | 2.0.0 | 2.0.0 |

用户批准的目标是 server **2.8.0**、server-ui **2.1.0**，不回退 2.0.0。
这是 Script API 依赖升级，不是把 Python SDK、npm 项目、pack header 或线协议版本都改成 2.8.0。
`addon/tests/manifest.test.ts:30` 已因 2.8.0 与旧断言不一致失败。
整改需检查声明、锁文件、安装类型和包内 manifest 一致，并独立断言批准的版本，避免两份文件同时降级仍通过。

官方 1.26.30 更新说明明确同时提供这两个目标 API。可据此确定目标游戏基线为 1.26.30，并在实施时核对 manifest 最低引擎及 gametest 兼容性；当前 `[1,21,80]` 不能继续作为新版已验证支持声明。
发布对应关系不等于本项目已在该版本完成实机验证。

### F06：帮助在命令 token 中间分片（P2）

`flow/flow_control.py:14` 将 `!`、`.` 等作为句末候选。当前帮助在实测目标名下分成 428 B、272 B 两条命令，拆开了 `!stream`。
整改优先保留完整帮助行和命令 token，同时保留字节上限、超长单行回退及重组不丢字保证，不能仅删除一个分隔符就宣称覆盖全部边界。

### F07：普通聊天触发自动回复（P2，用户明确要求）

示例把任何非空聊天交给分发，还接受裸 `help`、`帮助`，因此普通聊天收到“未知命令”。
最终合同：去掉首尾空白后，仅 ASCII `!` 开头才处理。普通聊天、裸帮助、句中 `!` 和全角 `！` 静默；未知 `!xxx` 可提示，裸 `!` 给一次用法提示。
过滤只放在示例业务入口，不得拦截 SDK 里的协商、MCBEWS2、commandResponse/error 或宿主原生事件。

### F08：生命周期诊断与默认原始日志（P2）

缺少稳定的协商接受/拒绝/超时和关闭原因记录；addon 示例 `--log-raw` 当前 default=True，默认重复打印完整 payload。
整改需默认摘要、显式开启完整包、记录协议/能力/拒绝及关闭原因，并保留脱敏边界。
日志过滤不能影响请求处理；零字节 EOF 未确认来源，不能直接屏蔽整个 websockets logger。

### F09：测试与安装产物覆盖不一致（P2）

`tests/smoke/r17-addon-runner.cjs:19` 主动注册演示处理器，默认打包入口没有。
既有回声/流测试通过不能证明默认包具备全部演示能力。
整改需分别验收基础包和完整演示包；运行实际 bundle 的请求、协商、取消和错误探针，并核对包内摘要。
旧正向兼容测试应替换为拒绝回归，不是直接删除所有相关测试。

## 4. 未确认或未覆盖

- 实机 Minecraft 完整版本、平台、实际加载包摘要；目标 1.26.30 与 gametest 组合仍需加载验证。
- 日志 7—42 行零字节 EOF 的来源；11:05:26 断开的发起方与原因。
- 多人、中文/emoji 的 461 B 边界、流首段/取消、慢端、重连和清理的最终实机结果。

快照成功与方法错误不支持“整个 addon 未加载”作为主要根因；也不能据此扩大平台兼容承诺。

## 5. 整改前验证证据

本次编辑未重跑代码测试；以下为前一轮只读审查结果，不是修复后通过证明。

| 检查 | 结果 |
| --- | --- |
| Python 七个相关测试文件：协商、入口、示例、V2 请求/流、会话与快速关闭 | 60 passed |
| addon 全部 17 个测试文件 | 204 passed、1 failed；失败为 manifest 版本断言 |
| TS 生产与测试配置 --noEmit | 通过，仍使用原类型依赖 |
| 当前实际 bundle，模拟 Minecraft API | 两项快照成功；缺注册错误及 V2 后接受 V1 复现 |
| 服务端准入探针 | 无回复回退 V1；显式不支持版本后业务仍执行 |
| 帮助分片探针 | `!stream` token 断裂复现 |

## 6. 资料与实施入口

2026-11-01 经 Context7 检索并核对 Microsoft 官方原文：

- [Minecraft Bedrock 1.26.30 更新说明](https://github.com/MicrosoftDocs/minecraft-creator/blob/main/creator/Documents/Update1.26.30.md)：明确 server 2.8.0 与 server-ui 2.1.0 的发布对应关系。
- [原生模块依赖说明](https://github.com/MicrosoftDocs/minecraft-creator/blob/main/creator/Reference/Content/ManifestReference/nativemoduledependency.md)：模块依赖版本决定可用 API，与 pack 自身版本不同。

按[整改方案](../plans/ws-sdk-refactor/v2-only-remediation.zh.md) D01—D08 实施，进度在[任务清单](../plans/ws-sdk-refactor/tasks.zh.md)的 R18A 更新。
先整改并移除 V1，再对最终候选补齐 R18，最后 R19 收口；不再把旧协议兼容全部通过作为移除 V1 的前置条件。
