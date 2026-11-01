# 原生事件与业务拦截

`McbeServer` 的每条连接都在由 SDK 托管的队列上运行业务回调，因此原生事件订阅始终可用。
`PlayerMessage` 仍然使用类型化的钩子；其他原生事件则按事件名称直接透传，
不需要向 `WsEventType` 添加成员。

## 连接内订阅

请在 `on_connected` 等由 SDK 托管的回调中注册观察者和拦截器：

```python
from mcbe_ws_sdk import InterceptResult, McbeServer, NativeEvent, NoOpHook


class Hook(NoOpHook):
    async def on_connected(self, state):
        token = await state.native_events.subscribe("PlayerMessage", self.observe)
        await state.native_events.intercept("PlayerMessage", self.filter_message)

    def observe(self, event: NativeEvent):
        print(event.name, event.packet)

    def filter_message(self, event: NativeEvent):
        return InterceptResult("allow")


server = McbeServer(hook=Hook())
```

要监听其他游戏事件，请传入它精确的原生名称。你也可以实现可选的
`NativeEventHook.on_native_event(state, event)`，把它作为这类事件进入业务逻辑的入口。
`NativeEvent.packet` 会保留完整的 `header`、`body` 和扩展字段，每个回调拿到的都是独立的副本。
`PlayerMessage` 仍然使用类型化的 `on_player_message` 钩子，其中的 `parsed` 参数
是根据最终放行的消息计算出来的。五个生命周期钩子分别是 `on_connected`、`on_disconnected`、
`on_player_message`、`on_command_response` 和 `on_error`。

## 生命周期与顺序

- 每次注册都会得到一个独立的令牌，即使把同一个函数注册两次，也会分别计数。
  `await state.native_events.unsubscribe(token)` 只会移除该令牌对应的那一次注册。
- 某个事件名称的第一次注册会发送 `subscribe`，移除它的最后一个观察者或拦截器时会发送
  `unsubscribe`。这两类帧都以控制优先级发送。调用返回只说明帧已经发出，
  并不代表游戏支持这个事件，也不代表订阅已被确认。
- `PlayerMessage` 是为 SDK 桥接保留的订阅，在连接初始化时建立，移除你的本地监听者不会取消订阅。
- 每条连接可注册的数量受 `dispatch.event_queue_maxsize` 限制。事件名称不能包含空白或控制字符，
  按 UTF-8 编码最多 256 字节。订阅操作超过 `dispatch.command_timeout_seconds` 就会超时。
  如果发送失败或被取消，导致远端的订阅状态无法确定，本地注册表会被关闭。
  注销未知或已经关闭的令牌会返回 `False`。
- 回调按注册顺序执行。移除注册会跳过尚未开始的回调，但不会取消正在运行的回调。
  连接关闭时，SDK 会清除注册、丢弃排队中的任务，并取消和等待托管的任务；
  它不会再向正在关闭的连接发送退订。

## 观察与拦截

观察者只接收通知，返回值会被忽略。回调抛出异常时，SDK 会先记录日志，再继续执行后面的观察者。
即使业务投递被拒绝，观察者仍然能看到原始事件。
观察者运行在一个有长度上限的独立观察队列上，WebSocket 读取循环不会等待你的代码。
较慢的异步观察者会延后之后的通知；观察队列溢出时，SDK 会拒绝新的通知，而不是阻塞回复。
同步回调必须简短，不能阻塞事件循环。

通过 `await state.native_events.intercept(name, callback)` 注册拦截器。
拦截器运行在同样有上限的业务调度器中，必须返回下列结果之一：

- `InterceptResult("allow")`：允许继续。
- `InterceptResult("reject")`：拒绝本次业务投递。
- `InterceptResult("transform", replacement_event)`：把变换后的事件交给下一个拦截器。

拒绝、抛出异常或返回无效结果，都会终止本次业务分发，但不影响观察者。
变换后的事件必须保留事件名称、事件的用途和玩家身份，也不能生成保留的桥接或控制消息。
同一玩家的消息，在经过完整的拦截链和钩子时保持先进先出，不同玩家之间可以并发推进；
其他原生事件共用一个先进先出的业务队列。业务队列溢出时，会拒绝本次分发。
回调抛出的异常会被调度器隔离，不会影响其他回调。

命令回复、错误以及保留的附加包消息，会先由 SDK 对应到请求或做分类，不会进入原生拦截链。
不可信的发送者使用保留前缀时，这些消息仍由桥接入口处理，不会被当作玩家业务放行。
拦截机制不能验证调用方身份，也不能代替宿主自己的认证与授权。

## 验证边界

`PlayerMessage` 是 SDK 一直支持并经过测试的原生事件。
其他事件名称只是原样透传：单元测试检查的是订阅和原始数据包的传递，
并不能证明某个事件在特定的基岩版版本中存在。
依赖这些事件之前，请先对照你所用的游戏版本，确认事件名称和载荷。
SDK 能接受一个事件名称字符串，并不表示该版本的游戏支持这个事件。
