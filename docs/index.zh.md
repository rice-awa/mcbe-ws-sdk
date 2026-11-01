# mcbe-ws-sdk

面向 Minecraft 基岩版的通用 WebSocket 网关 SDK。`McbeServer` 负责传输、强制
MCBEWS/2 准入、请求关联、字节安全命令分片、能力调用与请求流。
模型服务、消息代理、认证和业务历史由宿主负责。

```text
Minecraft 客户端  ←── /wsserver IP:端口 ──→  Python 宿主
                                              ├── ConnectionHook / ResponseSink
                                              └── V2Bridge ←→ 配套附加包
```

## 快速体验

```bash
pip install -e ".[dev,docs]"                 # 在当前仓库安装
python examples/basic-server/server.py       # 命令示例，使用 tellraw 回复
```

在游戏中执行 `/wsserver <宿主IP>:8080`，然后发送 `!help` 或
`!wscmd time query daytime`。

示例只处理去掉首尾空白后以 ASCII `!` 开头的消息。
普通聊天、裸 `help` / `帮助`、句中 `!` 与全角 `！` 都会静默忽略，
不回复也不调用能力。裸 `!` 返回一次用法提示；未知 `!command` 返回一次提示。

附加包能力需要导入配套包，并在目标世界开启 **实验 → 测试版 API（Beta APIs）**。
未开启时脚本不会加载，准入会超时。

## 接下来

- [快速开始](getting-started.md)：安装、附加包、首次连接、命令与排查。
- [架构](architecture.md)：准入状态机、运行时与桥接分层。
- [扩展作者](extension-authors.md)：能力、通知、元数据、事件与流式。
- [协议（MCBEWS/2）](protocol-v2.md)：强制准入、载体、限制与流。
- [追踪与诊断](diagnostics.md)：生命周期日志、`--log-raw`、脱敏。
- [原生事件](native-events.md)：观察者、拦截器与订阅。
- [迁移指南](migration.md)：从双协议版本升级的破坏性变化。
- [API 参考](reference.md)：由公开源码生成。

协议自动化覆盖不等于实机支持已验收。依赖某个部署前，请核对游戏版本、平台与附加包加载情况。
