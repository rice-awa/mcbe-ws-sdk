# 第三阶段中期验收：R09—R11

2026-10-31；开始 c1d457c，最终代码基线 6f647a8。R09—R11 通过；按用户要求暂停，R12/R13 未开始。没有 push 或发布。

## Review 与修复

实现与修复代理使用 Codex gpt-6.1-sol medium，独立 reviewer 统一 high。

| 范围 | 结果与提交 |
| --- | --- |
| R09 合同与样本 | 6e8c89b；独立 MCBEWS/2、23 样本、实际格式成本比较 |
| R10 双端编解码 | c314f2e；真实 Python/TypeScript 双向互解 |
| R11 双向分片 | 826618b；分层容量、TTL、墓碑及发送调度 |
| CI 两项 P1 | 8012c3e：活动解释器、可选本地工具与强制跨语言 CI；high 复核通过 |
| 协议五项边界 | bbf27c8：统一采样、坏续片释放、降额限额、巨大整数分类、准确 TTL；坏续片遗漏由新代理 6f647a8 补齐并 high 复核通过 |
| 合并检查遗漏 | cbef800：发布 gate 测试名单同步，显式 CI 入口加入边界回归；high 复核通过 |

所有确定缺陷已闭环。最后复核额外验证 12 场景、144 操作双端一致，坏帧不影响其他连接、来源或传输；9 个 CI 结构反例均拒绝，强制缺 Node/TypeScript 明确失败。

## 主代理最终验证

Python 3.14.4，Node 22.22.1；下列命令均在最终代码基线上执行。

| 命令 | 结果 |
| --- | --- |
| MCBE_REQUIRE_CROSS_LANGUAGE=1 python -m pytest -p no:cacheprovider --cov=mcbe_ws_sdk --cov-report=term-missing -q | 431 passed，无 skip，9 条既有警告，194.93s；覆盖率 91.49%（3901 statements / 332 missed） |
| addon：npm test -- --silent | 164 passed / 12 files，12.50s |
| addon：npm run typecheck、npm run lint、npm run build:production | 全部通过 |
| ruff check --no-cache src tests examples；mypy --no-incremental src | 通过；mypy 52 source files |
| generate_protocol_assets.py --check；generate_v2_assets.py --check；check_protocol_names.py | 全部通过 |
| git diff --check；旧协议四项资产 SHA-256 与基线目录对比 | 通过，MCBEWS/1 资产未改 |
| mkdocs build --strict | 18.44s 通过；随后仅修改验收记录和任务状态 |

原始日志在 /tmp/mcbe-phase3/：midpoint-accepted-python.log、midpoint-accepted-addon.log、midpoint-final-checks.log。review 报告为 midpoint-spec-high.md、midpoint-standards-high.md、midpoint-ci-rereview-high.md、midpoint-spec-rereview-high.md、midpoint-final-rereview-high.md；关键结论保存在本记录，临时日志不作唯一状态来源。

## 边界与暂停点

新协议目前是显式编解码和传输模块；双向请求、通知、元数据上下文接入属于 R12，版本协商与兼容回退属于 R13，尚未开始。第三阶段整体尚未完成。

完整 Python/websockets 矩阵、MCBE 实机（含 Beta APIs）、性能比较及正式分发/安装后合同仍属 R18，本轮不宣称通过。用户另有 AGENTS.md/CLAUDE.md 未提交修改，未纳入本轮提交。恢复工作须等用户指令，再从 R12 开始。
