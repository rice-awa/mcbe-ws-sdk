# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

面向 **Minecraft 基岩版（Bedrock）** 的通用 **WebSocket 网关 SDK**。

本包负责 WebSocket 传输、请求关联、附加包桥接与字节安全的命令分片。
新宿主使用 `McbeServer`，在受管理钩子中直接等待命令与能力调用。
旧入口 `McbeServerFacade` 保持原串行默认行为。模型服务、消息代理、认证与业务历史由宿主负责。

> **版本范围：** 当前 PyPI 发布版为 `0.2.1`。本文的新入口、v2、元数据与持续流属于尚未发布的当前源码；
> 请使用下面的可编辑安装，不能通过安装 PyPI `0.2.1` 获得这些能力。

```text
Minecraft 客户端  ←── /wsserver IP:端口 ──→  Python 宿主
```

## 附加包与双向能力

新服务可协商 v2，双向注册能力、发送通知、携带元数据，并持续生产与消费流。
旧附加包仅回退到已知 v1 方法；必需元数据与流式不会静默降级。
SDK 管理请求等待与分片，扩展作者只注册处理函数。

附加包加载要求目标世界开启 **实验 → 测试版 API（Beta APIs）**。
桥接不是安全边界，宿主负责认证与授权。新增接口已经自动化验证，实机与完整兼容矩阵仍待验收，
不扩大平台或游戏版本支持承诺。

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

以下代码需要安装当前源码。

```python
import asyncio
from mcbe_ws_sdk import McbeServer, NoOpHook


class MyHook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        print(f"{event.sender}: {event.message}")


async def main() -> None:
    server = McbeServer(hook=MyHook())
    print(f"ws://{server.settings.websocket.host}:{server.settings.websocket.port}")
    await server.run_lifetime()


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
| [迁移指南](./docs/migration.zh.md) | 新旧入口、顺序、返回值、回退与取消 |
| [扩展作者](./docs/extension-authors.zh.md) | 自定义能力、元数据、事件与流式 |
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
python tools/format.py --check  # 持续集成方式，只检查不写入
```

v2 Python/TypeScript 跨语言测试需要 Node.js 22，以及
`npm ci --prefix addon` 安装的依赖。缺少 Node.js 或 TypeScript 时，
仅调用 TypeScript 测试程序 的测试跳过，纯 Python 测试继续运行。
本地强制验收命令（必需的 `cross-language` 持续集成任务 使用同一模式）：

```bash
MCBE_REQUIRE_CROSS_LANGUAGE=1 python -m pytest -p no:cacheprovider -q \
  tests/unit/test_v2_codec.py tests/unit/test_v2_transport.py \
  tests/unit/test_v2_contract.py tests/unit/test_v2_tooling.py
```

强制模式缺少任一工具均失败；测试程序或协议执行错误在普通开发模式下也不会跳过。

## License

[MIT](./LICENSE)
