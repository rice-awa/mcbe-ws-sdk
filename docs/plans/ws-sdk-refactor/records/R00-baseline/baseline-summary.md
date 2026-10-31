# R00 全量基线证据（2026-10-31）

开始基线提交：`1d83192131fdc2d92b66735982c2b3cbfb876b24`，工作区干净。
执行环境：Linux 7.0.0-38-generic x86_64，Python 3.14.4，Node.js v22.22.1 / npm 9.2.0，
pydantic 2.13.4，websockets 16.1.1，structlog 26.1.0，ruff 0.15.22，mypy 2.3.0，
pytest 9.1.1，twine 6.2.0，hatchling 1.31.0。以下命令均在仓库根目录执行，
附加包命令在 `addon/` 执行；`addon/.env` 已存在，未覆盖。

## 验证命令与结果

| 检查 | 实际命令 | 退出码 | 结果 |
| --- | --- | --- | --- |
| Python 静态检查 | `ruff check --no-cache src tests examples` | 0 | All checks passed |
| 类型检查 | `mypy --no-incremental src` | 0 | Success: no issues found in 31 source files |
| 全量测试＋覆盖率 | `python -m pytest -p no:cacheprovider --cov=mcbe_ws_sdk --cov-report=term-missing -q` | 0 | 237 passed, 9 warnings；覆盖率 86.61%（门槛 85%） |
| 协议资产生成检查 | `python tools/generate_protocol_assets.py --check` | 0 | ok |
| 协议名称检查 | `python tools/check_protocol_names.py` | 0 | ok |
| 附加包测试 | `npm test` | 0 | 7 files / 104 tests passed |
| 附加包 lint | `npm run lint` | 0 | prettier 通过 |
| 附加包类型检查 | `npm run typecheck` | 0 | 通过 |
| 附加包生产构建 | `npm run build:production` | 0 | 3.67s 完成 |
| 文档严格构建 | `mkdocs build --strict -d /tmp/r00_site` | 0 | 12.33s；仅提示 `multiplayer-tellraw-origin-fix-plan.md` 未在 nav（既有信息） |
| 干净分发构建 | `python -m build --sdist --wheel --outdir /tmp/r00_dist` | 0 | `mcbe_ws_sdk-0.2.1` sdist + wheel |
| 分发包检查 | `python tools/check_dist.py /tmp/r00_dist` | 0 | wheel 与 sdist 均 ok |
| twine 检查（本机 twine 6.2.0） | `twine check /tmp/r00_dist/*` | 1 | 既有失败，见下 |
| twine 检查（隔离 twine 7.0.0） | `/tmp/r00_twine_venv/bin/twine check /tmp/r00_dist/*` | 0 | wheel 与 sdist 均 PASSED |

## 已有失败：twine 6.2.0 拒绝 Metadata-Version 2.5

复现（干净构建产物）：

```text
$ twine check /tmp/r00_dist/*
Checking /tmp/r00_dist/mcbe_ws_sdk-0.2.1-py3-none-any.whl: ERROR InvalidDistribution:
Invalid distribution metadata: '2.5' is not a valid metadata version
Checking /tmp/r00_dist/mcbe_ws_sdk-0.2.1.tar.gz: ERROR InvalidDistribution:
Invalid distribution metadata: '2.5' is not a valid metadata version
```

原因：`twine 6.2.0` 在 `twine/package.py` 顶部把
`packaging.metadata._VALID_METADATA_VERSIONS` 覆盖为仅到 2.4 的列表；
而 hatchling 1.31.0 生成 `Metadata-Version: 2.5`。同一产物用隔离安装的
twine 7.0.0 检查通过，说明不是包元数据本身损坏，而是本机 twine 版本的既有缺陷。
处理安排：本项不修改构建配置；后续阶段在验证环境升级 twine（≥7.0.0）后重跑
`twine check`，并在 R18 记录 CI 与实际版本。此失败不是本次改动引入，也没有被隐藏。

## 旧协议资产摘要

见 `protocol-asset-sha256.txt`。三个摘要与主代理独立基线一致。

## 公开接口基线

`tests/unit/test_public_api.py::test_public_api_snapshot`：1 passed。

本次新增回归节点：

- `tests/unit/test_r00_legacy_read_loop.py::test_legacy_read_loop_blocks_while_hook_awaits_addon_reply`：`xfail(strict=True)`，当前 1 xfailed。
- `tests/unit/test_r00_legacy_read_loop.py::test_legacy_read_loop_does_not_read_ahead_while_hook_waits`：1 passed，固定旧串行读取顺序。

加入新回归后全量结果：238 passed, 1 xfailed, 9 warnings；覆盖率 86.89%。

## 未覆盖矩阵（本机未执行）

| 组合 | 状态 | 说明 |
| --- | --- | --- |
| Python 3.11 / 3.12 / 3.13 | 未本机执行 | 本机仅 3.14.4；由 CI `python` 矩阵覆盖，不能写成“本机全部通过” |
| websockets 12 / 14 | 未本机执行 | 本机仅 16.1.1；由 CI `websockets` 矩阵覆盖 |
| MCBE 实机 / Beta APIs / 附加包加载 | 未执行 | R00 不含实机；留给 R18 |
| twine 7.0.0 以外的发行工具链 | 未执行 | 隔离 venv 仅验证 twine 7.0.0 |

## 环境备注

- 本地 `.venv` 的 editable 安装元数据仍是 `0.1.0`，因此
  `mcbe_ws_sdk.__version__` 在本机报告 `0.1.0`；干净构建的 wheel 为 `0.2.1`，
  CI contract 探针会按 wheel 断言。这是本地安装陈旧，不是仓库源代码问题。
- 基准测试记录于 `benchmark-baseline.json`。负载：每个场景 200 次/条，
  分片输入 4096 个 UTF-8 字节，命令行预算 461 字节，运行 5 次；
  内存峰值为同一负载额外运行一次的 tracemalloc 峰值。
