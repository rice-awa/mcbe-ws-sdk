# 内存能力往返示例

本示例保留兼容的 v1 公共接口，在内存中完成一次能力调用。
不需要游戏或附加包，也不演示协商 v2 或流式。
尚未发布的新入口见[附加包示例](../addon-server/README.zh.md)。

在仓库根目录执行：

```bash
pip install -e ".[dev]"
python examples/addon-capability-call/capability_call.py
```

预期输出：`{"ok":true,"greeting":"Hello, Steve"}`。
