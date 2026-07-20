# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)

面向 Minecraft Bedrock Edition 的通用 WebSocket 网关 SDK。本包拥有 WS 传输、数据包
协议与字节安全的命令分片，并通过依赖注入暴露一个双层接口供宿主驱动：既可以订阅
按 `WsEventType` 分键的 `EventBus`，也可以实现 `ConnectionHook` 与 `ResponseSink` 并通过
`McbeServerFacade` 运行全部流程。本包**不拥有**消息 broker 或 LLM worker —— 这些关切
完全属于宿主。

**单向能力模型：** SDK 从 Python 宿主向 Minecraft addon 发送桥接请求并接收响应。
SDK 不包含入站能力注册表分发 —— addon 端拥有所有能力处理逻辑。`LegacyMcbeAiV1Profile`
是唯一内置的协议 profile。

## 安装

针对主仓库的 venv 做可编辑安装（`.venv` 位于主仓库根目录，不在此包内）：

```bash
pip install -e ./mcbe-ws-sdk
```

## 快速开始

`McbeServerFacade` 是宿主入口。以默认协作者构造一个实例，再逐个覆盖，然后运行它：

```python
import asyncio
from mcbe_ws_sdk import McbeServerFacade, ConnectionHook


class MyHook(ConnectionHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event):
        print("chat:", event.message)

    async def on_disconnected(self, state):
        print("disconnected:", state.id)


async def main() -> None:
    facade = McbeServerFacade(hook=MyHook())
    print(f"listening on ws://{facade.settings.websocket.host}:{facade.settings.websocket.port}")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

构造器是 keyword-only 的；每个参数在 `None` 时会折叠回网关默认值，因此
`McbeServerFacade()` 即可启动一个带中性 sink、空命令注册表与默认安全 addon 桥的
可运行 facade：

```python
facade = McbeServerFacade(
    settings=None,    # -> GatewaySettings()
    hook=None,        # -> NoOpHook()
    sink=None,        # -> DefaultResponseSink()
    addon=None,       # -> AddonBridgeService(settings.addon)
    registry=None,    # -> CommandRegistry()
)
```

通过 `await facade.stop()` 可从另一个任务停止运行中的 facade（`run_lifetime`
会干净地展开为优雅关闭；直接取消该任务同样有效）。

## 公共 API 面

宿主需要实现 / 注入以下类：

- `ConnectionHook`（+ 6 个钩子）：`on_connected`、`on_disconnected`、
  `on_player_message`、`on_ui_chat_reassembled`、`on_command_response`、
  `on_error`。
- `ResponseSink` / `DefaultResponseSink`：出站文本负载与系统通知的投递方式。
- `AddonBridgeService` + `AddonBridgeClient`：承载结构化能力请求/响应的 ScriptEvent
  桥（无全局单例）。
- `LegacyMcbeAiV1Profile`（模块级 `LEGACY_MCBEAI_V1`）：唯一内置的协议 profile，
  用于与旧版 mcbeai v1 addon 的互操作。
- `CommandRegistry`：协议处理器所渲染的 Minecraft 命令面（默认为空）。
- `ConnectionManager`：持有每连接状态与玩家会话映射。
- `MinecraftProtocolHandler`：解析入站流量并构建出站数据包。
- `EventBus` / `WsEventType`：底层类型化事件订阅。

`addon/service.py` 与 addon 桥不携带全局单例，完全通过 `AddonBridgeSettings`
进行配置。

## 开发

```bash
pip install -e ".[dev]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
```

## License

MIT
