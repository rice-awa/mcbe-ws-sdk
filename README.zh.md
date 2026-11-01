# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

**用 Python，为 Minecraft 基岩版创造更多玩法。**

`mcbe-ws-sdk` 是面向 Minecraft 基岩版（Bedrock）的通用 WebSocket 网关 SDK。
从响应一条聊天指令、查询玩家背包，到让附加包调用 Python 服务、持续返回流式数据，
你都可以通过熟悉的异步代码把游戏世界与自己的应用连接起来。

项目提供 Python SDK 和配套的 TypeScript 附加包，管理连接、协议协商、请求与回复配对、
消息分片以及断连后的任务清理。你可以用它编写聊天机器人、游戏工具和自定义交互，
也可以接入自己的模型服务、消息系统或管理后台；认证、授权和业务状态由应用自行掌握。

**当前使用 V2 协议（MCBEWS/2）**，支持双向能力调用、通知、元数据和请求流。
SDK、附加包和协议的版本分别管理，具体说明见[迁移指南](docs/migration.zh.md)。

[浏览文档](https://rice-awa.github.io/mcbe-ws-sdk/zh/) · [快速开始](docs/getting-started.zh.md) · [查看示例](examples/addon-server/README.zh.md)

```text
Minecraft 世界（配套附加包） ←── /wsserver ──→ Python 应用（McbeServer）
                                                   ├── ConnectionHook：处理输入
                                                   ├── ResponseSink：显示回复
                                                   └── server.v2：能力、通知与流
```

## 可以用它做什么

- **用几行异步代码响应游戏操作。** 在 `ConnectionHook` 中处理玩家消息，
  执行原生命令或等待附加包能力的结果；SDK 会继续读取回包，让等待回复的处理器正常完成。
- **让 Python 与附加包双向协作。** 双端都可以注册自己的能力，发起请求、发送通知，
  并通过元数据传递调用上下文。扩展游戏侧功能时，沿用同一套请求机制即可。
- **逐项接收持续产生的数据。** 请求流支持正常结束和主动取消，大项会先分片再重组，
  消费者读取到的是完整的逻辑项。
- **按玩家组织业务。** SDK 透传消息发送者，同一玩家的处理保持顺序，不同玩家可以独立推进。
  应用按连接和发送者保存各自的上下文。
- **自定义游戏内交互。** `ResponseSink` 与 `McbeOutboundDelivery` 负责把应用结果显示到游戏中，
  原生事件订阅和拦截器则为更多输入方式提供扩展入口。
- **从可运行的例子开始。** 默认附加包已经包含玩家、背包快照、回声、流和世界命令能力，
  可以先运行示例，再逐步换成自己的业务处理器。

## 运行第一个示例

需要 Python 3.11+、Node.js 22，以及可使用 `/wsserver` 的基岩版世界。
两个示例都需要配套附加包完成协议协商。

在仓库根目录安装 SDK 并构建附加包：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
npm ci --prefix addon
npm --prefix addon run mcaddon:production
```

将 `addon/dist/packages/mc-ws-sdk.mcaddon` 导入 Minecraft，在目标世界开启
**实验 → 测试版 API（Beta APIs）**，并启用行为包。未开启时脚本不会加载，协议协商会超时。
当前附加包的引擎目标为 1.26.30，依赖 server 2.8.0 / server-ui 2.1.0；
完整依赖见[附加包说明](addon/README.zh.md)。

启动服务：

```bash
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
```

在游戏中连接并查询背包：

```text
/wsserver <Python宿主IP>:8080
!inv
```

同机可用 `127.0.0.1`，跨设备使用宿主的局域网 IP。
`!help` 查看命令，`!echo hello` 测试回声，`!stream 5` 接收五项后结束。
示例只响应以 ASCII `!` 开头的消息，普通聊天保持静默。按 `Ctrl+C` 停止服务。

## 集成到自己的应用

把示例接入自己的应用时，可以从一个钩子开始。下面的宿主会监听 `!inv`，
按发言玩家查询背包，再把结果打印到终端。请求的编号、发送、超时和回复配对都由 SDK 处理：

```python
import asyncio

from mcbe_ws_sdk import McbeServer, NoOpHook


class InventoryHook(NoOpHook):
    async def on_player_message(self, state, event, parsed=None):
        if event.message.strip() != "!inv":
            return
        response = await server.v2.request(
            state.id, "get_inventory_snapshot", {"target": event.sender}
        )
        print(response["result"])


server = McbeServer(hook=InventoryHook())
asyncio.run(server.run_lifetime())
```

如果希望把查询结果直接显示在游戏里，可以继续接入 `ResponseSink` 和
`McbeOutboundDelivery`；[可运行示例](examples/addon-server/server.py)包含完整写法。
多人场景下，按 `(connection_id, event.sender)` 保存玩家状态，就能把不同玩家的业务上下文分开。

## 按需要继续

| 目标 | 文档 |
| --- | --- |
| 安装、连接、命令与排查 | [快速开始](docs/getting-started.zh.md) |
| 添加能力、通知、元数据和流 | [扩展作者指南](docs/extension-authors.zh.md) |
| 理解队列、生命周期和职责 | [架构](docs/architecture.zh.md) |
| 订阅或拦截游戏事件 | [原生事件](docs/native-events.zh.md) |
| 跟踪请求、诊断错误 | [追踪与诊断](docs/diagnostics.zh.md) |
| 查看消息字段和传输限制 | [V2 协议](docs/protocol-v2.zh.md) |
| 从 0.2.x 升级或整套回退 | [迁移指南](docs/migration.zh.md) |
| 查找类、方法和参数 | [API 参考](docs/reference.zh.md) |

默认附加包包含快照、回声、流和世界命令能力。只需快照时，使用
`npm --prefix addon run mcaddon:base` 构建精简包。
世界命令应用[命令禁止列表](addon/README.zh.md#世界命令安全)；宿主负责调用授权。
原生命令保持原子，超出字节预算会明确失败；tellraw 和脚本事件可按默认 461 字节预算分片。

## 开发与验证

```bash
ruff check --no-cache src tests examples tools
mypy --no-incremental src
MCBE_REQUIRE_CROSS_LANGUAGE=1 pytest -p no:cacheprovider -q
mkdocs build --strict
python tools/format.py --check
```

跨语言测试使用 Node.js 22 与 addon 的 TypeScript 依赖；先执行上面的 `npm ci --prefix addon`。
缺少工具时，普通 pytest 会跳过依赖运行器的用例；`MCBE_REQUIRE_CROSS_LANGUAGE=1` 将缺工具视为失败。
