# 职业目标、诊断与学习路线（M1–M4）

`career` 将研究用公司岗位、技能任务目录和用户显式选择的材料接入当前本地工作区。它产生逐条、可回源、可重算的诊断，保持资格条件、材料支持和任务观察分离。

本阶段提供 CLI、持久化数据、任务路线编排、进度与评审、静态只读报告及本地交互式工作台。诊断输出中的目录锚点是有限任务建议；`career route` 才将它们编排为有依赖、交付物、验收条件和版本绑定的路线。

## M4：本地职业发展工作台

在私人求职环境中，双击工作区旁的 `职业发展工作台.vbs` 启动并打开工作台。仪表板只会在当前工作区的本地服务元数据通过实例和健康检查后显示入口；打开静态岗位仪表板不会启动服务。开发者可在已选定工作区后运行 `applypilot --workspace <工作区> career serve`，或用 `applypilot --workspace <工作区> career open` 启动并打开浏览器。服务默认分配空闲端口，绑定 `127.0.0.1`，实例元数据保存在该工作区的 `career/workbench/server.json`；不要把这个地址当作可共享或远程访问入口。

工作台围绕已导入的岗位、候选材料和诊断提供路线任务、前置条件、优先级、交付物及验收条件。材料需由用户明确选择，可从 Markdown、TXT 和可提取文本的 PDF 中选择片段并保留来源；它不扫描或上传整个简历目录。路线、任务提交、评审和历史都保存在当前本地工作区，并保持版本绑定。完成课程或提交文件不会自动标记通过，也不会自动提升技能等级；只有适用的评审依据可关闭相应任务。工作台不会将学习状态改写为申请匹配分、投递状态或授权。

工作台中的后台任务复用现有任务日志和租约，由单个本地 worker 执行；打开页面只读取记录。诊断、路线与补充材料在后台处理，可取消尚未提交结果的任务。结果和恢复记录在同一事务内保存；重启时，已保存的结果恢复为完成，尚未保存的中断任务需明确点击恢复。第 6、7 个有序 migration 增加工作台与恢复表。服务再次启动会优先复用原端口，让浏览器保留目标选择。

当前页面运行本地规则诊断，逐项人工记录评审；不在后台发起模型请求。服务只监听环回接口，处理当前工作区中的本地证据。页面与接口不提供公开分享入口；对外使用前应另行检查导出内容是否含私人材料。

### 申请待处理

打开工作台后，选择左侧 **申请待处理**。这里显示当前工作区原申请流程的暂停项、岗位、人工问题和下一步；不需要先选择职业诊断目标。可按待答复、可申请继续、等待接续或需要另行处理筛选。

对可答复的条目，点击 **填写答复**，填写已确认的事实并 **保存答复**，然后明确点击 **申请继续**。保存与继续是两次独立操作。答复作为本地内容寻址文件保存在 `career/workbench/human-responses/`，原人工请求保存其引用与摘要。已保存的答复保持不可变；需要更正时回到原申请流程重新核对问题。密码、验证码与安全验证应在原申请页面处理。

“已请求继续，等待申请进程接续”表示原申请的持久队列已收到请求；工作台不会代替原进程执行浏览器、模型或提交动作。原进程需要重新核对同一申请、检查点、人工答复和有效浏览器租约。会话过期、状态变化或效果待核对的记录会禁用操作并给出下一步，不能用此页面续租或重复提交。回执核对等其他暂停项仅展示处理说明。手动刷新可查看变化；后台任务轮询不覆盖当前答复草稿。

每次操作绑定页面所见版本并使用幂等键，重复发送不会重复保存或重复排队。列表最多显示最新 100 条开放记录，截断时明确标注计数范围；尚未读取或不可用的计数显示“—”。读取列表不创建控制表或修改申请记录。

## 使用

通用安装使用 `applypilot --workspace <工作区> career ...`。本仓库所在的私人求职环境使用上级 `run.ps1 career ...`，由 wrapper 绑定已有工作区。

```powershell
applypilot --workspace $careerWorkspace career import --bundle $researchBundle
applypilot --workspace $careerWorkspace career catalog
applypilot --workspace $careerWorkspace career catalog --job-id $jobId
applypilot --workspace $careerWorkspace career evidence-import --registry $evidenceRegistry --allow-root $materialsRoot
applypilot --workspace $careerWorkspace career target --evidence-id $evidenceId --job-id $jobId
applypilot --workspace $careerWorkspace career diagnose --target-id $targetId --output $reportDirectory
applypilot --workspace $careerWorkspace career diagnose --target-id $targetId --recompute
applypilot --workspace $careerWorkspace career show --diagnosis-id $diagnosisId
```

