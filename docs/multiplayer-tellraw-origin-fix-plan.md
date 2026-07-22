# 多人下行 tellraw origin 修复方案

## 根因

`MinecraftCommand.create_tellraw()` 将 WebSocket `commandRequest.body.origin.type` 硬编码为 `say`：

```python
origin=MinecraftOrigin(type="say")
```

`say` 是 `PlayerMessage` 事件中的消息类型，不是命令请求的来源类型。Bedrock WebSocket 的 `CommandRequest` 示例使用 `{"type": "player"}`，即使命令内容本身是 `say Hello`。因此 SDK 的 `tellraw` 请求虽然可能返回 `statusCode=0`，但多人场景下远端玩家的客户端可能不显示消息。

## 最小修改

修改 `src/mcbe_ws_sdk/protocol/minecraft.py`：

- 删除 `create_tellraw()` 对 `MinecraftOrigin(type="say")` 的显式设置；
- 使用 `MinecraftCommandBody` 默认的 `player` origin，或显式改为 `MinecraftOrigin(type="player")`；
- 不新增 API，不修改分片、队列或目标玩家逻辑。

同步修改测试：

- `tests/unit/test_delivery.py` 将 tellraw origin 断言改为 `player`；
- `tests/unit/test_protocol.py` 增加 `create_tellraw()` 的 origin 回归断言。

## 验证

在 SDK 目录执行：

```bash
pytest tests/unit/test_protocol.py tests/unit/test_delivery.py
ruff check --no-cache src tests examples
mypy --no-incremental src
pytest -p no:cacheprovider -q
```

游戏内用两个玩家分别验证普通 tellraw 回复和 raw `tellraw` 命令，确认远端玩家可以看到目标消息。

`@玩家名` 被解析为空数组属于目标名称规范化问题，不在本修复范围内。
