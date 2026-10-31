# 第三阶段验收：R12—R13

2026-10-31；恢复基线 `0d9b3a6`，最终代码 `3506570`。**R12、R13 已完成；按用户要求暂停，R14 未开始。** 无推送或发布。R09—R11 历史证据见[中期验收](phase3-midpoint.zh.md)。

## 审查闭环

实现与修复采用 Codex `gpt-6.1-terra max`，独立审查采用 `gpt-6.1-sol high`。问题交给新的修复代理；最后一项仅做最小关闭清理。

| 范围 | 结果 |
| --- | --- |
| R12 `79db1de` | 初审五项 P2，`3008fd8`、`cd0ef6f` 修复，并补调度器取消检查；独立复核 52 Python、63 TS 全通过。 |
| R13 `caf3e43` | 初审一项 P1、四项 P2，`d895c03` 修复；最终复核确认五项闭环。 |
| 首次调度前断连 P2 | `3506570` 同步释放发现记录、请求和挑战；修复方 21 项定向测试、独立 high 6 项回归通过，最终 PASS。 |

条件与测试映射见 [R12](R12.zh.md)、[R13](R13.zh.md)。真实 Node/Python 用例覆盖多连接、双向大消息、元数据、四种兼容组合及重连期间的传输隔离。

## 整体与补丁验证

环境：Linux，Python 3.14.4，Node 22.22.1。整体检查在 `d895c03` 上执行；之后仅增加关闭清理补丁及一项回归，使用定向验证，不把旧整体结果写成最终提交的全量重跑。

| 命令／范围 | 结果 |
| --- | --- |
| `MCBE_REQUIRE_CROSS_LANGUAGE=1 .venv/bin/python -m pytest -p no:cacheprovider --cov=mcbe_ws_sdk --cov-report=term-missing -q` | 476 passed，无 skip，9 条既有警告，317.75s；覆盖率 91.18%（4356 statements / 384 missed）。 |
| addon `npm test -- --silent` | 185 passed，16 files。 |
| addon typecheck、lint、build:production | 全部通过。 |
| Ruff 全目录、严格 mypy | 通过；mypy 53 source files。 |
| 新旧资产生成器 `--check`、协议名、MCBEWS/1 目录与样本对比 | 通过，旧资产未改。 |
| `mkdocs build --strict`、`git diff --check` | 通过。 |
| `3506570` 最小补丁 | 21 项定向测试通过；独立 high 再跑 6 项关闭／租约／隔离回归，0.67s 全通过；Ruff、mypy、diff 通过。 |

原始日志在 `/tmp/mcbe-phase3/final-d895c03-python.log`、`final-d895c03-gates.log`；独立结论在 `r12-fixed-rereview-high.md`、`r13-final-rereview-high.md`、`r13-fast-close-rereview-high.md`。主要结论保存在本记录，状态以[任务表](../tasks.zh.md)为准。

## 边界与暂停

已交给底层传输的帧无法撤回；取消不撤销已执行操作。协商令牌不是认证凭据。无可验证断连来源时，附加包通过显式关闭及有界租约释放资源。

实机（包括世界启用 Beta APIs）、完整 Python／websockets／游戏矩阵、性能对比及正式分发／安装后合同仍属 R18，本轮不宣称完成。用户同期的 `AGENTS.md`、`CLAUDE.md` 文案提交 `e972366` 已保留。后续须等新指令，从 R14 开始。