各命令输出 JSON，可取得后续所需 ID。`--recompute` 重新执行确定性判断；相同输入版本和引擎版本应得到相同诊断 ID，数据库只保留一份同 run 结果。更改资料后重新导入并建立目标；修改指定 target ID 需传 `--expected-revision`，旧诊断保留为历史。

公司方向模式使用 `target --company ... --role ... --region ... --track ... --level ... --evidence-id ...`。地区必须是目录中的原生地点值，招聘类型和层级也精确匹配；样本为空会明确报错。初版不推断不同城市、地区和 seniority 的等价关系。单岗位模式只允许一个确切研究职位，可保存多个独立目标比较。

## 研究资料契约

导入根目录必须有 `companies/jobs.json` 与 `skills/rubrics.json`，兼容第二阶段研究包。前者保留公司、职位身份、地区、原生层级、招聘类型、日期、正文快照和要求原句；后者保留技能 ID、来源、原生框架有限映射、任务及验收要求。

导入器先校验整个包，再开启事务保存。快照路径必须在选定根目录的公共子目录内；经过实际路径解析后再次检查，拒绝越界、私人 candidate 目录、错误引用、循环先修关系及无法回到原始正文的引文。不同版本保留不同快照，重复导入相同内容不增加记录。研究职位不会进入 `jobs` 申请队列，也不会由 `full_text` 自动晋升为当前可投。

六张新增表为 `career_companies`、`career_source_snapshots`、`career_catalogs`、`career_targets`、`career_diagnoses` 和 `career_model_attempts`。由第 4 个有序数据库 migration 建立；原有 migration 不改写。所有查询函数使用纯 SELECT，读取或导出不创建 schema。

完整原始资料保存在工作区 SQLite 快照中，便于原子导入和版本绑定。数据库及备份属于私人工作资料，不随软件分发。

## 候选材料契约

候选人材料单独导入，不从研究包自动加载。兼容第二阶段的 `materials/evidence` registry，也可转换既有写作 `sources/candidate_evidence` registry。文件范围由调用者的 `--allow-root` 指定，导入 JSON 不能自行扩大范围。当前文件材料使用已导出的 `.md` / `.txt`；无路径的明确内嵌文本也可注册。

```json
{
  "candidate_label": "Synthetic candidate",
  "materials": [
    {"id": "project", "kind": "project_fact_context", "path": "project.md"}
  ],
  "evidence": [
    {
      "id": "python-report",
      "claim": "Implemented a Python report generator.",
      "ownership": "personal",
      "support_type": "user_confirmed_document",
      "source_refs": [
        {"material_id": "project", "quote": "Implemented a Python report generator.", "line_start": 2, "line_end": 2}
      ],
      "demonstrates": ["python"],
      "does_not_prove": ["Distributed production pipelines or C++ proficiency."]
    }
  ]
}
```

引用必须在当前原文件和指定行范围内存在。导入与诊断发布前均检查材料版本。生成材料、简历自述和团队作品保持其原始身份；精确引文存在不等于陈述语义已验证，更不等于技能实测。

## 判断与模型

默认 `rules_only` 模式本地运行，不需要模型配置。它通过版本化要求解析和受控领域/技术映射给出材料关联；复杂混合条件保持 `unresolved`，目录外能力保持 `uncovered`。AND、OR、数量限定、学历/年限替代、示例和局部条件分别表示，条件树保存在 JSON 报告中。语言及其他资格条件独立列示。学历和头衔不授予工程能力，缺少材料不是零能力。

`--use-model` 可额外使用当前配置模型生成证据关联建议。只发送该目标的要求、选定事实引文及边界，不发送整份 profile 或所有材料文件。建议必须引用已知要求/证据 ID，始终保持为建议，不改变规则状态或技能等级。模型配置在本次工作区 `.env` / 当前进程环境中解析；没有配置时明确失败，不假装模型分析完成。

模型自由撰写的解释可能扩大事实，不能因引用了有效 ID 就视为可靠。报告中的关联解释由代码直接引用已选材料原句，配合本人贡献边界；模型只提出待核对的关联对象。原始响应保存在本地调用记录，展示规则单独版本化，更新规则可复用已保存的响应。

