# 扩展作者指南

扩展只注册处理函数，不维护请求等待表、线格式编号或分片状态。
新宿主先读[快速开始](getting-started.md)；更新现有宿主时参考[迁移指南](migration.md)。

## 注册 Python 能力

在服务使用的 `V2Bridge` 上注册：

```python
from mcbe_ws_sdk import CapabilityContext, McbeServer, V2Bridge

bridge = V2Bridge()
server = McbeServer(v2=bridge)


async def echo(name: str, payload: dict, context: CapabilityContext) -> dict:
    return {"ok": True, "payload": dict(payload), "metadata": dict(context.metadata or {})}


def validate(payload: dict, context: CapabilityContext) -> str | None:
    return None if "text" in payload else "text is required"


registration = bridge.registry.register("demo.echo", echo, validate=validate)
# bridge.registry.unregister(registration)
```

处理器接收 `(name, payload, CapabilityContext)`，返回映射，或最终返回映射的可等待对象。
校验器返回 `None` 表示通过，返回字符串表示拒绝及原因。

同名注册必须显式传入 `replace=True`。注销只影响后续调用，旧句柄不能删除替换后的注册；
正在执行的调用继续使用开始时选定的处理器。未知方法、输入不合法、处理器异常分别映射为
`UNSUPPORTED_METHOD`、`INVALID_PARAMS`、`HANDLER_FAILED`。

`context.client` 是当前连接的 V2 端点，可用于反向请求、通知与流。
调用正在执行的方法顺序组会被拒绝，避免递归等待队列中的自身任务。
元数据中的身份不可信，传输归属与宿主授权仍须分开处理。

## 注册附加包能力

在自己的附加包组装代码中使用路由器接口：

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("demo.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

处理器接收 `(method, payload, context)`，返回对象或 Promise。
上下文包含 `caller`、`requestVersion`、`metadata`、`client` 与 `cancellation`。
可选校验器返回消息即拒绝输入。

自定义能力与内置能力共存，无需修改 SDK。重名默认抛错，传入 `replace: true` 才覆盖。
调用进行中注销不影响当前调用。

当前版本没有聚合的 `setCapabilityHandler` 入口，请按名称逐个注册。

## 注册流生产者

Python：

```python
async def ticks(name: str, params: dict, context: CapabilityContext):
    for index in range(params.get("count", 5)):
        yield {"index": index}

bridge.streams.register("demo.ticks", ticks)
```

附加包：

```typescript
import { registerV2Stream } from "./bridge/router";

const unregister = registerV2Stream("demo.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

生产者逐项产生 JSON，不需要预先知道总长度。大项由 SDK 分片，重组后作为一个逻辑项交付。
重复注册同名流会抛错。

## 调用附加包

准入后，受管理钩子可以直接使用桥接：

```python
selection = await server.v2.ready(state.id)     # 需要显式等待准入时使用
response = await server.v2.request(
    state.id,
    "demo.echo",
    {"text": "hello"},
    metadata={"demo.locale": "zh", "demo.options": {"compact": True}},
    trace_id="demo-trace",
    require={"metadata/1"},
)
print(response["result"], response.get("meta"))

await server.v2.notify(state.id, "demo.event", {"value": 1})

stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

只有不在钩子中启动的工作才需要 `ready()`；钩子本身已运行在 `READY_V2` 之后。

远端协议错误抛出带 `code` 和受限说明的 `RemoteCallError`。
传输故障、超时与取消也会结束调用。通知没有回复等待项，因此无法用响应报告远端处理器失败。

元数据上限为紧凑 JSON 编码后 4096 个 UTF-8 字节，并遵守协议深度与数值规则。
未知可选字段与嵌套值按约定往返保留。调用方不能传入保留的 `sdk.*` 键；
追踪请使用 `trace_id`。身份字段不授予权限。

## 取消与错误

- 取消等待中的 Python 请求会结束等待。已经交给传输层的帧仍可能送达，
  已经执行的世界修改不会撤销。
- 附加包调用方为每个请求使用独立的 `V2RequestCancellation`：调用 `cancel()`，
  并在继续工作前检查 `cancelled`。
- 流消费者用 `async with` 或 `await stream.aclose()` 提前退出；
  取消正在等待的读取只取消该流，不影响其他请求。
- 生产者必须配合取消。JavaScript 生产者应检查 `context.cancellation`；
  任意永不完成的 Promise 无法被强制解除。
- 玩家看到的失败包含方法/能力名、错误码、最多 256 个 UTF-8 字节的可读说明和行动提示。
  内部请求编号与堆栈只留在日志中。

## 限制

| 资源 | 默认值 |
| --- | --- |
| 单条消息 | 65536 个 UTF-8 字节 |
| 元数据 | 紧凑 JSON 编码后 4096 个 UTF-8 字节 |
| 下行命令行 | 461 个 UTF-8 字节，只能降低 |
| 上行载体帧 | 256 个 Unicode 码点且不超过 1024 个 UTF-8 字节 |
| 单端点活跃流 | 双向合计 16 条 |
| 单消费者缓存 | 32 项 / 131072 个逻辑项字节 |
| 单端点缓存 | 262144 字节 |
| 流空闲期限 | 开流、产生下一项、发送单条消息各 30000 毫秒 |

慢消费者以 `LIMIT_EXCEEDED` 失败并发送取消。固定发送间隔不等于接收方背压；
当前没有接收额度机制。详见[协议合同](protocol-v2.md#request-streams)。

## 构建变体与演示注册

默认附加包会注册全部内置能力：`get_player_snapshot`、`get_inventory_snapshot`、
`demo.echo`、`demo.ticks` 流与 `run_world_command`：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

`--base` 精简包只注册 `get_player_snapshot` 与 `get_inventory_snapshot`：

```bash
npm run build:base
npm run mcaddon:base
```

`--base` 或 `MCBE_BASE=1` 选择 `main.ts`；默认入口是 `main.demo.ts`。
输出路径仍是 `dist/scripts/main.js`。添加自己的处理器时，在自己的入口中导入，
并在 `initializeAfterWorldLoad` 内注册一次。

`run_world_command` 默认应用危险命令禁止列表；需要更严格时可向
`handleRunWorldCommand` 传入显式允许列表。无论附加包策略如何，宿主侧授权都必须保留。

附加包必须导入目标世界，并开启 **实验 → 测试版 API（Beta APIs）**。
自动化示例不建立实机支持承诺；请单独验证游戏版本与平台。

## 信任边界

桥接不是安全边界。调用方归属、模拟玩家 `MCBEWS_BRIDGE`、元数据身份与附加包侧命令过滤
都不能替代宿主认证与授权。请在 Python 能力处理器中执行调用方权限检查。
