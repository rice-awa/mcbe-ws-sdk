# 快速开始

## 安装

```bash
pip install mcbe-ws-sdk
```

开发时的可编辑安装：

```bash
pip install -e ".[dev,docs]"
```

## 最小宿主

`McbeServerFacade` 是宿主入口。以默认协作者构造实例，再按需覆盖，然后运行：

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
    print(
        f"listening on ws://{facade.settings.websocket.host}"
        f":{facade.settings.websocket.port}"
    )
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

## 默认值

构造器是 **keyword-only** 的。每个参数在 `None` 时会折叠回网关默认值，因此
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

## 宿主通常注入的接口

| 表面 | 作用 |
|------|------|
| `ConnectionHook` | 六个生命周期钩子（`on_connected`、`on_disconnected`、`on_player_message`、`on_ui_chat_reassembled`、`on_command_response`、`on_error`） |
| `ResponseSink` | 将 `OutboundText` / `SystemNotification` 路由为 Minecraft 命令 |
| `AddonBridgeService` | ScriptEvent 能力请求/响应（无全局单例） |
| `CommandRegistry` | 前缀/别名命令解析（默认为空） |
| `MCBEWS_V1` | 内置 mcbews v1 协议 profile |

## 本地构建文档

```bash
pip install -e ".[docs]"
mkdocs serve          # 英文 http://127.0.0.1:8000
                      # 中文 http://127.0.0.1:8000/zh/
mkdocs build --strict # 输出到 ./site
```
