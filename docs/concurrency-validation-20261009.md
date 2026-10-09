# 并发验证记录（2026-10-09）

本轮按用户最新要求只测试，不提交申请。原真实申请页及其准备 host 已关闭，材料保留。并发写入只发生在合成页面、隔离队列和临时 SQLite 数据库中；未修改生产并发配置，也未运行付费模型或招聘网站提交。

## 已验证的场景

| 层面 | 场景 | 证据与结果 |
| --- | --- | --- |
| 调度器 | 1 / 2 / 4 worker，各处理八个任务 | 合成 CLI 的真实 PID 和起止时序分别证明峰值 1 / 2 / 4；同时核对 scheduler 状态 |
| 调度器 | 同 hostname 不同端口/大小写、混入不同 hostname | 单站点上限 1 / 2 均生效；被限流岗位不阻塞其他 hostname 填满可用槽 |
| 调度器 | 单任务失败、超时、host 绑定被替换 | 正确失败/超时/取消，另两项继续；重复 bridge/tab/session 在启动前拒绝 |
| 调度器 | 独立进程争抢 batch/worker 租约 | 后来者被拒，先前持有者的 token 不变；部分获得的租约被清理 |
| 浏览器组 | 四个任务，由两次同时进入的 execute 调用驱动 | 修复后共享两个操作槽；真实 IAB host 调用峰值为 2，结果按岗位对应 |
| 浏览器组 | 同一岗位同时收到两个请求 | 一个执行，另一个立即返回已调度错误；等待中的岗位同样受保护 |
| 浏览器组 | 一路慢、另一路失败 | 空出的槽立即服务后续已审阅请求，结果不串到其他岗位 |
| 浏览器队列 | 取消尚未认领请求、关闭等待中的 host | 未发生输入，其他岗位继续；认领后的中断记录 outcome_unknown，不自动重试 |
| 真实 IAB 本地表单 | 普通文本、邮箱、原生下拉、长文本 | 四字段均读回验证成功 |
| 真实 IAB 本地表单 | 选择城市导致团队选项改变 | 第一步后 parked，后续备注为空；重新观察后按新选项恢复成功 |
| 真实 IAB 本地表单 | 邮箱格式无效 | 第一步后 parked，后续备注为空；修正并重新审阅后恢复成功 |
| 真实 IAB 本地表单 | 输入失焦后回滚 | 读回发现值未保存，停在第一步，后续字段未写入 |
| 真实 IAB 本地表单 | 旧 observation 与另一个有效请求并行 | 旧请求被拒且原字段不变；另一个岗位完成 |
| SQLite | 八路独立连接争抢同岗位、最后一个名额、跨批 writer、频率限制 | 各场一条新 claim、七条拒绝；持久名额及 gate 各一条 |
| SQLite | 相同 intent 与冲突 intent | 前者共用同一幂等 gate；后者一胜七冲突；没有增加名额 |
| SQLite | 终态/过期 attempt 重放、八路租约恢复 | 失效重放全部拒绝；恢复仅一个连接计数，提交后不明状态仍阻止重试 |

## 修复的缺陷

1. 浏览器组原来的两路限制只约束一次 execute 调用，重叠调用可以突破限制；慢任务还会使另一个已空出的槽等待整组。现在同组共享槽位并立即为岗位保留执行权。
2. SubmissionGate 原来对相同标识直接返回成功重放，跳过当前 gate 状态及 attempt 的有效性。现在只允许仍有效的 claim 重放，取消、失败、完成、不明或失效 attempt 均拒绝。
3. Windows 的 `communicate(input=prompt, timeout=...)` 可能先同步写满 stdin 管道，子进程不读取时会在进入计时等待前卡住；上层 watchdog 最终强杀 wrapper，可能留下租约。现在将 UTF-8 prompt 写入自动关闭的临时 stdin 文件，超时等待不再依赖子进程先读数据。真实大 prompt/不读 stdin 用例返回 124，子进程结束，租约清理，其他任务继续；没有放宽超时设置。

