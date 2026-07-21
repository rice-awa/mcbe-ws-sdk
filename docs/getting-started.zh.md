# 快速开始

本指南面向第一次使用本 SDK 的读者。读完后你将能够：

1. 安装 `mcbe-ws-sdk`
2. 把基岩版 Minecraft 连到本机 Python 服务
3. 用 `tellraw` 把玩家聊天回显进游戏
4. 理解需要实现的两个接口：`ConnectionHook` 与 `ResponseSink`

---

## 环境准备

| 项目 | 要求 |
|------|------|
| Python | **3.11+**（`python --version`） |
| 操作系统 | Windows / macOS / Linux |
| Minecraft | 基岩版（手机 / Win10·11 商店版 / 教育版等），能进一个**有作弊权限**的世界 |
| 网络 | 游戏与 Python 在同一台机器，或同一局域网；防火墙放行监听端口 |

!!! note "`/wsserver` 是怎么工作的"
    基岩版的 `/wsserver` 是**客户端**命令：玩家在世界里输入后，**这台客户端**会连到
    你的 Python 服务。不需要开专用服务器，单人世界也能用。

---

## 安装

### 从 PyPI

```bash
pip install mcbe-ws-sdk
```

### 从源码可编辑安装（开发 / 跑示例推荐）

```bash
cd mcbe-ws-sdk
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

验证：

```bash
python -c "import mcbe_ws_sdk; print(mcbe_ws_sdk.__version__)"
```

能打印版本号（例如 `0.1.0`）就说明安装成功。

---

## 5 分钟上手：跑通第一个示例

仓库自带一个「收到聊天就原样回复」的示例，建议先跑通它，再自己写代码。

### 第 1 步 — 启动 Python 服务

在 `mcbe-ws-sdk` 目录下：

```bash
python examples/basic-server/server.py
```

看到类似输出就表示在听了：

```text
[info] listening host=0.0.0.0 port=8080 url=ws://0.0.0.0:8080
[info] connect_hint command=/wsserver <this-machine-ip>:8080
[info] ready stop_with=Ctrl+C
```

可选参数：

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
python examples/basic-server/server.py --log-level DEBUG   # 看更细的帧日志
```

### 第 2 步 — 查自己的 IP

| 场景 | 在游戏里写什么 |
|------|----------------|
| 游戏和 Python **同一台电脑** | `/wsserver 127.0.0.1:8080` |
| 游戏在手机 / 另一台电脑 | `/wsserver 192.168.x.x:8080`（换成电脑的局域网 IP） |

```bash
# Linux / macOS
ip a          # 或 ifconfig
# Windows
ipconfig
```

找类似 `192.168.1.23` 的地址，**不要**用 `127.0.0.1` 给另一台设备连。

### 第 3 步 — 在游戏里连接

1. 打开一个基岩版世界（需要能输入命令：创造模式，或开了作弊）。
2. 在聊天框输入（把 IP 换成上一步的）：

   ```text
   /wsserver 127.0.0.1:8080
   ```

3. 连接成功时：

   - 游戏里会出现一条提示（示例会发欢迎通知）；
   - Python 终端会打印 `connected`。

### 第 4 步 — 发消息试一下

在聊天框随便打点字，例如 `你好`。

- 游戏内应收到：`收到 <你的名字> 的消息：你好`
- 终端应打印一条 `chat` 日志

发 `帮助` 或 `!help` 会看到示例帮助文案。

### 第 5 步 — 停止服务

在跑 Python 的终端按 `Ctrl+C` 即可优雅退出。

---

## 手写一个最小回声机器人

下面这份代码可以保存成 `my_bot.py`，在任意目录运行（需已 `pip install mcbe-ws-sdk`）。

