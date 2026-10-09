# 真实文件选择与上传测评（2026-10-09）

在前轮三个真实岗位页面上，补做了文件选择、附件显示、移除后重传、文件名兼容和并发上传测试。合计 8 次有效文件选择完成：5 次通过浏览器公开接口排查，3 次通过修复后的项目桥接复验。另复现 1 次文件选择器超时，并验证 1 次空数组被拒绝后的恢复。没有点击申请提交、勾选协议或上传成绩单。

## 实际结果

| 岗位 / 平台 | 有效文件选择次数 | 最后独立观察到的结果 |
| --- | ---: | --- |
| [ShopBack SRE Intern / Lever](https://jobs.lever.co/shopback-2/df5ed7cf-eb1f-4f17-ad11-35ad6011412c/apply) | 2 | 附件文件名正确，页面从 Analyzing resume 变为 Success；第二次解析保留了事先修正的姓名 |
| [Visier Software Developer Intern / Greenhouse](https://job-boards.greenhouse.io/visiersolutionsinc/jobs/4711074006) | 2 | 首次隐藏输入点击未接通选择器，改用可见 Attach 后恢复；移除附件后，经修复桥接再次上传，进度结束后显示文件名和 Remove file |
| [Unravel Machine Learning Intern / Manatal](https://www.careers-page.com/unravel-carbon-pte-ltd/job/8X595793/apply) | 4 | 原文件、中文和空格文件名、错误参数后的有效重选、桥接重选均能显示相应文件名 |

这里的次数是成功设置文件并得到页面文件名证据的操作数，不是独立文件数量、申请数量或服务器接收次数。Manatal 的证据只证明该表单持有选中文件，没有独立服务器上传完成信号。Lever 与 Greenhouse 有上述页面处理结果，仍不构成雇主收到申请的回执。

材料使用现有简历库的工程版及 Unravel 已绑定版本。用于中文和空格文件名测试的副本与对应原 PDF 字节一致；没有更改简历内容或覆盖原件。工程版用于附件控件测试，不代表为 ShopBack/Visier 新完成岗位适配。材料路径、校验值与私有页面资料仅留在 data 目录。

## 已复现并修复的问题

### 1. Playwright 观察模式拒绝上传操作

原有 guard 只允许文本、选择框等表单操作；`upload_artifact` 只能通过旧 `dom_cua` 节点路径执行。当前内置浏览器采用 Playwright 观察模式时，即使文本填写正常，也无法从该桥接上传。

现在新增 `artifact_id + field_key` 路径，保留互斥的旧 `artifact_id + node_id` 路径。新路径只允许当前观察到的唯一可用 file 控件，执行前重新核对页面、控件身份、类型、状态和定位唯一性。文件仍由 host 的 artifact allowlist 提供；worker 不获得任意路径或 selector 权限。Python 校验与 MCP 工具说明同步更新。

### 2. Greenhouse 真实 file input 只有 1×1 像素

首次直接点击 `#resume` 没有报点击错误，但 10 秒内未产生 filechooser。检查实际页面发现：输入有 `visually-hidden` 样式，旁边才是可见 Attach 按钮。根据新鲜截图点击该按钮后，选择器立即接通，上传进度最终变成附件文件名。

修复后，观察器对这一明确结构记录关联触发器：裁剪输入、唯一上传组、同一直接父容器内的唯一可见普通按钮、唯一 for 标签与按钮文字匹配。排除提交、申请、确认和协议按钮。host 重新核对触发器身份后只点击一次，没有先失败再自动重试，也不注入 DOM。

真实页面第二轮已经验证：桥接直接识别正确的 Attach 按钮并完成文件选择，无需再次截图兜底。

### 3. 文件设置完成不能代表网页上传已完成

现在动作回复明确给出 `file_selection_done` 和 `webpage_acceptance: unverified`。网页是否接受、是否仍在处理、是否报错，由后续页面观察核对。不会把 `setFiles` 没抛错直接报告为服务器接收成功。

窄范围代码复核另外发现：上传携带不支持的 screenshot 回读模式时，可能先完成选文件，再把回读错误误分为可恢复的输入前拒绝。已补上动作前模式校验；并记录动作是否已完成，让输入后的回读失败报告 `outcome_unknown`、停止 host，避免诱发重复上传。该错误路径通过合成回归验证，未在雇主页面故意制造结果不明。

## 并发与恢复

- 第一轮 Lever 与 Greenhouse 同时启动：Lever 文件选择和处理完成，Greenhouse 选择器超时；失败没有阻止另一页面完成。
- 修复后二轮通过三个独立 prepare host、两个重叠调度调用执行上传，实测执行峰值为 2。三者最终文件名分别正确，未观察到跨页面文件混用。
- Greenhouse 的 Remove file 界面操作确实恢复了空上传控件，随后重传成功。
- Manatal 支持中文和空格文件名，以及将既有选择替换为另一个同字节副本。
- 当前浏览器 API 拒绝 `chooser.setFiles([])`，错误为 requires at least one file；原附件保持不变，同一 chooser 随后选择有效 PDF 可以恢复。这不是已完成原生取消测试，也不能将空数组当作清空接口。
- Lever 首次解析填入简历中的常用名；人工按事实主档修正为法定姓名后，再次上传仍保留正确姓名。自动解析结果仍需事实核对。

## 验证与边界

- JS 目标集合：`visual-bridge-host.test.mjs`、`browser-form-state.test.mjs`、`browser-form-shadow.test.mjs`、`browser-field-batch.test.mjs`，78 passed / 0 failed。
- Python 目标集合：`tests/test_visual_bridge.py`、`tests/test_visual_form_operations.py`，19 passed。
- 后续状态判定补修：`visual-bridge-host.test.mjs` 与 `browser-observation-feedback.test.mjs` 最小相关集合 73 passed / 0 failed，包含两个新增回归；与前述 JS 集合存在重叠，不相加。正常 DOM 上传和控件映射未再改动，未重复上传真实附件。
- 定向 Ruff 检查通过。窄范围代码复查确认截图模式和动作后观察失败的原问题已关闭；该复查为只读代码核对，没有重复执行测试。
- 范围内 `git diff --check` 通过。未提交、推送或部署代码。
- 私有证据：`../data/reports/file-upload-validation-20261009/`，含每轮原始响应、失败结构、最终 UI、截图和 `summary.json`。
- 最终独立观察后，本轮三个页面和 prepare host 全部关闭；没有申请状态或提交授权写入。

本轮没有验证原生系统文件对话框取消、拖放、云盘附件、批量多文件、超大或非法格式文件、服务器故障注入、最终申请提交或雇主回执。没有为制造错误向真实招聘系统上传损坏文件或身份材料。

## 后续修复优化（同日追加）

用户要求继续落实对应修复后，补齐了上传证据生命周期和失败恢复指令：

- 上传变化以动作执行前的新鲜同页表单为基线，避免把上次观察之后、上传之前的变化误归为解析结果。连续只读观察保留该基线，以捕获异步解析；下一次输入尝试、第二次上传尝试或观察到离开原页面后，旧基线即失效，返回原 URL 不会恢复。
- 错误响应按实际 host 状态给出恢复建议。活跃 host 的前置拒绝允许重新观察；已停止的 host 明确返回 `reobserve_before_retry: false`、`host_state: stopped`、`handoff_required: true`，不增加自动重试。
- 主申请 prompt 现在覆盖所有 `outcome_unknown`，包括 click、chooser、setFiles 和回读错误。结果未明时停止输入并交回协调方；不得切换控制器、上传入口或简历文本模式。普通已确认失败才允许基于新观察恢复。新增 `field_key` 与选文件/网页接受区别的调用说明。

新增 5 个行为回归在修复前全部失败，修复后全部通过。最终相关 host/observation-feedback 集合 **78 passed**；生成 prompt 合同与 Python visual bridge/form operations 集合 **20 passed**。这些是本次运行结果，与上一阶段集合有重叠，不合计为独立覆盖数量。定向 Ruff 与范围内 diff 检查通过。没有测试实际模型的恢复决策准确率。

本次另外在原 ShopBack SRE 的真实 Lever 表单完成 **1 次**文件选择，仍使用既有审核材料，不属于新的申请提交。立即回读时页面正在解析、上传差异为 0；随后页面显示 **Success!**，持续观察捕获姓名、邮箱、电话和公司 4 个解析变化。按事实主档将常用名纠正为法定名 （不在公开报告中保存姓名） 后，独立读取确认姓名保持正确，`post_upload_changes` 为 0，证明手工纠正不会继续归到旧上传。未勾协议、未点击 Submit；该页面 18 与 prepare host 均已关闭。

本次私有证据位于 `../data/reports/file-upload-validation-20261009/repair-followup/`，包含原始桥接回复、最终读数、截图及摘要。Greenhouse/Manatal 的上一阶段证据未当作本次新跑结果；未在真实站点故意制造上传结果不明或服务器故障。