模型步骤在发请求前提交独立 attempt 状态；已有调用者事务时拒绝调用，避免未知请求记录随事务回滚。单次调用不自动重试或切换 provider。响应先持久保存再解析，重跑复用已保存响应；超时/崩溃后未知结果不会自动重发。

此步骤是有界的结构化关联：DeepSeek 使用现有评分流程相同的 `thinking=disabled` 策略，避免推理内容耗尽 JSON 输出预算；请求策略纳入版本和缓存身份。空响应、无效 JSON 或错误引用会保留原响应并明确失败，不生成诊断，也不在重跑时再次付费请求。

```powershell
applypilot --workspace $careerWorkspace career diagnose --target-id $targetId --use-model --output $reportDirectory
applypilot --workspace $careerWorkspace career attempts
# 仅当确实决定重新请求未知结果时使用；可能造成重复模型用量。
applypilot --workspace $careerWorkspace career diagnose --target-id $targetId --use-model --retry-unknown
```

`budget_reserved` 是该次请求的输出 token 上限，不是费用估算。未知用量不当作零。API key 不进入请求记录和报告。模型诊断目前通过同步 CLI 调用；本地工作台的规则任务已提供 HTTP 队列、租约和取消界面。

## 输出与回归

`--output` 写入 Markdown、无外部资源的只读 HTML 和完整 JSON。每条判断保留 JD 原句、来源快照、条件树、材料引用、本人范围和下一步目录任务 ID。报告有本地私人材料，公开分享前需另行删减。

已保存诊断在材料、目录、岗位版本或目标修订变化后显示 stale；新运行拒绝发布旧输入结果。学习状态不会修改 fit score、申请状态、简历、profile 或申请准入。

## M3：学习路线与任务进度

路线绑定目标修订、目录、材料快照和诊断版本，保存任务 DAG 与原始验收条件。优先整理已有证据，再按依赖安排必要验证；明确的失败项可生成有限补练任务。资格单独核对，不作为学习任务的技能前置；OR 条件选择有限路线分支，保留其他替代项。无法可靠解析或目录未覆盖的要求不会被伪装成确定技能缺口。

默认不设时间预算：只输出拓扑顺序及优先理由，不生成日历或默认学习期限。只有显式提供 `--weekly-hours` 时才使用预算，`--weeks` 默认为 4；估时是作者试点估计，包含准备、交付和验收，按上界保守预留，超额任务延后，不缩小估计凑入预算。`--prefer-skill` 可重复指定偏好，`--anchor-id` 可重复选择当前目标相关锚点，仍保留必要的前置闭包。

```powershell
applypilot --workspace $careerWorkspace career route --target-id $targetId --output $reportDirectory
applypilot --workspace $careerWorkspace career route --target-id $targetId --diagnosis-id $diagnosisId --weekly-hours 5 --weeks 4 --prefer-skill $preferredSkill --anchor-id $anchorId
applypilot --workspace $careerWorkspace career routes --target-id $targetId
applypilot --workspace $careerWorkspace career progress --route-id $routeId --output $reportDirectory
```

`routes` 可不传目标 ID 列出当前工作区路线。`route --parent-route-id` 记录路线来源。进度报告包含依赖优先顺序、可开始任务、阻塞原因、原始验收项、提交、评审和历史路线；HTML 使用可展开任务卡片，不加载外部脚本或资源，也不提供状态编辑按钮。Markdown 与 HTML 只展示选定任务字段与回源标识，完整 JSON 保留原始进度数据。报告包含私人任务材料；导出不会自动发布或分享。

任务状态为 `pending`、`in_progress`、`submitted`、`completion_reported`、`passed`、`needs_revision`、`skipped`。`started` 开始任务；`marked_done` 只是自报完成；`skipped` 不证明通过；`reopened` 重新打开任务。标记完成、阅读资料、完成课程和提交文件均不会自动满足依赖。已验收通过的证据整理任务可以解锁其后置验证任务，但不授予技能等级。

```powershell
applypilot --workspace $careerWorkspace career task-event --route-id $routeId --task-id $taskId --event started --reason "开始有限任务" --event-key $eventKey --expected-revision $progressRevision
applypilot --workspace $careerWorkspace career task-submit --route-id $routeId --task-id $taskId --registry $artifactRegistry --allow-root $artifactRoot --event-key $submissionKey --expected-revision $progressRevision
applypilot --workspace $careerWorkspace career task-review --route-id $routeId --task-id $taskId --review $reviewJson --review-key $reviewKey --expected-revision $progressRevision
```

