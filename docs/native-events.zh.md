# 原生事件与业务拦截

`McbeServer` 为每条连接使用受管理业务队列，因此原生事件订阅始终可用。
`PlayerMessage` 继续使用类型化钩子；其他原生事件按名称透传，无需向 `WsEventType`
添加成员。

## 连接内订阅

在 `on_connected` 等受管理回调中注册观察者与拦截器：

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

其他事件传入精确的原生名称，也可实现可选的 `NativeEventHook.on_native_event(state, event)`
作为业务入口。`NativeEvent.packet` 保留完整的 `header`、`body` 与扩展字段；
每个回调收到独立副本。`PlayerMessage` 的 `parsed` 参数基于最终放行的消息计算。
五个生命周期钩子分别是 `on_connected`、`on_disconnected`、`on_player_message`、
`on_command_response`、`on_error`。

## 生命周期与顺序

- 每次注册返回独立令牌，同一函数重复注册也分别计数。
  `await state.native_events.unsubscribe(token)` 仅移除该次注册。
- 首个监听者发送 `subscribe`，移除最后一个观察者或拦截器时发送 `unsubscribe`。
  两者均走控制优先级发送队列；返回只表示已发送，不代表游戏支持或确认订阅。
- `PlayerMessage` 是桥接保留订阅，在连接初始化时建立，移除本地监听者不会退订。
- 单连接注册上限使用 `dispatch.event_queue_maxsize`。
  事件名称不能含空白或控制字符，UTF-8 编码后最多 256 字节。
  订阅操作使用 `dispatch.command_timeout_seconds` 限时。
  发送失败或取消导致远端状态不确定时，关闭本地注册表。
  注销未知或已关闭令牌返回 `False`。
- 回调按注册顺序执行。移除会跳过尚未开始的回调，不取消正在执行的回调。
  连接关闭释放注册、丢弃排队项，并取消和等待受管理任务；不会向关闭中的连接发送退订。

## 观察与拦截

观察者只接收通知，返回值被忽略。异常记入日志后继续执行后续观察者。
即使业务投递被拒绝，观察者仍能看到原始事件。
异步观察者在有界观察队列中执行，不阻塞读取与回复关联；慢观察者会延迟后续通知，
溢出时拒绝通知。同步回调必须简短，不能阻塞事件循环。

通过 `await state.native_events.intercept(name, callback)` 注册拦截器。
拦截器在有界业务调度器中运行，必须返回以下结果之一：

- `InterceptResult("allow")`：允许继续。
- `InterceptResult("reject")`：拒绝本次业务投递。
- `InterceptResult("transform", replacement_event)`：将变换后的事件交给下一拦截器。

拒绝、异常或无效结果会终止本次业务分发，观察不受影响。
变换必须保留事件名称、事件用途和玩家身份，不能生成保留的桥接或控制消息。
同一玩家的完整拦截链与钩子保持先进先出，其他玩家可并发推进；
其他原生事件共用一个先进先出的业务队列。业务队列溢出拒绝本次分发，回调异常由调度器隔离。

命令回复、错误及保留的附加包消息先由 SDK 关联或分类，不进入原生拦截链。
不可信发送者使用保留前缀时，仍由桥接入口消费，不会作为玩家业务放行。
此机制不替代宿主认证与授权。

## 验证边界

`PlayerMessage` 是 SDK 既有且经过测试的原生事件。
其他名称只提供透传能力：单元测试验证订阅和原始数据传递，
不证明特定名称在某个基岩版版本中存在。
使用前须验证游戏版本中的名称与载荷，不能将字符串可注册理解为游戏支持声明。
