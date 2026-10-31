# R18 自动化验收证据

执行日期：2026-10-31。包版本保持 `0.2.1`；协议 `MCBEWS/1` 与 `MCBEWS/2` 独立于包版本。
本目录只记录本机自动化，不代表 GitHub Actions 已在线运行，也不代表 Minecraft 实机验收通过。
原始 `.log` 文件保留在本机并由 Git 忽略，不随提交分发；仓库保留命令脚本、结果摘要、环境版本及快照散列。下文日志文件名用于本机核查，不表示仓库包含原始日志。

## 改动范围

- `ci.yml` 增加 v2 生成资产检查，以及 runtime、negotiation、streams、实际示例的必跑跨语言测试。websockets 门槛加入 `test_server_entry.py`。
- `release.yml` 在 Python 测试前安装 Node 依赖，强制跨语言、覆盖率和新旧生成资产门槛；验证构建包后，在仓库外的新 venv 执行统一安装合同探针。未执行推送、标签或发布。
- `check_dist.py` 同时检查 wheel/sdist 中 v1 和 v2 authority 内容，包含 v2 流消息定义及向量。保持 wheel 1 MiB、sdist 5 MiB、文件数小于 250 的原限制。
- `probe_installed_contract.py` 验证旧入口默认串行、新入口 reliable、公共 v2 导出、资源可读、新旧编解码、请求往返、分片流首段、正常结束与主动取消。

## 快照与环境

R17 代码工作者发出源码冻结通知后，创建 `/tmp/r18-candidate`。四版全量各用 `/tmp/r18-matrix/py311` 等独立源码副本，避免分发测试的缓存目录相互覆盖；覆盖率各用独立 `COVERAGE_FILE`。
`addon/node_modules` 继承工作区已有安装，Node.js `v22.22.1`、npm `9.2.0`。始终设置 `MCBE_REQUIRE_CROSS_LANGUAGE=1`，不接受缺少 Node/TypeScript 的跳过结果。

文档冻结后，另建 `/tmp/r18-final-candidate`。与全量快照的差异仅为 `README.zh.md`、加入实际示例的 CI 必跑列表及对应工作流结构断言，见 `snapshot-delta.json`；SDK、示例、TypeScript 实现均相同。未重复四版全量。

Python 为 3.11.15、3.12.13、3.13.14、3.14.4。四环境依赖使用同一份 `dependencies-pinned.txt`，完整安装版本见 `environment-py*.log`。
初次 Python 3.11 环境信息命令包含仅新版本允许的 f-string 引号嵌套，报语法错误；已用兼容语法重取，正确记录为 `environment-py311-corrected.log`，原始错误保留。

主要依赖：pydantic 2.14.2、structlog 26.1.0、websockets 16.1.1、pytest 9.2.0、pytest-asyncio 1.4.1、pytest-cov 7.1.0、ruff 0.17.3、mypy 2.4.1、build 1.6.1、twine 7.0.0；隔离构建解析 hatchling 1.33.0。没有修改项目依赖声明或包版本。

## 命令与原始记录

- `commands.log` / `results.json`：初始候选、矩阵、每项退出码与耗时。
- `commands-final.log` / `final-results.json`：文档冻结后的最终构建、重建、安装与合同探针。
- `snapshot-sha256.json` / `final-snapshot-sha256.json`：受检代码、示例、工具、协议与包描述文件的散列。
- `artifacts.json` / `final-artifacts.json`：初始和最终候选的 SHA-256、字节数与最终 sdist 文件数。
- `r18-run-automation.py` / `r18-final-build.py`：本轮使用的本机执行脚本，便于核对；不是新增公共工具。
- `r18-addon-*.log`：协调者在同一 TypeScript 实现上运行并提供的附加包原始日志；本工作者未重复执行。

## 自动化结果

初始候选通过 v1/v2 生成检查、协议名称、ruff、mypy（55 模块）。最终候选构建、twine 7、check_dist、sdist 重建和两个全新仓库外 venv 安装合同均退出 0；`PYTHONPATH` 为空，导入路径落在各 venv 的 site-packages。原 wheel 与 sdist 重建 wheel 的 SHA-256 相同。

