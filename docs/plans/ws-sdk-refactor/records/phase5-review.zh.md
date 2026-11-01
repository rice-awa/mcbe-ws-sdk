# R17/R18 第五阶段独立审查

日期：2026-10-31。固定基线与当前 HEAD：`206361cd0af944def4cc562fea28ae464f933ef7`。
审查对象是基线到当前共享工作区的修改及新增文件，不仅是已跟踪 diff。开始和报告前均刷新了差异；未修改仓库文件、未提交、未派子代理。

## 结论

**本轮审查未发现阻塞合入的实际运行缺陷。** 规范轴无新增硬性违规；规格轴曾有一项非阻塞 CI 覆盖建议，原作者已修复并经本次独立复核关闭。R18 整项仍不能标记完成：实机未执行，最终自动化汇总与候选包快照一致性仍由协调者验收；本报告不是发布许可或实机通过证明。

## 规范轴

- 新 `McbeServer` 是对既有 facade 的小型入口组合，强制 reliable 并保留其余冻结设置；注入桥接与公开 `v2` 属性一致。没有复制传输、等待表或分片实现，没有新增不必要的防御层。
- 根导出只新增名称，保留旧快照名称；旧 facade 的本轮变化只有说明文字，旧运行路径没有被新入口替换。
- 新公共源码说明为英文；新增迁移及扩展指南、示例说明有中英文版本。中文 API 页保留跳转 stub，没有重复 mkdocstrings 树。

规范轴发现数：0。

## 规格轴：已修复建议

### S1（已关闭）：PR CI 没有执行新增示例的真实跨语言回归

位置：`.github/workflows/ci.yml:44` 的 cross-language 显式 pytest 列表（至第 50 行），以及 `tests/unit/test_example_behavior.py:113`、`:140`。

在干净 GitHub runner 上运行当前 PR CI 时，python job 只安装 Python dev extras，没有执行 `npm ci`；这两个测试先调用 `typescript([])`，发现未安装 TypeScript 后按辅助函数合同 skip。安装了 npm 依赖且设置 `MCBE_REQUIRE_CROSS_LANGUAGE=1` 的 cross-language job 又没有列入 `test_example_behavior.py`，addon job 只运行其自己的测试。因此文档安装位置的 TS 类型检查，以及真实示例模块/router 的元数据、流结束、提前取消、取消后继续调用回归，不会在普通 PR CI 中执行。

建议将 `tests/unit/test_example_behavior.py` 加入 cross-language job 的文件列表，并更新 `test_ci_cross_language_gate_requires_tools_and_regressions` 的结构断言。

该建议已通过 Orca 消息 `msg_eafd26e417df` 发给协调者，原作者随后把 `test_example_behavior.py` 加入 `.github/workflows/ci.yml:50`，并在 `tests/release/test_workflows.py:71` 增加必须包含它的断言；最终刷新已确认修复，结构测试再次通过。原列为非阻塞建议的原因：本次独立已实跑上述两项且通过；release verify 已先安装 npm 再全量测试并要求跨语言工具，因此发布流程仍会执行它们。

规格轴遗留阻塞发现数：0；遗留非阻塞建议数：0；已修复建议数：1。

## 核查内容与证据

- 已读 AGENTS.md、现有重构方案、R17/R18 条件、第四阶段最终验收。
- 核对新旧入口实际默认值、设置不变性、根导出、原生命令返回类型与状态码、协商 v1 包装、远端 v2 异常、必要元数据/流拒绝降级及取消清理路径。示例不再自行维护请求任务或等待表，TS 模块通过公开注册入口组装。
- 核对英中迁移表与扩展指南。PyPI 0.2.1 与未发布 checkout 已明确区分；Beta APIs、宿主认证、同步回调限制、同玩家 FIFO、慢消费者上限、取消不能撤回副作用均明确。最终刷新中中文流式锚点已修为 `request-streams`，不把此前中间态算作问题。
- 核对 CI/release：v2 资产检查、Node 安装先于 release 全量、隔离 venv 与仓库外 probe、发布使用验证产物。收尾新增的 websockets job 入口测试及其结构断言已复核通过。
- 核对 check_dist 的双协议资产/流种类校验及 installed probe 的 v1 字节安全、v2 编解码/元数据/大项/结束/取消路径。读取了自动化 worker 的 wheel、sdist 重建与安装后探针记录，但没有冒充自己重新跑过干净分发包矩阵。

## 独立复跑

| 命令范围 | 结果 |
| --- | --- |
| `pytest -p no:cacheprovider -q tests/unit/test_server_entry.py tests/unit/test_example_behavior.py tests/release/test_installed_contract.py tests/unit/test_public_api.py` | 12 passed，23.92s；包含真实 Node router + 示例模块 |
| `pytest -p no:cacheprovider -q tests/unit/test_v2_negotiation.py tests/unit/test_r00_legacy_read_loop.py tests/release/test_workflows.py` | 19 passed，7.96s |
| `pytest -p no:cacheprovider -q tests/release/test_distribution.py -k broken_v2_authority` | 10 passed，15 deselected，0.31s |
| 收尾刷新后（含 CI 示例漏测修复）`pytest -p no:cacheprovider -q tests/release/test_workflows.py` | 5 passed，0.44s |
| `git diff --check 206361cd` | 通过 |

一次初始命令引用了不存在的 `test_legacy_callback_contract.py`，pytest 未执行；已改为实际存在的 `test_r00_legacy_read_loop.py` 并取得上表结果。不重复全量或版本矩阵。

## 性能与审查边界

读取 `R18-performance` 的同环境 baseline/candidate JSON、方法参数及 cProfile。入站吞吐中位数约 9493→3399/s，出站约 29431→11768/s，确有退化，不能称为提速；候选 profile 显示 2000 条入站对应 4000 次 JSON loads，且新增 trace_scope/上下文开销，与协调者定位一致。分片字节、包数与峰值内存没有出现未解释的恶化；出站原始微基准波动也较大。依任务约定，保留追踪合同、记录代价并等待实机评估本身不是本轮阻塞，不要求为审查临时删追踪或改线格式。

未执行真实 Bedrock、Beta APIs 世界加载、四类新旧组合、真实网络性能或发布操作；没有把自动化 Node 替身当成游戏验证。未重复全量、覆盖率、完整 Python/websockets 矩阵、整站文档构建与干净包构建，这些由自动化 worker/协调者汇总。最终 R17/R18 验收记录仍在其他 worker 收尾范围，本报告不将暂缺汇总文件当作实现缺陷，也不预先签署其最终结论。
