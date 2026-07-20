# Basic MCBE WebSocket Server

这是一个基于 `mcbe-ws-sdk` 的最小可运行服务端示例。它会：

1. 监听 `0.0.0.0:8080`；
2. 接受 Minecraft Bedrock 的 `/wsserver` 连接；
3. 订阅 `PlayerMessage`；
4. 在控制台打印玩家聊天；
5. 使用 SDK 的 `McbeOutboundDelivery` 通过 `tellraw` 回复玩家。

## 运行

先在仓库根目录安装 SDK（推荐开发模式）：

```bash
pip install -e ".[dev]"
```

启动服务端：

```bash
python examples/basic-server/server.py
```

也可以指定绑定地址和端口：

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

需要查看分片发送、response sender 等细节时，打开 DEBUG：

```bash
python examples/basic-server/server.py --log-level DEBUG
```

控制台默认使用紧凑日志（无 `[info     ]` 填充空格），所有输出统一为：

```text
2026-07-20 16:40:16 [info] listening host=0.0.0.0 port=8081 url=ws://0.0.0.0:8081
```

成功的命令响应不会刷屏，只有失败的 `statusCode` 才会打印。
Bedrock 会把 tellraw 回显成 `sender=外部` 的 PlayerMessage；示例会过滤这些回显，
避免把服务器自己的消息当成玩家聊天。

然后在 Minecraft Bedrock 世界中执行：

```text
/wsserver <运行 Python 服务端的机器 IP>:8080
```

连接成功后，在游戏聊天框发送任意消息，例如：

```text
你好
```

服务端会在游戏内回复：

```text
收到 Steve 的消息：你好
```

其中 `Steve` 会替换为真实的玩家名。

## 网络注意事项

- 如果 Python 服务端运行在另一台机器上，使用该机器在局域网中的 IP，不要使用
  `127.0.0.1`。
- 确认防火墙允许 TCP `8080` 入站连接。
- Minecraft 与服务端之间通常不需要额外的路径；直接使用 `/wsserver IP:PORT`。
- 示例没有实现认证，建议只在可信的本机或局域网环境中使用。生产环境应在宿主的
  `ConnectionHook` 中增加认证和权限检查。

## SDK 结构

- `ExampleHook`：处理连接、玩家消息和命令响应。
- `MinecraftSink`：将 SDK 的 `OutboundText` / `SystemNotification` 转换为 MCBE
  `commandRequest`。
- `McbeServerFacade`：负责 WebSocket 监听、握手、`PlayerMessage` 订阅、解析和连接
  生命周期。

示例使用 `player_event.sender` 作为目标玩家。一个 `/wsserver` 连接可能承载多个玩家，
不要把连接级的 `ConnectionState.player_name` 当作玩家身份。