| 最终候选 | 字节 | SHA-256 |
| --- | ---: | --- |
| wheel / sdist 重建 wheel | 121665 | `7358a90639e0e4a164bd48def10a921f9f0463913f596ba7f88f548498a376bd` |
| sdist，190 文件 | 3901660 | `921903724d82854ab9c6861b0d991eaf353612150671613199b10d143e250960` |

完整 Python 测试每版仅运行一次，强制跨语言，无跳过。四版并行初跑含失败，不能写成“初跑全部通过”。

| Python | 初跑结果 | 初跑覆盖率 | 原失败节点单独顺序重跑 |
| --- | --- | ---: | --- |
| 3.11.15 | 533 passed / 4 failed，562.23s | 91.13% | 4 passed，10.93s |
| 3.12.13 | 533 passed / 4 failed，550.32s | 91.13% | 4 passed，13.45s |
| 3.13.14 | 533 passed / 4 failed，549.02s | 91.24% | 4 passed，10.73s |
| 3.14.4 | 535 passed / 2 failed，545.09s | 91.20% | 2 passed，8.23s |

原始失败堆栈见 `pytest-py*.log`；逐节点重跑命令和结果见 `commands-retry.log`、`retry-results.json`、`retry-failed-py*.log`。所有失败节点在未改源码、测试和时限的情况下顺序重跑通过，支持并发负载触发短期限失败的判断。

四版均在示例 Node router 和 R13 多 facade Node router 的启动 `ready` 行超过 5 秒。3.11 另有 R12 的 50ms 请求先到期，导致原测试断言 pending 为 1 时已为 0；3.11/3.12 的 v2 runtime、3.12/3.13 的真实协商 Node 启动也超时；3.13 另有新 peer 协商的短期限失败。
初跑失败前尚未进入清理段的四个 Node 子进程已按 `/proc/<pid>/cwd` 严格限定为本轮 `/tmp/r18-matrix` 进程后终止，原 pid/cwd/命令见 `orphan-node-cleanup.json`。3.12 初跑另有两条子进程析构时 event loop 已关闭的 warning，保留原日志。未改变生产期限、默认设置或源码。

协调者要求追加覆盖率后，复制原始 coverage 到各自 `.coverage-appended-py*` 并再次单独执行失败节点，见 `commands-retry-cov.log`、`retry-cov-results.json` 与 `retry-cov-py*.log`；原始完整覆盖率文件未覆盖。

| Python | 追加覆盖率复跑 | 合并覆盖率 |
| --- | --- | ---: |
| 3.11.15 | 4 passed，13.93s | 91.30% |
| 3.12.13 | 4 passed，15.69s | 91.33% |
| 3.13.14 | 4 passed，12.02s | 91.41% |
| 3.14.4 | 2 passed，9.43s | 91.35% |

websockets 专项与 CI 使用相同三个文件（facade、delivery、新公共入口），Python 3.11.15：12.0 / 14.2 / 16.1.1 每组均 33 passed。没有扩大成 Python×websockets 的 12 组合。

附加包由协调者验证：17 文件 205 tests、lint、typecheck、production build 通过，原日志已复制。文档 strict 最终通过由 `r17-mkdocs-final.log` 给出。新增门槛最终结构测试 5 passed，见 `workflows-final.log`。

本机自动化分批通过：所有计划环境均已覆盖，四版各 537 个不同节点都有通过证据；没有单次全绿的完整测试运行。保留初跑负载敏感失败与顺序复跑证据，不宣称 CI 线上运行通过。

## 未覆盖

未运行真实 Minecraft，未覆盖双方升级、单侧升级、无附加包的实机组合，也未测多人、大消息、流式、取消、重连、回声、慢处理器的游戏内行为与性能。
没有变更 R17/R18 主记录或任务状态，没有执行 R19。实机记录与性能比较由协调者在各自记录中汇总；R18 整项仍须保持待验收。
