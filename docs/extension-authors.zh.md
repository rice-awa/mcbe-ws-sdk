# 扩展作者指南

扩展只需要注册处理函数。待回复请求表、线格式编号和分片状态都由 SDK 维护，不需要你操心。
第一次接触时，请先读[快速开始](getting-started.md)；只有在升级现有宿主时，才需要参考[迁移指南](migration.md)。

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

处理器接收 `(name, payload, CapabilityContext)`，返回一个映射，或返回最终得到映射的可等待对象。
校验器返回 `None` 表示通过，返回字符串则表示拒绝，字符串即拒绝原因。

注册已存在的名称时，必须显式传入 `replace=True`。注销只影响之后的调用，
过期的注册句柄也无法删除替换它的新注册；正在执行的调用会继续使用它开始时选定的处理器。
在协议上，未知方法、不合法的输入和处理器异常分别对应 `UNSUPPORTED_METHOD`、
`INVALID_PARAMS` 和 `HANDLER_FAILED`。

`context.client` 是当前连接的 V2 端点，可以用它向调用方反向发起请求、通知和流。
如果调用的方法所在的顺序组正在执行，这次调用会被拒绝，以免处理器等待自己排在队列中的任务。
元数据里的身份不可信，详见本页末尾的“信任边界”。

## 注册附加包能力

在自己的附加包组装代码中使用路由器接口：

```typescript
import { registerCapability } from "./bridge/router";

const registration = registerCapability("custom.echo", (_method, payload, context) => ({
  text: payload.text,
  metadata: context.metadata,
}));
// unregisterCapability(registration)
```

处理器接收 `(method, payload, context)`，返回对象或 Promise。
上下文包含 `caller`、`requestVersion`、`metadata`、`client` 与 `cancellation`。
可选的校验器返回一条消息，就表示拒绝这份输入。

你注册的能力与内置能力并存，不需要修改 SDK。注册重复的名称会抛出错误，
传入 `replace: true` 才会覆盖。调用进行期间注销该能力，不会影响这次调用。

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

const unregister = registerV2Stream("custom.ticks", async function* (_method, params, context) {
  for (let index = 0; index < (params.count ?? 5); index += 1) {
    if (context.cancellation?.cancelled) return;
    yield { index };
  }
});
```

生产者逐项产生 JSON，不需要预先知道总长度。较大的数据项由 SDK 分片传输，重组后作为一个完整的逻辑项交付。
注册已存在的流名称会抛出错误。

## 调用附加包

协议协商完成后，由 SDK 托管的钩子可以直接使用桥接：

```python
selection = await server.v2.ready(state.id)     # 需要显式等待协议协商时使用
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

只有在钩子之外启动的任务才需要 `ready()`，因为钩子本身已经运行在 `READY_V2` 之后。

远端返回协议错误时，调用会抛出带有 `code` 和一段简短说明的 `RemoteCallError`。
传输故障、超时和取消同样会结束调用。通知没有需要等待的回复，所以远端处理器出错时，
这个错误无法作为响应返回给调用方。

元数据紧凑 JSON 编码后最多 4096 个 UTF-8 字节，并遵守协议对嵌套深度和数值的规则。
未知的可选字段及其嵌套值会原样往返保留。形如 `sdk.*` 的键是保留的，调用方不能设置；
需要追踪请求时，请使用 `trace_id`。身份字段不会授予任何权限。

## 取消与错误

- 取消一个等待中的 Python 请求，会结束这次等待。已经交给传输层的帧仍可能送达，
  已经执行的世界修改也不会撤销。
- 附加包一侧的调用方，为每个请求使用独立的 `V2RequestCancellation`：调用 `cancel()`，
  并在继续发送新工作之前检查 `cancelled`。
- 流的消费者如果可能提前退出，请使用 `async with` 或 `await stream.aclose()`。
  取消一个正在等待的读取，只会取消这条流，不影响其他请求。
- 生产者必须配合取消。JavaScript 生产者应当检查 `context.cancellation`，
  因为一个永远不会完成的 Promise 无法被强制终止。
- 向玩家展示失败时，请包含方法或能力名、错误码、最多 256 个 UTF-8 字节的可读说明，以及行动提示。
  内部请求编号和堆栈只写入日志。

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

慢消费者会以 `LIMIT_EXCEEDED` 失败，SDK 随后发送取消。按固定间隔发送并不能给接收方提供背压，
目前也没有接收额度机制。详见 [MCBEWS/2 协议](protocol-v2.md#request-streams)。

## 构建变体与演示注册

默认附加包会注册全部内置能力：`get_player_snapshot`、`get_inventory_snapshot`、
`demo.echo`、`demo.ticks` 流和 `run_world_command`。`--base` 精简包只注册
`get_player_snapshot` 与 `get_inventory_snapshot`。两种包的构建命令见
[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md#构建)。

`--base` 或 `MCBE_BASE=1` 会选择 `main.ts`，默认入口则是 `main.demo.ts`，
两者都输出到 `dist/scripts/main.js`。想添加自己的处理器，请在自己的入口中导入它们，
并在 `initializeAfterWorldLoad` 里注册一次。

`run_world_command` 默认只应用命令禁止列表，需要更严格时，可以向 `handleRunWorldCommand`
传入明确的允许列表。附加包必须导入开启了 **实验 → 测试版 API（Beta APIs）** 的世界，
步骤见[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md#在世界中启用)。
自动化示例不代表实机一定受支持，请自行验证游戏版本和平台。

## 信任边界

桥接不是安全边界。下面这些都不能代替宿主自己的认证和授权：消息经由哪个载体送达、
模拟玩家 `MCBEWS_BRIDGE`、元数据里的身份，以及附加包一侧的命令过滤。
无论附加包采用什么策略，都请在 Python 能力处理器中检查每个调用方的权限。
