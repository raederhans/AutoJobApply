# P1 本机整合、统计修复与验收

核验日期：2026-10-12。基线为 [PR #21](https://github.com/raederhans/AutoJobApply/pull/21) 的 `6f2a116c36846dd8a8d17e1ec7d427089011ba63`，仍为 draft，较 `main@64d2ba6a76457abe8648e8b5cf34665d206be6a3` ahead 7。本记录不代表合并、发布、用户安装更新或真实投递。

## 已交付能力与边界

| 能力 | 基线中可核实的实现 | 尚不能据此声称的结果 |
| --- | --- | --- |
| A/B：输入依赖与有限子目标 | 共用浏览器 adapter 的 pre-input、readback、Host final-refresh 依赖检查；已观测记录内 school selection / record fields | 任意 ATS、iframe、closed shadow、虚拟列表、上传或完整申请均已覆盖 |
| C/D：恢复与一致性 | 本次新增 opt-in Host review → SQLite 单次许可 → 原始 Host completion → 既有 C/D → transport ack；复用 Supervisor、Episode barrier 与材料约束，受控 Chromium 场景已通过 | 实际 Codex Browser runtime、用户安装入口、Host 身份迁移、上传及最终提交仍待验证 |
| E：交互评测 | 隔离 Chromium、禁网虚构材料、生产 browserAdapter、独立终态 DOM oracle；三组使用相同底层工具和必要保护 | reference_driver 的通过率、速度等价于真实模型收益；已消融完整 C/D、上传、导航或投递流程 |
| P1-A：程序性建议 | 专用有界 SQLite store、独立验证与去重、冻结读取；同 session/epoch 的 prepare MCP 回复可选择性增加建议 | 默认开启、从 worker 自述学习、自动生产成果晋级、建议拥有输入或投递权限 |
| 职业工作台 | PR #19 与补充 PR #20 已合入 main；旧交付勾选已纠正 | 用户本地安装或旧 source 工作区已同步；历史 Edge 消息已核实送达 |

PR21 原始 [CI 38145882951](https://github.com/raederhans/AutoJobApply/actions/runs/38145882951) 五项检查仍为 success。PR 记录的 core 4083 passed / 1 skipped、browser 73 passed 属于原始头及其 merge preview 的历史验证；不能作为本次新增提交的 CI，更不能与本机目标测试数量相加。

## 人工负担统计修复

复现：`holdout-duplicate` 或 `holdout-no-commit` 返回 `finish(blocked)`，独立 oracle 给出 `expected_handoff=blocked`、`complete=false`。原实现正确判为 `correct_handoff=true`，但遗漏请求次数并输出等待 0。本缺口低估人工负担，未将该任务计入自主成功。

新报告为 `interactive-preparation-evaluation/v2`，不更改自主完成、移交正确性或提交权限的判定。

| 证据 | 请求次数 / 状态 | 等待毫秒 / 状态 |
| --- | --- | --- |
| 本次没有人工请求；或 oracle 明确表明 blocked 不是人工移交 | `0 / not_requested`（请求未发生） | `0 / not_requested` |
| `needs_fact`，或 blocked 的独立 task oracle 表明需要人工处理 | 已知次数 / `observed` | 无回调、超出人工回调预算或 blocked 交回用户：`null / not_observed`（未观测） |
| 测试注入的人类回调完整返回 | 已知次数 / `observed` | 回调墙钟累计 / `measured` |
| blocked 缺少 oracle、缺少分类字段或分类无法识别 | `null / unknown` | `null / unknown`（未知） |
| 人类回调抛异常 | 请求已发生，保留次数 / `observed` | `null / unknown`，不把失败前的局部时间冒充完整等待 |

`blocked` 不是通用的“必须人工”标签：这里只用评测器的独立任务证据分类。正确性和负担分开；有错误写入的移交仍可产生人工请求。事实回调不能解决重复选项或批准一般 blocked 恢复，因此不会为这些情况新增自动回调或重试。

`summarize()` 保留全部 trial，报告总请求数、未知请求 trial 数和四种等待状态各自分母。任何未观测/未知等待都使总等待保持 `null`；已测一部分后出现未测请求也不输出部分和作为总量。没有从 elapsed、browser 时间或停顿猜测人工等待。回调计时属于 trial 的 inclusive wall span，不能再加到 elapsed。汇总按 schema 分组，配对对比拒绝 v1/v2 混用；旧 v1 结果保留原始记录，不事后捏造等待时间。

## 本机验证

- 修改产品代码前，新增的目标回归有 7 个失败，包括两项明确的 `0 != 1` blocked 复现、回调异常输出 0、未知分类被写为 0。
- 修改后非浏览器目标套件：`python -m pytest -q tests/test_harness_evaluation.py tests/test_procedure_experience.py -m "not browser" --basetemp <new-task-local-directory>`：初次 74 passed、1 deselected。后续审查增加两项畸形 oracle 分类回归：原集合判断会抛 `TypeError` 丢失 trial，修复后完整目标套件为 76 passed、1 deselected；无法识别的列表/对象分类也保持未知。
- `ruff check src tests/test_harness_evaluation.py` 与 `git diff --check` 通过。使用既有 Python 3.12.10、pytest 9.1.1 和 Ruff 0.16.4，没有安装或更新依赖。
- Chromium 目标回归：`python -m pytest -q tests/test_harness_evaluation.py -m browser --basetemp <new-task-local-directory>`：1 passed、36 deselected，76.19 秒。该测试含 6 次训练、7 个不同 DOM holdout 和 1 次 false-ready 负控制；其中 duplicate/no-commit/missing 均验证请求 1、等待 `null / not_observed`。这些是 reference_driver 框架回归，不是模型效果证据。最终三项整合仍另行验证。

所有运行绑定显式隔离 `APPLYPILOT_DIR`，临时文件使用本任务新建目录。默认 pytest 临时目录曾因沙箱权限导致一个 setup error；改用任务可写目录后通过。系统 Playwright 要求未安装的 Chromium revision 1223，前两次 browser 运行未产出终态 oracle；设置 `APPLYPILOT_TEST_PYTHON` 指向既有虚拟环境的 Playwright 1.62.0，匹配已有 Chromium revision 1234 后，在工具允许的进程权限下通过。没有下载或更新浏览器。未使用用户桌面浏览器、个人材料、密钥或付费模型。

## 三项提交的整合验收清单

整合顺序：本统计/文档提交 → P1-B0 Host 恢复提交 → P1-C0 测量提交。第四个工作树同样从 `6f2a116` 创建，只作本地 cherry-pick。P1-B0 不改 evaluator；P1-C0 与本修复共用 `harness_evaluation.py`，应保留 v2 的全部 `user_intervention_*` 字段、独立 measurement 包装与 source manifest 身份。

- [x] 三路来源提交均可定位，工作树归属清楚；组合 diff 没有吸收原 source 的未归属 WIP。
- [x] combined evaluator：blocked/needs_fact、非人工终止、缺 oracle、回调失败、部分观测、旧 schema 隔离全部通过；真实 DOM duplicate/no-commit/missing 均是请求 1、等待 `null`。
- [x] P1-B0 主验收使用实际 Host 回复、真实 SQLite 和受控浏览器；部分成功、缺事实、parser overwrite、丢回复、停止 Host、进程重启、submit latch 各有证据。未知效果不得授予重试许可。
- [x] P1-B0 的下一次输入仍需显式 review；同 job/attempt/session/tab/epoch 与材料约束不丢失，先持久化完成/不可逆事实再确认 transport。两个独立 SQLite 连接的回归验证延迟发布不能复活已撤销许可。
- [x] P1-C0 区分 provider 客户端墙钟与服务端计算时间；encoding、decision、browser 的嵌套/并行关系明确；coordination、slot、load、recovery 缺真实边界就未知，不拿 residual 冒充等待。
- [x] token/cache/reasoning/cost 来源缺失时保持未知；配对冷暖计划、价格来源和预算有模板，未启用 `--run-model`，未调用真实模型。
- [x] 组合后的目标 Python、Node/浏览器测试、Ruff、diff 检查通过；sdist/wheel 构建与新增文件内容检查通过。安装运行及实际 Codex Browser 入口仍未验证。
- [x] 原始 33 个 checkout 的 HEAD/状态清单与隔离前一致；本地提交、diff、测试记录和仍未验证项保留在任务交付目录，供父任务审阅。

## 第四工作树联合验收（2026-10-12）

目录：`task-3/AutoJobApply-combined`；分支：`codex/pr21-combined-20261012`。创建前再次读取远端引用，PR21 仍为 `6f2a116c36846dd8a8d17e1ec7d427089011ba63`，main 仍为 `64d2ba6a76457abe8648e8b5cf34665d206be6a3`。

| 来源 | 原分支本地提交 | 整合工作树对应提交 |
| --- | --- | --- |
| 人工统计及文档 | `ede5651a1b4c001fe4c1b90b64d36835a51b88d7`、`e7e43080d40476433cd8c68c85c1e90ea15146ab` | `aa7a397`、`a463c65` |
| Host 恢复 | `7a730f5a4728dd3e7048be6a5af225c90cfc4f88` | `2c11a46` |
| 测量与离线计划 | `7c23677ae649537b8742bb28d88b5563da0ba879` | `ac7bf14` |

唯一 cherry-pick 冲突是 evaluator 返回行；采用 v2 的 `result`，保留人工统计全部字段及 C0 measurement、encoding finally、usage/cost helper、导出和源码身份。新增联合回归验证同一 blocked trial 同时保留请求 1、等待未观测与独立 measurement。审查另复现未知 usage 类别仍估价的问题：root/input/output 三种新增类别均错误返回 0.00036；修复后金额未知，已知 token 总量继续保留。新 Host journal 作为已加载依赖纳入 E 的 source manifest。

| 联合检查 | 结果及范围 |
| --- | --- |
| Python 目标套件 | 296 passed、10 deselected，19.12 秒；E/procedure/measurement/performance 与 attended C/D/Host contract/visual bridge，共 12 个文件，未重跑全量核心套件 |
| Chromium | 10 passed、38 deselected，117.89 秒；E 的 6 train + 7 holdout + 1 false-ready reference trial，以及 B0 的 9 个受控恢复场景 |
| Node Host | 48 passed |
| 末次源码身份回归 | 补 Host journal 指纹后，相关 5 个身份/费用/整合检查 passed，0.36 秒；只重跑受影响检查并重新构建发行包 |
| Ruff / diff | 所有 src 与四个变更 Python 测试文件通过；diff 无空白错误 |
| 发行包 | 既有环境 `build --no-isolation` 生成 sdist 与 wheel；检查三个新增 Python 模块、v2 schema 与源码包内 Host journal |
| 原工作区保护 | 全部 33 个原 checkout 的 HEAD/完整状态清单一致；原 source 保持 `48bec07612aa8fe9269eabf31d9feece725359eb` 及 1759 条 porcelain 项 |

联合 Python/浏览器使用既有 Python 3.11.14 与 Playwright 1.62.0；启动先 `sys.path.insert(0, combined/src)`，断言 E、measurement/planner 和 attended owner/adapter 的 `__file__` 属于本树，再执行 pytest。子进程重启测试也保留源路径断言。首次系统 Python 因缺 PyYAML 使 Host setup 失败；普通沙箱阻止 Node 原子改名及构建解压。使用完整既有环境、任务内新 TEMP/TMP 与获准的执行上下文后通过，未安装依赖或削弱产品检查。这些失败日志与成功日志都保留。

离线 planner 和 E preflight 在最终源码上重新生成，状态 `not_run`、无模型调用、金额未知；预算仍是 14 training + 42 evaluation、最多 448 decisions，模板不授予实际费用权限。源码指纹与当前工作树逐项比对。审阅补丁、完整 diff、日志、预检与发行包保存在 `task-3/delivery`；最终提交身份由该目录的交付清单记录。

真实模型实验、授权真实页面的非提交试点、生产建议晋级、安装/发行验证与远端 review/merge/release 仍按 [后续批次](p1-revised-next-steps.md) 单独判断，不从本机测试推导为完成。
