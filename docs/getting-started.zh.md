# 快速开始

本指南把基岩版客户端连接到 Python 宿主，运行仓库自带示例，并说明最常见的首次故障。

## 环境要求

- Python 3.11+。
- 可使用 `/wsserver`、允许执行命令的基岩版世界。
- 使用附加包能力时：配套 TypeScript 附加包，依赖固定在
  [`addon/package.json`](https://github.com/rice-awa/mcbe-ws-sdk/blob/main/addon/package.json)：
  `@minecraft/server` 2.8.0、`@minecraft/server-ui` 2.1.0，以及与 1.26.30 基线匹配的
  `@minecraft/server-gametest` 测试版。
- 附加包清单的目标 `min_engine_version` 为 `[1, 26, 30]`。

SDK 要求每条连接完成 MCBEWS/2 准入。没有旧协议模式，也不会在准入前缓存业务。

## 安装

在仓库根目录执行：

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev,docs]"
```

如果已有构建好的 wheel，`pip install ./dist/mcbe_ws_sdk-*.whl` 即可安装。

## 构建并导入附加包

默认包会注册两个只读快照、`demo.echo`、`demo.ticks` 流和 `run_world_command`：

```bash
cd addon
npm install
npm run build
npm run mcaddon
```

精简包只注册两个只读快照：

```bash
npm run build:base
npm run mcaddon:base
```

两个变体写入同一个脚本路径 `dist/scripts/main.js`，但构建产物彼此独立：
默认包生成 `mc-ws-sdk.mcaddon`，精简包生成 `mc-ws-sdk-base.mcaddon`，均位于
`dist/packages/`，不会互相覆盖。`--base` 或 `MCBE_BASE=1` 选择 `main.ts`；
默认入口是 `main.demo.ts`。

### 在世界中启用

1. 将生成的 `.mcaddon` 导入 Minecraft。
2. 打开**目标世界**设置（新建世界，或对已有世界点**编辑**）。
3. 在 **实验**（Experiments）中开启 **测试版 API（Beta APIs）**。
4. 启用行为包（如有提示，也启用配套资源包）。
5. 进入世界。未开启 Beta APIs 时脚本不会加载，准入会超时。

开启实验性玩法可能使世界无法解锁部分成就，建议使用专用测试世界。

## 启动示例服务

两个示例都需要附加包完成准入。基础示例不调用附加包能力，只响应带前缀的命令，
并等待原生 `commandRequest` 回复：

```bash
python examples/basic-server/server.py --host 0.0.0.0 --port 8080
```

使用 `--log-level DEBUG` 查看生命周期诊断，按 `Ctrl+C` 停止。

然后在游戏中执行：

```text
/wsserver 127.0.0.1:8080
```

Python 与游戏在同一台机器时使用 `127.0.0.1`。
跨设备时改用运行 Python 的机器的局域网 IP，并在防火墙放行监听端口。
示例没有登录步骤，也没有鉴权。

连接只有在 MCBEWS/2 准入进入 `READY_V2` 后才算就绪。
`on_connected` 在该时刻触发；此前不会处理任何业务消息。

## 示例命令

两个示例使用同一条输入规则：去掉首尾空白后，消息必须以 ASCII `!` 开头。
其他输入一律静默——不回复、不执行命令、不调用能力或流。

| 输入 | 结果 |
| --- | --- |
| 空串或空白 | 静默 |
| `hello`、`a`、`stream 5` | 静默 |
| 裸 `help` 或 `帮助` | 静默（别名现在必须带前缀） |
| `hello !player`、全角 `！player` | 静默 |
| ` !player ` | trim 后正常处理 |
| 裸 `!` | 一次用法提示 |
| `!unknown` | 一次未知命令提示 |
| `!help`、`!帮助` | 帮助 |
| 其他 `!command` | 分发 |

过滤只影响示例的玩家命令分发。SDK 协议帧、协商、`MCBEWS2|` 载体、原生
`commandResponse` / `error` 与原生事件都不受该规则影响。

基础示例命令：

| 命令 | 行为 |
| --- | --- |
| `!help` / `!帮助` | 显示帮助 |
| `!wscmd <command>` | 执行原生命令并等待 `commandResponse` |

附加包示例命令：

| 命令 | 行为 |
| --- | --- |
| `!player [target]` / `!玩家` | `get_player_snapshot`，默认当前玩家 |
| `!inv [target]` / `!背包` | `get_inventory_snapshot` |
| `!wscmd <command>` / `!ws命令` | 原生命令，不需要附加包能力 |
| `!cmd <command>` / `!命令` | `run_world_command`，危险命令被拒绝 |
| `!echo <text>` | `demo.echo`，附带示例元数据 |
| `!stream [count]` | 消费 `demo.ticks` 直到正常结束（默认 5 项） |
| `!stream-cancel [count]` | 读取无限生产者的前若干项后取消（默认 3 项） |
| `!help` / `!帮助` | 显示帮助 |

`run_world_command` 随默认包发布。默认策略为仅禁止列表：命中危险命令禁止列表的入口
一律拒绝；需要更严格时可向 `handleRunWorldCommand` 传入显式允许列表。

## 从 Python 调用附加包能力

在准入后的受管理钩子中，使用服务的 `V2Bridge`：

```python
response = await server.v2.request(
    state.id,
    "get_player_snapshot",
    {"target": event.sender},
)
print(response["result"])
```

SDK 负责关联、超时、分片与重组。远端协议错误抛出带线错误码 `code` 的
`RemoteCallError`。原生命令还需检查 `statusCode`；收到响应不等于业务成功。

流式调用：

```python
stream = await server.v2.stream(state.id, "demo.ticks", {"count": 3})
async with stream:
    async for item in stream:
        print(item)
