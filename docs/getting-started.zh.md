# 快速开始

本指南使用**尚未发布的当前源码**中的 `McbeServer`。
当前 PyPI 发布版是 `0.2.1`，`pip install mcbe-ws-sdk` 不会安装本轮新增接口。
旧入口及回调合同见[迁移指南](migration.md)。

## 安装当前源码

需要 Python 3.11+，以及能使用 `/wsserver`、允许执行命令的基岩版客户端与世界。
新增接口不代表扩大平台或游戏版本支持承诺；实机验收仍待完成。

```bash
cd mcbe-ws-sdk
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

## 连接并回复聊天

启动可运行的回声示例：

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

游戏与 Python 在同一台机器时，执行 `/wsserver 127.0.0.1:8080`。
跨设备时改用运行 Python 的机器的局域网 IP，并在防火墙放行监听端口。
发送聊天消息后，示例会用 tellraw 回复，并过滤游戏产生的外部消息回声。
使用 `--log-level DEBUG` 查看诊断日志，按 `Ctrl+C` 停止。

下面的最小宿主会打印聊天，并执行一条命令：

```python
import asyncio
from mcbe_ws_sdk import McbeServer, NoOpHook

class Hook(NoOpHook):
    async def on_connected(self, state):
        print("connected:", state.id)

    async def on_player_message(self, state, event, parsed=None):
        if event.sender in {"External", "外部"}:
            return
        print(event.sender, event.message)
        if event.message == "!daytime":
            response = await state.command_client.run("time query daytime")
            print(response)

async def main():
    server = McbeServer(hook=Hook())
    await server.run_lifetime()

asyncio.run(main())
```

`McbeServer` 由 SDK 管理任务，钩子中可以直接等待回复，不会堵住 WebSocket 读取循环。
同一玩家的消息保持先进先出，不同玩家可以并发推进；同步回调仍须保持简短、不阻塞。
宿主不需要维护等待表、请求任务或分片缓存。
`McbeServer()` 默认使用中性的 `DefaultResponseSink`，不会自动渲染应用回复。
基础示例提供了使用 `McbeOutboundDelivery` 的游戏响应接收器，并将 `OutboundText` 排队发送。

本次玩家身份和回复目标请使用 `event.sender`。一条连接可能承载多名玩家，
应用历史与锁应按 `(state.id, event.sender)` 隔离。
已弃用的 `state.player_name` 只是最近发言者的便捷指针。

## 通过附加包查询背包

构建配套附加包：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

导入生成的 `.mcaddon`，在**目标世界**的设置中开启
**实验 → 测试版 API（Beta APIs）**，并启用行为包。
未开启时脚本不会加载，能力请求会超时。
详见[附加包启用说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)。
然后回到仓库根目录启动示例：

```bash
python examples/addon-server/server.py
```

用 `/wsserver <宿主 IP>:8080` 连接后，发送 `!inv` 或 `!player`。
`!wscmd time query daytime` 使用原生命令，不需要附加包；
`!cmd` 使用需要另外启用的附加包世界命令处理器。

在自己的受管理钩子中，使用同一个服务的协商桥接：

```python
response = await server.v2.request(
    state.id, "get_inventory_snapshot", {"target": event.sender}
)
print(response["result"])
```

SDK 负责请求关联、超时、分片与重组。
v2 远端错误抛出 `RemoteCallError`；v1 回退把能力结果保留在 `result` 中，
其中也可能包含 `ok: false`。收到响应不等于执行成功，需要检查结果。
原生命令还需检查 `statusCode`。取消调用会结束等待，但不撤销已经执行的命令。

## 扩展宿主

| 需求 | 公共入口 |
| --- | --- |
| 自定义 Python 能力 | `server.v2.registry.register(name, handler, validate=...)` |
| 自定义附加包能力 | 附加包路由器的 `registerCapability(name, handler)` |
| 元数据与追踪 | `server.v2.request(..., metadata=..., trace_id=...)` |
| 事件观察者 | `await state.native_events.subscribe(name, observer)` |
| Python 流生产者 | `server.v2.streams.register(name, handler)` |
| 流消费者 | `await server.v2.stream(state.id, name, params)` |

完整注册、消费、取消及限制见[扩展作者指南](extension-authors.md)。
附加包示例提供可选的自定义能力与流注册；使用 `!echo`、`!stream`、
`!stream-cancel` 前请按其 README 安装演示处理器。

探测有时间上限，普通聊天和原生命令独立运行。旧附加包可为已知方法回退到 v1。
元数据和流式必须协商出对应能力，不支持时明确失败，不静默丢弃必需字段。
流缓存有界，慢消费者会导致流终止；当前没有接收额度机制。详见[v2 合同](protocol-v2.md)。

## 现有宿主与排查

`McbeServerFacade()` 仍默认使用旧串行回调路径。
只有该路径在钩子中等待由读取循环接收的回复时，才需要单独管理后台任务。
新入口不采用这种绕过方式。默认值和返回值对照见[迁移表](migration.md)。

- 连接失败：检查监听进程、IP、端口和防火墙。
- 能力调用超时：检查本世界启用的附加包、**实验 → 测试版 API** 和 `/wsserver` 连接。
- 文本分成多段：默认命令预算为 461 字节，部署可以降低；原生命令保持原子，超限即拒绝。
- 停止服务：调用 `await server.stop()` 或取消 `run_lifetime()`；受管理任务与待完成调用会被清理，重连不会重放世界修改。

示例没有鉴权。桥接不是安全边界；宿主负责调用方认证、授权与应用状态。
