# 原生事件与业务拦截

新扩展须显式使用 DispatchSettings(mode="reliable")；默认构造和旧串行回调
顺序不变。PlayerMessage 继续使用现有类型化钩子；其他原生事件按名称透传，
无需向 WsEventType 添加成员。完整示例见 [英文页面](native-events.md)。

## 连接内订阅

在 on_connected 等受管理回调中，调用
await state.native_events.subscribe("PlayerMessage", observer)，保存返回令牌。
其他事件直接传精确原生名称，可实现可选 NativeEventHook.on_native_event(state,
event) 作为业务入口。NativeEvent.packet 保留 header、body 和扩展字段；每个
回调收到独立副本。PlayerMessage 的 parsed 参数基于最终放行的消息计算。
现有类型化钩子不需适配；旧 UI 三参数回调仍须显式使用 LegacyUiChatHookAdapter。

- 每次注册均返回独立令牌，同一函数重复注册也分别计数。调用
  await state.native_events.unsubscribe(token) 仅移除该次注册。
- 首名监听者发送 subscribe，末名观察者或拦截器移除时发送 unsubscribe，
  均走控制优先级发送队列；返回仅表示已发送，不代表游戏支持或确认订阅。
- PlayerMessage 是桥接保留订阅，连接初始化时建立，移除本地监听者不会退订。
- 单连接注册上限使用 dispatch.event_queue_maxsize；名称不能含空白/控制字符，
  最多 256 UTF-8 字节。订阅操作以 dispatch.command_timeout_seconds 限时。
  发送失败或取消导致线端状态不确定时，关闭本地注册表；未知或已关闭令牌返回 False。
- 回调按注册顺序执行。移除会跳过尚未开始的回调，不取消正在执行的回调。
  连接关闭释放注册、丢弃排队项，并取消和等待受管理任务，不向关闭中的连接发退订。

## 观察与拦截

观察者仅通知，返回值忽略；异常记日志后继续后续观察者。即使业务拒绝，观察者
仍可看到原始事件。异步观察者在有界观察队列中执行，不阻塞读取和配对；慢观察者
会延迟后续通知，溢出拒绝通知。同步回调必须短且不阻塞事件循环。

拦截器通过 await state.native_events.intercept(name, callback) 注册，
在有界业务调度器中执行，须返回 InterceptResult("allow")、
InterceptResult("reject") 或 InterceptResult("transform", replacement_event)。
变换结果依次传给下一拦截器；拒绝、异常或无效结果终止该次业务分发，观察不受影响。
变换须保留事件名、事件用途和玩家身份，不能生成保留桥接/控制前缀。
同一玩家的整条拦截链与钩子保持 FIFO，其他玩家可并行；其他原生事件共享一个 FIFO
业务队列。业务队列溢出拒绝该次分发；业务回调异常由调度器隔离。

命令回复/错误和保留 addon 消息先完成 SDK 配对或分类，不进入原生拦截链。
伪造发送者使用保留前缀仍由桥接入口消费，不会作为玩家业务放行。
此机制不替代宿主鉴权或授权。

## 验证边界

PlayerMessage 是 SDK 既有、已测试的原生事件。其他名称只提供透传能力；单元测试
证明订阅与原始数据传递，不证明任何特定名称在某个基岩版版本中存在。
依赖前须自行验证游戏版本中的名称与载荷，不能将字符串可注册理解为游戏支持声明。