```python
"""最小回声机器人：玩家说话，原样回复。"""

import asyncio

from mcbe_ws_sdk import (
    DefaultResponseSink,
    FlowControlSettings,
    GatewaySettings,
    McbeOutboundDelivery,
    McbeServerFacade,
    NoOpHook,
    OutboundText,
    WebsocketTransportConfig,
    enqueue_response,
)


# ── 1. 出站：把 SDK 消息变成游戏里的 tellraw ─────────────────
class MinecraftSink(DefaultResponseSink):
    def __init__(self, flow: FlowControlSettings) -> None:
        self._flow = flow

    def _delivery(self, state):
        if state.send_payload is None:
            return None
        return McbeOutboundDelivery(
            connection_id=state.id,
            send_payload=state.send_payload,
            settings=self._flow,
        )

    async def on_outbound_text(self, state, message: OutboundText) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_outbound_text(message)

    async def on_system_notification(self, state, message) -> None:
        delivery = self._delivery(state)
        if delivery is not None:
            await delivery.send_system_notification(message)


# ── 2. 入站：继承 NoOpHook，只覆盖你关心的钩子 ───────────────
class EchoHook(NoOpHook):
    async def on_connected(self, state) -> None:
        print(f"[+] 已连接: {state.id}")

    async def on_disconnected(self, state) -> None:
        print(f"[-] 已断开: {state.id}")

    async def on_player_message(self, state, event, parsed=None) -> None:
        # Bedrock 会把我们自己的 tellraw 回显成 sender=「外部」
        if event.sender in {"外部", "External"}:
            return

        text = event.message.strip()
        if not text:
            return

        print(f"[chat] {event.sender}: {text}")

        # 用 event.sender 当目标：一条 /wsserver 可能有多名玩家
        enqueue_response(
            state,
            OutboundText(
                content=f"收到 {event.sender} 的消息：{text}",
                channel="echo",
                player_name=event.sender,
                target=event.sender,
            ),
        )


# ── 3. 启动 ───────────────────────────────────────────────────
async def main() -> None:
    settings = GatewaySettings(
        websocket=WebsocketTransportConfig(host="0.0.0.0", port=8080),
    )
    facade = McbeServerFacade(
        settings=settings,
        hook=EchoHook(),
        sink=MinecraftSink(settings.flow),
    )
    print("监听 ws://0.0.0.0:8080 ，游戏里执行 /wsserver <本机IP>:8080")
    await facade.run_lifetime()


if __name__ == "__main__":
    asyncio.run(main())
```

```bash
python my_bot.py
```

### 代码在干什么？

| 部分 | 作用 |
|------|------|
| `EchoHook(NoOpHook)` | 只覆盖关心的钩子：连接 / 断开 / 收到聊天；其余钩子保持空实现 |
| `MinecraftSink` | 实现 `ResponseSink`：把 `OutboundText` 真正发成游戏命令 |
| `enqueue_response(...)` | 把回复放进该连接的发送队列，由 SDK 后台协程发出 |
| `McbeServerFacade` | 一站式入口：监听端口、握手、订阅 `PlayerMessage`、分发事件 |
| `event.sender` | **玩家身份**。不要用 `state.player_name`（一条连接可多人） |

!!! tip "优先继承 `NoOpHook`"
    `ConnectionHook` 是协议接口；日常开发请继承 `NoOpHook`，只重写需要的方法，
    不必六个钩子全写。

---

## 默认值

构造器是 **keyword-only** 的。每个参数在 `None` 时会折叠回网关默认值，因此
`McbeServerFacade()` 即可启动一个带中性 sink、空命令注册表与默认安全 addon 桥的
可运行 facade：

```python
facade = McbeServerFacade(
    settings=None,    # → GatewaySettings()
    hook=None,        # → NoOpHook()
    sink=None,        # → DefaultResponseSink()
    addon=None,       # → AddonBridgeService(settings.addon)
    registry=None,    # → CommandRegistry()
)
```

通过 `await facade.stop()` 可从另一个任务停止运行中的 facade（`run_lifetime`
会干净地展开为优雅关闭；直接取消该任务同样有效）。

---

## 宿主通常注入的接口

