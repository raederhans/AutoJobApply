# P1 本机整合、统计修复与验收

核验日期：2026-10-12。基线为 [PR #21](https://github.com/raederhans/AutoJobApply/pull/21) 的 `6f2a116c36846dd8a8d17e1ec7d427089011ba63`，仍为 draft，较 `main@64d2ba6a76457abe8648e8b5cf34665d206be6a3` ahead 7。本记录不代表合并、发布、用户安装更新或真实投递。

## 已交付能力与边界

| 能力 | 基线中可核实的实现 | 尚不能据此声称的结果 |
| --- | --- | --- |
| A/B：输入依赖与有限子目标 | 共用浏览器 adapter 的 pre-input、readback、Host final-refresh 依赖检查；已观测记录内 school selection / record fields | 任意 ATS、iframe、closed shadow、虚拟列表、上传或完整申请均已覆盖 |
| C/D：恢复与一致性 | `attended_application.py` 的显式 `recover` / `bind-consistency`，复用 Supervisor、Episode barrier、SQLite owner；checkpoint / claim / intent 保留来源与材料约束 | 基线已自动贯通 Host 回复、持久恢复和下次 reviewed input；该接线由 P1-B0 独立交付 |
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

- [ ] 三个来源提交均可定位，工作树归属清楚；组合 diff 没有吸收原 source 的未归属 WIP。
- [ ] combined evaluator：blocked/needs_fact、非人工终止、缺 oracle、回调失败、部分观测、旧 schema 隔离全部通过；真实 DOM duplicate/no-commit/missing 均是请求 1、等待 `null`。
- [ ] P1-B0 主验收使用实际 Host 回复、真实 SQLite 和受控浏览器；部分成功、缺事实、parser overwrite、丢回复、停止 Host、进程重启、submit latch 各有证据。未知效果不得授予重试许可。
- [ ] P1-B0 的下一次输入仍需显式 review；同 job/attempt/session/tab/epoch 与材料约束不丢失，先持久化完成/不可逆事实再确认 transport。
- [ ] P1-C0 区分 provider 客户端墙钟与服务端计算时间；encoding、decision、browser 的嵌套/并行关系明确；coordination、slot、load、recovery 缺真实边界就未知，不拿 residual 冒充等待。
- [ ] token/cache/reasoning/cost 来源缺失时保持未知；配对冷暖计划、价格来源和预算有模板，未启用 `--run-model`，未调用真实模型。
- [ ] 组合后的目标 Python、Node/浏览器测试、Ruff、diff 检查通过；若入口或发行资产发生变化，补对应源码/构建入口验证。不得以旧 CI 替代新组合结果。
- [ ] 原始 33 个 checkout 的 HEAD/状态清单与隔离前一致；最终本地 commit、diff、测试记录和仍未验证项交回父任务。

真实模型实验、授权真实页面的非提交试点、生产建议晋级、安装/发行验证与远端 review/merge/release 仍按 [后续批次](p1-revised-next-steps.md) 单独判断，不从本机测试推导为完成。
