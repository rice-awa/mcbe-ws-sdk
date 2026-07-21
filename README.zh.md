# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

面向 **Minecraft 基岩版（Bedrock）** 的通用 **WebSocket 网关 SDK**。

本包拥有 WS 传输、数据包协议与字节安全的命令分片（461 字节硬上限）。宿主通过
`ConnectionHook` 与 `ResponseSink` 注入行为，并由 `McbeServerFacade` 驱动整条链路。

SDK 内部**不包含**消息 broker 或 LLM worker —— 这些关切完全属于宿主应用。

```text
Minecraft 客户端  ←── /wsserver IP:端口 ──→  你的 Python 宿主（本 SDK）
```

## 安装

```bash
pip install mcbe-ws-sdk
```

开发时的可编辑安装：

```bash
pip install -e ".[dev,docs]"
```

需要 **Python 3.11+**。

## 30 秒体验

```python
import asyncio
from mcbe_ws_sdk import McbeServerFacade, NoOpHook


class MyHook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        print(f"{event.sender}: {event.message}")


async def main() -> None:
    facade = McbeServerFacade(hook=MyHook())
    print(f"ws://{facade.settings.websocket.host}:{facade.settings.websocket.port}")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

然后在游戏里执行：`/wsserver <本机IP>:8080`

若要「收到消息并用 tellraw 回复」，直接跑现成示例：

```bash
python examples/basic-server/server.py
```

## 文档

完整新手教程、架构、协议与 API 参考在文档站（中英双语）：

| | |
|---|---|
| **在线** | https://rice-awa.github.io/mcbe-ws-sdk/zh/ |
| **本地** | `pip install -e ".[docs]" && mkdocs serve` → http://127.0.0.1:8000/zh/ |

| 页面 | 内容 |
|------|------|
| [快速开始](./docs/getting-started.zh.md) | 安装、5 分钟上手、最小回声机器人、FAQ |
| [架构](./docs/architecture.zh.md) | 分层栈与依赖倒置 |
| [协议](./docs/addon-bridge-protocol.zh.md) | mcbews v1 桥接线格式 |
| [API 参考](./docs/reference.zh.md) | 从源码生成（导读见中文站） |

## 示例

| 路径 | 说明 |
|------|------|
| [`examples/basic-server/`](./examples/basic-server/) | 聊天回声 + `tellraw`（推荐先看） |
| [`examples/addon-server/`](./examples/addon-server/) | 通过配套 addon 调用能力 |
| [`examples/addon-capability-call/`](./examples/addon-capability-call/) | 内存中的桥接往返（不连游戏） |

配套 TypeScript addon：[`addon/`](./addon/)。加载该包的世界必须开启
**实验 → 测试版 API**（详见 [addon README](./addon/README.zh.md#%E5%9C%A8%E4%B8%96%E7%95%8C%E4%B8%AD%E5%90%AF%E7%94%A8)）。

## 开发

```bash
pip install -e ".[dev,docs]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
python tools/format.py          # ruff format+fix（Python）；有 Node 时跑 prettier（Addon）
python tools/format.py --check  # CI 风格只检查不写入
```

## License

[MIT](./LICENSE)
