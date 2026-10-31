# 第四阶段验收：真正流式

开始基线 `182d1ed`，执行日期 2026-10-31。开始时工作区干净；改动见本记录所在的第四阶段提交。
范围为 R14—R16，R17—R19 未开始。

## 结果

- [R14](R14.zh.md)：双端持续生产／消费，不需要预先知道长度；逻辑项独立编号，复用 R11 分片；空流、大项、多流与正常结束可用。
- [R15](R15.zh.md)：取消、失败、超时、断连、慢消费者均明确结束；任务和缓冲有界；取消单流不影响其他流或普通请求。
- [R16](R16.zh.md)：三类负载各测七轮，独立 review 核对并复跑。保留有界超载终止策略，本轮不加入接收额度。

## 独立 review

实现由主代理与 TypeScript 工作者完成，Python 测试由独立工作者补充。
R14／R15 每轮实现完成后交给 reviewer；R16 另启 reviewer 核对测量与决策。

第一轮发现两项 Important 问题，均先以失败回归复现，再修复并通过独立复核：

1. Python 超载拒绝的流 ID 未保留墓碑，容量释放后重放可执行生产者。现在容量允许时留墓碑，且生产者＋墓碑仍不超限。
2. Python 流处理器嵌套调用丢失入站 trace。现在生产任务在对应 trace_scope 中创建，迭代及嵌套请求继承 trace。

收尾定向核查发现 open 正在发送时关闭 endpoint 没有取消发送任务，可能关闭后启动远端生产者。
已补失败回归并管理开流发送任务；关闭、超时、调用者取消和首次执行前关闭均通过测试。
最终独立 reviewer 复测四项通过，规格与质量均通过，无遗留发现。
墓碑表已满时不继续存储被拒绝的新编号，重复抑制受容量边界限制；双语文档明确这一点。

## 已运行检查

环境：Python 3.14.4、Node.js v22.22.1，使用已有本地依赖。

| 命令 | 结果 |
| --- | --- |
| `pytest -p no:cacheprovider --cov=mcbe_ws_sdk --cov-report=term-missing -q` | 514 passed, 9 warnings in 263.50s (0:04:23)；覆盖率 90.60%，超过 85% 门槛 |
| `pytest -p no:cacheprovider -q tests/unit/test_v2_streams.py` | 最终通用 Awaitable 协程包装后，27 passed；独立 reviewer 确认包装不改变生命周期 |
| `npm test` | 17 文件，205 passed |
| `ruff check --no-cache src tests examples` | 通过 |
| `mypy --no-incremental src` | 54 个模块通过 |
| `npm run typecheck`、`npm run lint`、`npm run build:production` | 均通过 |
| `python tools/generate_protocol_assets.py --check` | 通过 |
| `python tools/check_protocol_names.py` | 通过 |
| `python tools/generate_v2_assets.py --check` | 通过 |
| `mkdocs build --strict` | 通过 |
| `python tools/measure_streams.py` | 三类场景各七轮，原始数据见 R16 |
| `ruff check --no-cache tools/measure_streams.py`、`git diff --check` | 通过 |

首次全量发现两条 R09 资产断言仍只允许原子消息种类和样本数，已更新；
格式比较工具保留原七个原子样本的八位置数组实验，不把新增 item/seq 塞入不含这两个字段的数组。
定向两项与之后全量均通过。没有删除旧样本，没有降低覆盖率门槛。

## 限制与后续

真实 Node router 与 Python facade 的双向持续流已经自动化验证；测量使用合成内存链路，
不代表 Bedrock tick 或真实网络性能。实机、Python／websockets 全矩阵与安装包合同检查仍由 R18 负责。
根模块公共入口整理与迁移示例属 R17，当前入口和双语合同已经可读。

JavaScript 生产者须响应 context.cancellation，Python 生产者须允许任务取消；
无法撤销已交给传输层的帧，也不撤销已执行的世界修改。无需无限缓冲、自动重试或副作用重放。
本阶段没有新增依赖，没有改变 v1 资产、旧默认入口或 461 字节安全预算。

## 结论

通过。R14—R16 的实现、测量、独立 review、修复复核及本阶段本机验证均完成。
下一任务为 R17：公共入口、迁移示例与文档。实机及完整矩阵继续留 R18。
