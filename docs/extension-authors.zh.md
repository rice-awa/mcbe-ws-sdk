# 扩展作者指南

这些接口属于**尚未发布的当前源码**，不在 PyPI `0.2.1` 中。
新宿主先读[快速开始](getting-started.md)，现有宿主参考[迁移指南](migration.md)。
扩展只注册处理函数，不维护请求等待表、线格式编号或分片状态。

## 注册 Python 能力

在服务使用的 `V2Bridge.registry` 上注册，并在开始接收请求前完成组装：

```python
from mcbe_ws_sdk import CapabilityContext, McbeServer, V2Bridge

bridge = V2Bridge()
server = McbeServer(v2=bridge)

async def echo(name, payload, context: CapabilityContext):
    return {"ok": True, "payload": dict(payload), "metadata": dict(context.metadata)}

def validate(payload, context):
    return None if "text" in payload else "text is required"

registration = bridge.registry.register("demo.echo", echo, validate=validate)
# bridge.registry.unregister(registration)
```

处理器接收 `(name, payload, CapabilityContext)`，返回映射，或最终返回映射的可等待对象。
校验器返回 `None` 表示通过，返回字符串表示拒绝及原因。
同名注册必须显式传入 `replace=True`。注销只影响后续调用，旧句柄不能删除替换后的注册；
正在执行的调用继续使用已经选定的处理器。
v2 将未知方法、输入不合法、处理器异常分别映射为
`UNSUPPORTED_METHOD`、`INVALID_PARAMS`、`HANDLER_FAILED`。

`context.client` 是当前连接的 v2 端点，可用于反向请求与通知。
调用正在执行的方法顺序组会被拒绝，避免递归等待队列中的自身任务。
元数据中的身份不可信，传输来源与宿主授权仍须分开处理。

## 注册附加包能力

在自己的附加包组装代码中使用路由器导出的接口：

```typescript
import { registerCapability } from "./bridge/router";

registerCapability("demo.echo", (_name, payload, context) => ({
  ok: true,
  payload,
  metadata: context.metadata,
}));
```

自定义能力与内置能力共存，无需修改 SDK。
构建并导入生成的附加包，在目标世界开启 **实验 → 测试版 API（Beta APIs）**，
然后启用行为包；未开启时脚本不会加载。
详见[附加包说明](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/README.zh.md)。
这不代表新增了平台或游戏版本支持承诺。

## 元数据与通知

执行 `selection = await bridge.ready(state.id)` 后，可发起协商调用：

```python
response = await bridge.request(
    state.id, "demo.echo", {"text": "hello"},
    metadata={"demo.locale": "zh", "demo.options": {"compact": True}},
    trace_id="demo-trace", require={"metadata/1"},
)
print(response["result"], response.get("meta"))
```

v2 远端协议错误抛出 `RemoteCallError`；传输故障、超时、取消也会结束调用。
v1 回退结果仍包在 `response["result"]` 中，需要检查能力结果本身的 `ok` 字段。
只要提供元数据，就要求 `metadata/1`，不会静默降级到 v1。
未知可选字段与嵌套值按约定往返保留。元数据上限为紧凑 JSON 编码后 4096 个 UTF-8 字节，
并遵守协议深度与数值规则。调用方不能传入保留的 `sdk.*` 键；追踪请使用 `trace_id`。
身份字段不授予权限。

仅限 v2 的通知可在确认协商出端点后调用 `selection.endpoint.notify(...)`。
通知没有回复等待项，因此无法用响应报告远端处理失败。详见[v2 合同](protocol-v2.md)。

## 订阅原生事件

在受管理的钩子中使用：

```python
token = await state.native_events.subscribe("PlayerMessage", observe)
# Later: await state.native_events.unsubscribe(token)
```

`observe(event)` 接收 `NativeEvent`。观察者不能消费业务消息，返回值会被忽略；
拦截器则显式允许、拒绝或变换业务投递。注册与观察队列有界，断连时清理。
其他事件名称只是透传，不代表游戏版本支持该事件。详见[原生事件](native-events.md)。

## 持续流式

在服务的桥接对象上注册生产者：

```python
async def count(name, params, context):
    for value in range(params["count"]):
        yield {"value": value}

bridge.streams.register("demo.count", count)
```

对端注册同一生产者后，可以这样消费：

```python
stream = await bridge.stream(state.id, "demo.count", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

附加包端对应接口是 `registerV2Stream(name, handler)`，生产者返回 `AsyncIterable`。
消费不需要预先知道总长度；大项由 SDK 分片，重组后作为一个逻辑项交付。
提前退出请使用上下文管理器或 `await stream.aclose()`。
取消正在等待的读取只取消该流，不影响其他请求。生产者必须配合取消；
JavaScript 生产者应检查 `context.cancellation`。

流要求协商出 `stream/1`。默认最多 16 条活跃流；每个消费者最多缓存 32 项、
131072 个逻辑项字节，每个端点最多缓存 262144 字节。
慢消费者以 `LIMIT_EXCEEDED` 明确失败；当前没有接收额度机制，固定延迟也不等于接收方背压。
失败或取消会丢弃排队项，不撤销世界修改。
截止时间与重复抑制边界见[流式合同](protocol-v2.md#request-streams)。
