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

SDK 与附加包之间使用 **V2 协议（MCBEWS/2）**通信，支持双向能力调用、通知、元数据和请求流。
SDK、附加包和协议各自独立编号，当前版本见[迁移指南](docs/migration.zh.md#版本号分别表示什么)。

[浏览文档](https://rice-awa.github.io/mcbe-ws-sdk/zh/) · [快速开始](docs/getting-started.zh.md) · [查看示例](examples/addon-server/README.zh.md)

```text
Minecraft 世界（配套附加包） ←── /wsserver ──→ Python 应用（McbeServer）
                                                   ├── ConnectionHook：处理输入
                                                   ├── ResponseSink：显示回复
                                                   └── server.v2：能力、通知与流
```

## 可以用它做什么

- **用异步代码响应游戏操作。** 在 `ConnectionHook` 中处理玩家消息、执行原生命令，
  或等待附加包返回结果。处理器等待期间，SDK 仍会继续读取消息，并把回复对应到各自的请求。
- **让 Python 与附加包双向调用。** 双方都可以注册能力、发起请求和发送通知，
  并用元数据携带调用上下文。扩展游戏侧功能时，沿用同一套请求机制即可。
- **随产生随接收数据。** 请求流可以正常结束，也可以中途取消。较大的数据项会先分片传输再重组，
  你的代码拿到的始终是完整的数据项。
- **让每名玩家的交互互不干扰。** 每条消息都带有发送者。同一玩家的处理按顺序执行，
  不同玩家可以各自推进，因此可以按连接和发送者保存应用上下文。
- **自定义游戏内体验。** 用 `ResponseSink` 和 `McbeOutboundDelivery` 把结果显示到游戏里，
  也可以通过原生事件订阅和拦截器改变宿主接收输入的方式。
- **从可运行的示例起步。** 默认附加包已经提供玩家和背包快照、回声、流和世界命令。
  先运行示例，再逐步换成自己的处理器。

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
**实验 → 测试版 API（Beta APIs）**，并启用行为包。未开启时脚本不会加载，协议协商也会超时。
附加包面向 Minecraft 引擎 1.26.30，完整要求见[附加包说明](addon/README.zh.md#环境要求)。

启动服务：

```bash
python examples/addon-server/server.py --host 0.0.0.0 --port 8080
```

在游戏中连接并查询背包：

```text
/wsserver <Python宿主IP>:8080
!inv
```

Python 与游戏在同一台机器上时使用 `127.0.0.1`，从其他设备连接则使用运行 Python 的机器的局域网 IP。
连接后可以试试：

- `!help` 查看命令列表。
- `!echo hello` 收到一条回声。
- `!stream 5` 依次接收五项数据后结束。

示例只响应以 ASCII `!` 开头的消息，普通聊天不会有任何反应。按 `Ctrl+C` 停止服务。

## 集成到自己的应用

要在自己的应用里使用 SDK，可以从一个钩子开始。下面的宿主监听 `!inv`，
向附加包查询发言玩家的背包，再把结果打印到终端。请求编号、发送、超时，
以及把回复对应到请求，这些都由 SDK 完成：

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

如果想把结果直接显示在游戏里，可以再接入 `ResponseSink` 和
`McbeOutboundDelivery`；[可运行示例](examples/addon-server/server.py)有完整写法。
多人世界里，请按 `(connection_id, event.sender)` 保存每名玩家的状态，让不同玩家的上下文互不干扰。

## 按需查阅

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

## 使用须知

- **选择附加包。** 默认附加包包含快照、回声、流和世界命令。只需要快照时，
  可以用 `npm --prefix addon run mcaddon:base` 构建精简包。
- **世界命令。** 命令会先经过[命令禁止列表](addon/README.zh.md#世界命令安全)过滤，
  但谁有权调用仍由宿主判断。
- **长文本。** 原生命令不会被拆开发送，超过字节预算时直接报错；
  tellraw 和脚本事件则可以按默认的 461 字节预算分片。

## 开发与验证

参与 SDK 本身的开发时，在仓库根目录运行下列检查：

```bash
ruff check --no-cache src tests examples tools
mypy --no-incremental src
MCBE_REQUIRE_CROSS_LANGUAGE=1 pytest -p no:cacheprovider -q
mkdocs build --strict
python tools/format.py --check
```

跨语言测试需要 Node.js 22 和 addon 的 TypeScript 依赖，请先执行前面的 `npm ci --prefix addon`。
缺少这些工具时，普通 pytest 会跳过依赖它们的用例；设置 `MCBE_REQUIRE_CROSS_LANGUAGE=1` 后，缺少工具会直接判为失败。