`--expected-revision` 在进度写入时检查并发修订；`--event-key` / `--review-key` 是调用者为一次具体操作提供的幂等键，重试同一操作复用同一键，不用同一键覆盖新内容。事件和评审保留历史，不通过修改旧记录隐藏失败。任务提交使用单独的产物 registry、快照和指纹，材料 ID 在各产物快照内解释，不自动混入候选事实。

评审 JSON 提供 `method`、`reviewer`、`subject`、`context`、`assistance` 和 `acceptance_results`，按任务原始验收项逐项给出完整且准确的标准名称。状态只允许 `pass`、`fail`、`not_observed`；通过与失败项必须引用该次提交产物中实际存在的片段，可附行范围。以下为虚构结构示例，标准名称需换成所选任务的全部原始验收项：

```json
{
  "method": "self_task_review",
  "reviewer": "candidate",
  "subject": "candidate",
  "context": "fictional local fixture",
  "assistance": "guided instructions",
  "acceptance_results": [
    {
      "criterion": "原始验收项一",
      "status": "pass",
      "evidence_refs": [{"material_id": "artifact", "quote": "实际产物片段", "line_start": 1, "line_end": 1}]
    },
    {"criterion": "原始验收项二", "status": "not_observed", "evidence_refs": []}
  ]
}
```

评审方式为 `artifact_review`、`self_task_review`、`independent_task_review` 或 `runtime_check`。自评保持为自评，不展示为独立证明。`subject: artifact` 只表示产物检查；即使产物由助手生成且检查通过，也不证明候选人个人能力。只有当前版本兼容、对象为候选人、方法适用的验证或练习任务观察，才反馈为有限任务通过或失败。全部验收项通过才关闭该任务；未观察项保持未完成。一个引导任务通过不表示整条 JD 条件已满足，技能等级继续为 `null`。

任务评审后重新诊断可将有证据的失败反馈为绑定要求的 `observed_task_gap`，或在同任务后续通过后清除当前失败。通过保留要求级材料关联状态，关闭对应任务建议；资格、未解析和目录未覆盖状态不被任务通过提升。服务核对当前目标、目录、材料及提交产物，过期评审保留为历史但不作当前结论。

## 补充材料与路线修订

```powershell
applypilot --workspace $careerWorkspace career supplement --target-id $targetId --registry $additionalRegistry --allow-root $additionalRoot --expected-revision $targetRevision --parent-route-id $routeId --output $reportDirectory
applypilot --workspace $careerWorkspace career rediagnose --route-id $routeId --output $reportDirectory
```

`supplement` 将显式注册的新材料与已有材料做加法合并，保存各 registry 及允许根目录的版本记录，重复材料或证据 ID 冲突会被拒绝，不静默覆盖原始事实。这里的 `--expected-revision` 是目标修订；任务事件中的修订是进度修订。补充材料不自动改写事实主档、profile 或简历，也不扩展为投递。

`rediagnose` 从路线当前绑定的目标及有效评审重新诊断，建立有 `parent_route_id` 的子路线并保留历史。子路线继承原路线的预算、技能偏好和锚点选择；原先没有预算的路线继续仅排序。旧路线及事件保留，不迁移未匹配任务或把旧任务完成状态当成新任务通过。

M3 由第 5 个有序 migration 新增 `career_learning_routes`、`career_task_events`、`career_assessments`。路线定义不可变，进度由有序事件及评审生成；读取和导出仍是只读。复用键、修订检查、提交绑定及验收引用共同避免重复写入和跨任务误用，历史不改变申请准入、fit score 或已有投递记录。

目标测试：

```powershell
python -m pytest -q tests/test_career_import.py tests/test_career_diagnosis.py tests/test_career_evidence_model.py tests/test_career_service.py tests/test_career_report.py tests/test_llm_chat_once.py tests/test_database_migrations.py
python -m pytest -q tests/test_career_planner.py tests/test_career_assessment.py tests/test_career_learning_store.py tests/test_career_learning_report.py tests/test_career_learning.py tests/test_career_learning_cli.py
```

测试只使用虚构材料，覆盖数据迁移、版本与事务、路径边界、引用回读、组合条件、证据权威、真实 CLI、模型单次请求及恢复。规则契约通过不代表判断准确率或真实候选人能力测量；这些需要独立评审与任务观察。