## 运行证据

- 调度器及 worker：`pytest tests/test_browser_batch.py tests/test_browser_batch_processes.py tests/test_browser_batch_concurrency.py tests/test_browser_worker.py -q -s`，43 项通过、3 项跳过，27.68 秒，exit 0。新增 scheduler 测试保留实际 worker/lease/timeout，仅将模型命令替换为本地合成 CLI；另外验证完整 UTF-8、中文、emoji、多行与 EOF 输入。最后只收紧测试断言后独立 worker 23 项再次通过，与 43 项重叠，不能相加。
- 八项合成任务在 1 / 2 / 4 worker 下的批次耗时分别为 6.638 / 3.465 / 2.024 秒。这只反映本机固定耗时 CLI 夹具与调度开销，不能外推招聘站或模型加速比。
- 三项跳过均为 POSIX SIGTERM/process-group 的 cancel/timeout/during_spawn 孙进程回收测试。Windows 本轮证明的是实际 wrapper + CLI 的超时回收、租约清理及后续任务推进，不能把它当作 POSIX 信号测试通过。
- 浏览器组：`node --test scripts/browser-host-group.test.mjs`，8 项通过，其中新增 6 项。包含真实文件队列、可控异步屏障；这些单元测试不等于真实浏览器验证。
- SQLite：31 项新并发测试及 153 项既有共享契约测试，共 184 项通过。使用八个线程、八个真实独立连接，覆盖 DELETE/WAL 和部分默认事务/autocommit 场景；不是多进程数据库测试。
- 真实 IAB：四个独立标签，八类初始/恢复/竞争场景；实际调用生产 browser host、表单操作和 host group。每次请求由主代理审阅，没有后台执行循环。测试调用绕过模型决策层，因此不代表多模型吞吐或质量。
- 证据目录：`tmp/concurrency-20261009/`。`scheduler.log`、`host-group.log`、`ledger.log`；`iab/mixed-results.json`、`recovery-results.json`、`stale-isolation.json`、`final-evidence.json` 保存具体返回和操作时序。`iab/stable.png`、`rollback.png` 为本地浏览器结果截图。
- 可复用合成页面：`tests/fixtures/apply/concurrency-lab.html`，通过 `?scenario=stable|dynamic|invalid|rollback` 选择场景。只在 loopback 提供，无提交端点。
- 最终范围内 diff 检查通过；变更生产 Python 和新并发测试 Ruff 通过。旧 worker 测试文件保留原有两处 SIM117 样式提示，其余检查通过。

## 真实招聘页只读检查及边界

同时观察了 [ShopBack / Lever](https://jobs.lever.co/shopback-2/df5ed7cf-eb1f-4f17-ad11-35ad6011412c/apply) 与 [Visier / Greenhouse](https://job-boards.greenhouse.io/visiersolutionsinc/jobs/4711074006) 两个实际申请页；没有填入资料或上传附件。生产观察器返回 18/38 条字段记录，并识别各自的 iframe 覆盖缺口。这些数字包括辅助控件，不是完整问题数。

检查也暴露了现有观察范围：部分 Lever 自定义问题只返回通用提示/内部名称；部分附件的可见必填标记尚未映射为 required；Greenhouse 有额外的空标签辅助输入，折叠的自定义下拉框没有当前可用选项。因此本轮只确认两页可访问、可读取部分结构，不能声称两种 ATS 已完整自动化。这些边界未通过猜值、扩大标签匹配或操作真实表单来掩盖。具体记录在 `iab/readonly-ats.json`。

本轮没有验证实际招聘站并发写入、模型限额、共享登录冲突、大批量附件上传或最终提交回执。默认容量不因隔离测试通过而提高；未发布或提交 Git。所有本轮页面已关闭，loopback 服务已停止。