```

流要求协商出 `stream/1`。`demo.ticks` 生产者由默认包注册。
可能提前退出时使用 `async with` 或 `await stream.aclose()`；
取消正在等待的读取只取消该流，不影响其他请求。

## 玩家看到的错误

应用层失败应向玩家显示：

- 正在调用的方法或能力名；
- 稳定的错误码；
- 有界可读说明（最多 256 个 UTF-8 字节，不截断 Unicode 标量）；
- 行动提示，例如“请使用默认包”或“请检查参数”。

内部请求编号、追踪编号和堆栈只留在日志中。附加包会把未知能力、参数错误与处理器异常映射为
`UNSUPPORTED_METHOD`、`INVALID_PARAMS` 与 `HANDLER_FAILED`。

## 排查

| 现象 | 检查 |
| --- | --- |
| 连接立即以 `UNSUPPORTED_PROTOCOL` 关闭 | 附加包或客户端不是 MCBEWS/2。旧协议帧会被拒绝，没有回退。 |
| `NEGOTIATION_TIMEOUT` | 附加包缺失、未启用，或脚本未加载。确认行为包与 **测试版 API**。 |
| `NEGOTIATION_CONFIRMATION_TIMEOUT` | 附加包已应答挑战，但最终确认未完成。重连并查看世界/脚本日志。 |
| `NEGOTIATION_BUSY` | 临时准入或对端名额已满；等旧会话过期后重连。 |
| `INVALID_NEGOTIATION` | 控制帧格式错误或不匹配。确认附加包版本与本 SDK 一致。 |
| `SESSION_INVALID` | 已准入会话被撤销或过期。重连会获得新的会话代次。 |
| 能力调用超时 | 确认启用的包、目标世界、测试版 API 与 `/wsserver` 连接。 |
| `!cmd` 失败但 `!wscmd` 正常 | 命令在危险命令禁止列表内，或附加包是 `--base` 精简构建。 |
| 普通聊天没有回复 | 这是预期行为。示例只处理以 ASCII `!` 开头的消息。 |
| 文本分成多段 | SDK 使用 461 字节经验预算拆分 tellraw/scriptevent；原生命令保持原子，超限即拒绝。 |

准入失败会以 `1008` 关闭 WebSocket。关闭原因是有界标识（最多 123 个 UTF-8 字节）。
日志中的零字节 EOF 仍是未确认现象，不能当作正常行为。详见[追踪与诊断](diagnostics.md)。

## 关闭与回退

调用 `await server.stop()` 或取消 `run_lifetime()`；受管理任务、待完成调用与分片会被清理。
重连会重新准入并建立新会话。已经执行的世界修改不会撤销，也不会自动重放。

示例没有鉴权。桥接不是安全边界；宿主负责调用方认证、授权与应用状态。
