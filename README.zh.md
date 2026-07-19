# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-中文-blue?style=flat-square)](./README.zh.md)

面向 Minecraft Bedrock Edition 的通用 WebSocket 网关 SDK。本包拥有 WS 传输、数据包
协议与字节安全的命令分片，并通过依赖注入暴露一个双层接口供宿主驱动：既可以订阅
按 `WsEventType` 分键的 `EventBus`，也可以实现 `ConnectionHook` 与 `ResponseSink` 并通过
`McbeServerFacade` 运行全部流程。本包**不拥有**消息 broker 或 LLM worker —— 这些关切
完全属于宿主。

## 安装

针对主仓库的 venv 做可编辑安装（`.venv` 位于主仓库根目录，不在此包内）：

```bash
pip install -e ./mcbe-ws-sdk
```

## 快速开始

`McbeServerFacade` 是宿主入口。以全部默认值（`SilentResponseSink`）构造一个实例，
再逐个覆盖协作者，然后运行它：

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
`McbeServerFacade()` 即可启动一个带静默 sink、默认命令注册表与能力注册表的可运行 facade：

```python
facade = McbeServerFacade(
    settings=None,        # -> GatewaySettings()
    hook=None,            # -> NoOpHook()
    sink=None,            # -> SilentResponseSink()
    addon=None,           # -> AddonBridgeService(settings.addon)
    registry=None,        # -> CommandRegistry(DEFAULT_COMMANDS)
    capabilities=None,    # -> CapabilityRegistry()
)
```

通过 `await facade.stop()` 可从另一个任务停止运行中的 facade（`run_lifetime`
会干净地展开为优雅关闭；直接取消该任务同样有效）。

## 公共 API 面

宿主需要实现 / 注入以下类：

- `ConnectionHook`（+ 7 个钩子）：`on_connected`、`on_authenticated`、
  `on_disconnected`、`on_player_message`、`on_bridge_message`、
  `on_ui_chat_reassembled`、`on_command_response`。
- `ResponseSink` / `SilentResponseSink` / `DefaultResponseSink`：出站 tellraw /
  scriptevent / AI 响应负载的投递方式。
- `AddonBridgeService` + `AddonBridgeClient`：承载结构化请求/响应的 ScriptEvent
  桥（无全局单例）。
- `CapabilityRegistry` + `CapabilityHandler` + `CapabilityContext`：入站
  `scriptevent mcbeai:bridge_request` 的覆盖点。
- `CommandRegistry`：协议处理器所渲染的 Minecraft 命令面。
- `ConnectionManager`：持有每连接状态与玩家会话映射。
- `MinecraftProtocolHandler`：解析入站流量并构建出站数据包。
- `EventBus` / `WsEventType`：底层类型化事件订阅。

`addon/service.py` 与 addon 桥不携带全局单例，完全通过 `AddonBridgeSettings`
进行配置。

## License

MIT