| 表面 | 作用 |
|------|------|
| `ConnectionHook` | 六个生命周期钩子（`on_connected`、`on_disconnected`、`on_player_message`、`on_ui_chat_reassembled`、`on_command_response`、`on_error`） |
| `ResponseSink` | 将 `OutboundText` / `SystemNotification` 路由为 Minecraft 命令 |
| `AddonBridgeService` | ScriptEvent 能力请求/响应（无全局单例） |
| `CommandRegistry` | 前缀/别名命令解析（默认为空） |
| `MCBEWS_V1` | 内置 mcbews v1 协议 profile |

### 双层接口

| 层级 | 用法 | 适合 |
|------|------|------|
| **高层** | 实现 `ConnectionHook` + `ResponseSink`，交给 `McbeServerFacade` | 绝大多数宿主（推荐） |
| **低层** | 订阅 `EventBus`（按 `WsEventType`） | 需要自己拼装生命周期时 |

### 多人 isolation 提醒

基岩版一个世界里通常只有**一条** `/wsserver` 连接，多名玩家共用它。

- 会话、历史、锁请按 `(connection_id, player_name)` 分桶
- 身份一律用本次消息的 `event.sender`，不要用连接级 `ConnectionState.player_name`

---

## 更多示例

| 示例 | 路径 | 说明 |
|------|------|------|
| 基础回声服务器 | [`examples/basic-server/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/basic-server) | 最小可运行宿主，推荐先看 |
| Addon 能力演示 | [`examples/addon-server/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/addon-server) | 聊天命令查玩家 / 背包、发 WS 命令 |
| 内存能力往返 | [`examples/addon-capability-call/`](https://github.com/rice-awa/mcbe-ws-sdk/tree/main/examples/addon-capability-call) | 不连游戏，单测式走通 bridge API |

### 想调用游戏内能力时

需要额外构建并启用配套 addon：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

把生成的 `.mcaddon` 导入世界并启用该包，再跑 `examples/addon-server`。

!!! warning "必须开启「测试版 API」"
    在世界设置的 **实验** 中打开 **测试版 API**（Beta APIs）。
    本 addon 依赖 Minecraft Script API；未开启时脚本不会加载，能力请求会超时。
    建议使用专用测试世界——实验性玩法可能影响成就 / 功能。

细节见 [addon README](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)
与示例内 README。

---

## 常见问题

**游戏里提示连接失败？**

- 确认 Python 已启动且打印了 `listening`
- 端口是否被占用 / 防火墙是否放行
- 跨设备时是否用了局域网 IP（不要用 `127.0.0.1`）
- 手机与电脑是否同一 Wi-Fi，是否开了 AP 隔离

**连上了但发消息没回复？**

- 看 Python 终端有没有 `chat` 日志
- 若只有 `external_echo_ignored`，说明过滤了自己的 tellraw 回显，属正常
- 用 `--log-level DEBUG` 看是否收到 `PlayerMessage`

**一条消息被拆成好几段？**

- 正常现象。Bedrock 的 `commandLine` 实测安全上限约 **461 字节**，SDK 会自动分片并按间隔发送。

**可以同时给多名玩家用吗？**

- 可以。同一 `/wsserver` 连接上的多名玩家都会触发 `on_player_message`；请始终用 `event.sender` 定位玩家。

**怎么安全地挂到公网？**

- 示例**没有鉴权**。公网部署前请在 `ConnectionHook` 里自己做登录 / 权限校验，并配合防火墙与反向代理。Addon 桥**不是**安全边界。

**`on_player_message` 里能直接 `await` 很久的操作吗？**

- **不要**在 hook 里长时间阻塞等待（例如等 addon 响应或 `commandResponse`）。
  facade 是串行处理入站帧的；阻塞会饿死接收循环。请用 `asyncio.create_task(...)`
  丢到后台，示例 `addon-server` 展示了正确写法。

**Addon 能力请求超时？**

- 本世界是否已启用 bridge 行为包？
- **实验 → 测试版 API** 是否已打开？未开启时脚本不会加载。
- `/wsserver` 是否仍然连着？

---

## 下一步

- [架构](architecture.md) — 分层栈与依赖倒置
- [协议](addon-bridge-protocol.md) — mcbews v1 桥接线格式
- [API 参考](reference.md) — 从源码生成
