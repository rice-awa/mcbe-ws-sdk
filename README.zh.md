# mcbe-ws-sdk

[![Languages](https://img.shields.io/badge/Languages-English-blue?style=flat-square)](./README.md)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?style=flat-square)](https://www.python.org/)
[![License](https://img.shields.io/badge/License-MIT-green?style=flat-square)](./LICENSE)

面向 **Minecraft 基岩版（Bedrock）** 的通用 **WebSocket 网关 SDK**。

SDK 负责 WebSocket 传输、MCBEWS/2 准入、请求关联、字节安全命令分片、能力调用与请求流。
模型服务、消息代理、认证和业务历史由宿主负责。

```text
Minecraft 客户端  ←── /wsserver IP:端口 ──→  Python 宿主（McbeServer）
                                              │
                                              ├── ConnectionHook / ResponseSink
                                              └── V2Bridge ←→ 配套附加包
```

## 提供什么

- 唯一公开入口 `McbeServer`；每条连接必须先完成 MCBEWS/2 准入才会执行业务。
- 双向能力：Python 调附加包，附加包也可以调 Python。
- 通知、元数据与持续请求流。
- 字节安全的 tellraw / scriptevent 投递；默认 461 字节经验预算，部署只能降低。
- 实现 Minecraft 侧线协议的 TypeScript 配套附加包。

## 环境要求

- Python 3.11+。
- 可使用 `/wsserver` 的基岩版客户端与世界。
- 使用附加包能力时：导入配套附加包，并在目标世界开启 **实验 → 测试版 API（Beta APIs）**。
  未开启时脚本不会加载，准入会超时。

## 安装

在仓库目录中安装：

```bash
pip install -e ".[dev,docs]"
```

如果已有构建好的 wheel 或 sdist，可直接安装文件：

```bash
pip install ./dist/mcbe_ws_sdk-*.whl
```

## 第一次连接

启动仓库自带的命令示例，它会用 tellraw 回复：

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

在游戏中执行 `/wsserver <宿主IP>:8080`，然后发送带前缀的命令：

```text
!help
!wscmd time query daytime
```

示例只处理去掉首尾空白后以 ASCII `!` 开头的消息。
普通聊天、裸 `help` / `帮助`、句中 `!` 与全角 `！` 都会被静默忽略，不回复、不调用能力。
裸 `!` 返回一次用法提示；未知 `!command` 返回一次提示。

需要附加包的宿主可以在受管理钩子中调用 `server.v2.request(...)`：

```python
import asyncio

from mcbe_ws_sdk import McbeServer, NoOpHook


class Hook(NoOpHook):
    def __init__(self) -> None:
        self.server: McbeServer | None = None

    async def on_player_message(self, state, event, parsed=None) -> None:
        message = event.message.strip()
        if message != "!inv":
            return
        response = await self.server.v2.request(
            state.id, "get_inventory_snapshot", {"target": event.sender}
        )
        print(response["result"])


async def main() -> None:
    hook = Hook()
    hook.server = McbeServer(hook=hook)
    await hook.server.run_lifetime()


asyncio.run(main())
```

`McbeServer()` 使用中性响应接收器：它会打印结果，但不会自动渲染回复。
需要 tellraw 投递时使用 `McbeOutboundDelivery`，示例服务就是这样做的。

## 附加包

默认包包含两个只读快照、`demo.echo`、`demo.ticks` 流和 `run_world_command`：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

精简包只保留两个只读快照：

```bash
npm run build:base
npm run mcaddon:base
```

导入生成的 `.mcaddon`，在目标世界开启 **实验 → 测试版 API（Beta APIs）**，并启用行为包。
依赖、构建变体与信任边界见[附加包说明](./addon/README.zh.md)。

## 文档

| 页面 | 内容 |
| --- | --- |
| [快速开始](./docs/getting-started.zh.md) | 安装、附加包、首次连接、命令与排查 |
| [架构](./docs/architecture.zh.md) | 准入状态机、运行时、桥接分层 |
| [扩展作者](./docs/extension-authors.zh.md) | 能力、通知、元数据与流式 |
| [协议（MCBEWS/2）](./docs/protocol-v2.zh.md) | 准入、载体、限制与流 |
| [追踪与诊断](./docs/diagnostics.zh.md) | 生命周期日志、`--log-raw`、脱敏 |
| [原生事件](./docs/native-events.zh.md) | 观察者、拦截器与订阅 |
| [迁移指南](./docs/migration.zh.md) | 从双协议版本升级的破坏性变化 |
| [API 参考](./docs/reference.zh.md) | 由公开源码生成 |

英文文档见各页面的 `.md` 文件，以及文档站的默认语言。

## 示例

| 路径 | 说明 |
| --- | --- |
| [`examples/basic-server/`](./examples/basic-server/) | `!` 命令与原生 `commandRequest` 回复 |
| [`examples/addon-server/`](./examples/addon-server/) | 附加包能力、元数据与流 |

## 开发

```bash
pip install -e ".[dev,docs]"
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
python tools/format.py          # ruff format+fix；有 Node 时跑 prettier
python tools/format.py --check  # 持续集成方式，只检查不写入
```

MCBEWS/2 跨语言测试需要 Node.js 22 和 `npm ci --prefix addon`。
缺少 Node.js 或 TypeScript 时只跳过依赖运行器的测试；协议或运行器错误始终失败。
强制要求两端工具：

```bash
MCBE_REQUIRE_CROSS_LANGUAGE=1 python -m pytest -p no:cacheprovider -q \
  tests/unit/test_v2_codec.py tests/unit/test_v2_transport.py \
  tests/unit/test_v2_contract.py tests/unit/test_v2_tooling.py
```

## License

[MIT](./LICENSE)
