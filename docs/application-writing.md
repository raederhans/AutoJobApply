# 独立申请写作

当前实现包含前两阶段：明确题目与来源，再生成、修订及评估独立草稿。开放题答案和求职信使用独立命令。输出不填入浏览器、不生成 PDF、不提交申请，也不把草稿升级为个人事实或投递授权。

从 `applypilot-local/source` 运行下面的命令。`context-check`、`questions`、`plan`、`evaluate` 和默认 `benchmark` 在本地处理显式输入；`answer`、`cover` 和带 `--use-llm` 的基准会向配置的模型发送选定的题目、JD、证据引用和可用声音样本。写作子命令不初始化数据库，也不从 profile 推断个人事实；既有 `run.ps1` 包装器仍按原设计读取工作区运行策略。

## 准备来源和题目

上下文是 `schema_version: 1` 的 JSON 注册表，包含 `sources`、`candidate_evidence`、`role`、`company` 和 `voice_examples`。以下是虚构示例，可保存为 `writing/context.json`：

```json
{
  "schema_version": 1,
  "sources": [
    {"id": "project", "kind": "project_evidence", "text": "I built a Python dashboard for weekly operational reporting."},
    {"id": "jd", "kind": "jd", "text": "Analyst internship: build Python dashboards for operational reporting."}
  ],
  "candidate_evidence": [{
    "id": "dashboard", "source_id": "project",
    "quote": "I built a Python dashboard for weekly operational reporting.",
    "status": "confirmed", "boundaries": ["No measured productivity gains"],
    "tags": ["Python", "dashboard"]
  }],
  "role": {"job_id": "fictional-analyst", "title": "Analyst Intern", "company_name": "Example", "jd_source_id": "jd"},
  "company": {"name": "Example", "facts": []},
  "voice_examples": []
}
```

来源可以用显式 `path` 引用本地 UTF-8 文件，路径相对注册表解析；同时提供 `text` 时必须与文件内容一致。每条 `quote` 必须逐字存在于对应来源。读取一份 Markdown 不会自动把全文标为已确认事实，也不会扫描其他未注册文件。

个人证据只有来自 `candidate_facts` / `project_evidence`、状态为 `confirmed`、岗位或公司范围吻合且与题目相关时才能被选用。`conditional`、`unresolved`、历史与生成内容保留其边界；简历仅用于定位，不作为个人事实来源。公司事实需要 `verification_status: "verified"`，不能支持个人经历。公司规模、阶段和交付模式分别用证据关联；不要把来源压成一段混合背景文本。

正文声明分别标记为 `candidate`、`company` 或 `role`。岗位要求只能通过 `role` 引用当前岗位的 JD；它不能证明申请人做过这项工作。生成器会收到按类型列出的可用引用 ID，最终正文中的引用片段须逐字匹配。

语气样本需注册 `voice` 来源、原文引用、`language`、`genre`、`authorship` 和 `user_approved`。只有本人写作或明确批准、语种与文体一致的样本用于表达参考。语气样本不提供事实；没有合格样本时采用直接朴素的语言，评估标为 `uncalibrated`。

单题可手工提取为 `writing/question.json`，保留完整题目、提示与限制，不补猜被截断的内容：

```json
{
  "schema_version": 1,
  "job_id": "fictional-analyst", "page_id": "motivation-step", "field_key": "experience",
  "text": "Describe your Python dashboard work and your own contribution.",
  "help_text": "At most 150 words.", "language": "en", "required": true,
  "options": [], "constraints": [{"kind": "max", "unit": "words", "value": 150, "source": "help_text"}],
  "section_path": ["Experience"], "completeness": "known"
}
```

`words` 使用空白分词；`utf16` 对应原生 `maxlength` 的 UTF-16 计数；`characters` 是显式字符计数。未明确计数规则的限制应保留在 `unresolved_instructions` 中。页面观察始终只证明已观察页面，`whole_form` 保持 `unknown`。

```powershell
../run.ps1 writing context-check --file writing/context.json
../run.ps1 writing plan --context writing/context.json --question writing/question.json
```

## 导入页面题目

`questions` 读取已保存的观察 JSON（`fields` / `form_fields`），不启动浏览器。提供稳定的字段 `field_key`、完整 `application_question.text`、提示、选项与限制。当前输入值不会写入题目记录。同一 URL 的向导必须用不同 `--page-id` 标识步骤，不能只用 URL 区分。

```powershell
../run.ps1 writing questions --file writing/observed-step1.json --job-id fictional-analyst --page-id step1 --output writing/questions-v1.json
../run.ps1 writing questions --file writing/observed-step2.json --job-id fictional-analyst --page-id step2 --existing writing/questions-v1.json --output writing/questions-v2.json
```

重复观察同一步骤会更新该页的当前题目，并保留题目、提示或限制变化的旧修订及其他页面。`answer --question` 接收一个单题对象；从题目集合选取目标题目另存为单题 JSON 后再运行。每次指定新输出文件，已有文件会被拒绝覆盖。

## 生成和修订

```powershell
../run.ps1 writing answer --context writing/context.json --question writing/question.json --output-dir writing/answers
../run.ps1 writing cover --context writing/context.json --brief "Connect the dashboard work to operational reporting." --surface body --language en --output-dir writing/covers
```

开放题由原题及提示决定内容，不能套求职信格式。求职信选择最能说明岗位匹配的经历，其他经历只在增加必要证据时加入；`body` 输出正文，`formal` 加通用称呼与落款，不补造姓名。普通英文求职信的 180–320 词只是软偏好；显式 `--max-words` 或原题限制优先。最多进行一次定向修复，使用 `--max-repairs 0` 可关闭修复。

默认在初稿通过确定性校验后增加一次表达编辑，然后重新校验改稿并做全文评审。编辑分别处理开放题和求职信，返回原文片段、修改后片段及具体理由；已经清楚的稿件可以保持原样，不为显示工作量强行重写。`--no-editorial` 可关闭这一步以作对照，仍保留原有校验与评审；`--max-repairs 0` 本身不关闭编辑。一次正常生成从两次模型调用增加为三次，后续有必要时只进行一次定向修复，不循环改到自评分满意为止。缺少事实或初稿未通过确定性校验时不调用表达编辑。

指导重点是按题意取舍材料、删除重复自评和写作规划用语、保留有用的具体细节。技术解释可以较长；行为题不必补公司名；公司动机题需要实际公司或 JD 特征；求职信结尾不必重述开头。不把标点、长句或正式用词本身当作缺陷，不插入错误或虚构个性来追求“像人写的”。公司规模不决定固定文风。历史求职信可以用于发现编辑问题，但在作者或用户认可状态未明确前，不能自动导入为本人语气样本。

求职信的主要标准是成熟的专业写作规范，参考 [MIT CAPD](https://capd.mit.edu/resources/how-to-write-an-effective-cover-letter/)、[Oxford Careers](https://www.ox.ac.uk/careers/careers-guidance/job-search-and-applications/writing-applications/cover-letters) 和 [Harvard MCS](https://careerservices.fas.harvard.edu/resources/gsas-masters-resume-cover-letter/) 的官方指导（2026-10-09查阅）：交代申请目的和目标岗位，说明有依据的兴趣与联系，用精选经历支持匹配，并礼貌收束。它们是沟通功能，不是固定段数或开头句；常规申请句、简短致谢可以保留。语言应专业、清楚、有分寸，不为消除重复而删掉必要的申请动机，也不将整封信压成项目笔记。指南中的他人经历、推荐人和个人动机不会进入候选人事实。旧稿只供识别问题，不作为主要文风标准，本轮不开展完整个人风格建模。

实际取舍先于句子润色：开篇应让读者明白申请什么岗位及相关联系；每段经历先确定要证明的能力，再选择所需的行动与交付证据。例子已经说明 API 整合和失败恢复测试时，不必继续列出每个库、端点和界面状态。保留有解释作用或岗位明确要求的技术；一般性的工程要求不能成为复制全部技术清单的理由。业务职责和数字也按同一原则取舍。删去清单后仍须保留本人做了什么，不能改成“技术能力很强”等自评。增加 JD 关键词或补一句感谢，本身不证明整封信的论述已改善。

产物中的 `editing.passes` 保存编辑前后的完整草稿、修改理由和编辑版本；`attempts` 保存改稿后的验证、评审及可能的定向修复。编辑器没有事实批准能力：数字、职责归属、受控试用范围、题目子问和原生长度限制都要重新检查。最终评审可参照编辑前文字核对遗漏，但旧稿仍不是事实来源。任何评分为 0 的评审不能同时声明 `pass`；高分也不等于用户认可自然度。目前以通用指导为主，没有合格个人样本时继续标为 `uncalibrated`。

同一申请有多道开放题时，`plan` 和 `answer` 支持重复传入 `--sibling 路径/artifact.json`。其他题的正文仅供避免重复；同一项目可以用于不同答题角度，旧答案不会成为事实来源。跨岗位或文体不符的草稿会被拒绝。

每次保存新 UUID 目录，包含 `draft.txt` 和 `artifact.json`。JSON 保留题目、上下文快照、内容摘要、声明引用、编辑记录、确定性校验、全文语义评估及每次调用元数据。修订需要原始 `artifact.json` 和明确请求，不能拿裸文本冒充旧版本：

```powershell
../run.ps1 writing answer --context writing/context.json --question writing/question.json --previous writing/answers/REVISION_UUID/artifact.json --revision-request "Make my contribution clearer; preserve the supported details." --output-dir writing/answers
../run.ps1 writing cover --context writing/context.json --previous writing/covers/REVISION_UUID/artifact.json --revision-request "Shorten the closing." --output-dir writing/covers
```

旧正文仅作编辑材料，所有事实仍核对当前来源。跨岗位、文体不符或内容摘要不匹配会拒绝；答案还绑定题目身份。修订生成新目录并关联 `previous_revision`，不覆盖旧版。

状态含义：

| 状态 | 含义 |
| --- | --- |
| `reviewed_draft` | 本次来源、长度等校验及模型全文评估通过；仍是草稿 |
| `needs_fact` | 缺少可用真实证据，或评估指出需补事实 |
| `needs_revision` | 校验或评估仍有未解决问题，有限修复后也不会假报通过 |

未达到 `reviewed_draft` 时命令仍保存可检查产物，并以退出码 2 结束；非法输入同样返回非零退出码。所有状态均保持 `authority: "none"`、`submission_ready: false`。引用和 JSON 校验不证明来源蕴含正文；模型全文评估也是独立、有限的语义证据，应人工核对重要声明。

首轮实际评测发现，当前默认模型会漏判未经记录的团队职责、因果关系和失败事件。因此 `reviewed_draft` 表示模型和规则审查过的候选稿，尚不能当作事实已核准；`needs_fact` 的未通过正文也可能含虚构内容，只用于检查问题。结果与限制见[评测说明](application-writing-evaluation.md#first-local-run-2026-10-09)。

## 真实表单中的迭代

先核对当前官方页面、原题、提示、原生长度约束和其他字段，再准备正文。公司规模只是背景；实际职责决定证据主线。例如，用户运营岗位应说明客户评估与 agent 问题处理的相关经历，客户 AI 交付岗位应说明需求、本人实现范围与试用状态。

每轮先核对事实，再填写同一个目标字段，失焦后读回完整 `value`，记录是否与待填正文逐字一致、字段是否显示错误，以及网站实际字数限制。浏览器字段通过原生校验只证明当前字段接受了文字；不证明整个申请有效、服务器保存成功或已投递。不要点击提交来测试写作。

建议按实际问题修订：第一轮确认事实和答题范围；第二轮删去分散主线的项目、重复 JD 和无意义工具清单；第三轮核对措辞自然度、贡献归属、数字所指人群和未来计划与过去经历的区别。轮数不是硬性配额，有新问题才继续改。没有新增信息时，可选的补充说明可以保持空白；先由表单协调者决定是否需要生成，不强迫写作器为每个空框补一段自荐。

数字验证同时检查含义：交付客户人数不是反馈样本人数，受控试用不是全面采用，参与项目不是本人主持试用。不得把 JD 的职责改写成已完成的工作。对未来贡献可以写明确的建议或计划，不必虚构一段“这让我学会了”的经历。

确定性数字检查会将 `more than 20` / `over 20` 与 `20+` 对齐，保留下界标记；精确 `20`、`20+` 和 `20%` 仍是不同 token。这只消除数字写法的误报，人数所指对象和因果关系仍需全文复核。

证据片段也需要足够上下文。仅摘取一段简短的英文成果总结，可能漏掉项目名、开源基础或本人职责；模型实际收到的是选中的 `quote` 与 `boundaries`，不是整个来源文件。构建 registry 时优先保留同一项目中“名称、用途、本人范围、交付状态”的连续原文片段，且仍须通过精确引用校验。不要靠 evidence ID 充当项目名称的事实来源，也不要为补上下文把其他项目或未经核实的材料一起晋升为事实。不同版本的 registry 和生成产物分开保存。

真实题目中，旧的强制非推理调用出现了这类错误。写作与评估现在保留所选模型的默认推理设置；DeepSeek 的默认输出预算提高至 16,384 token，为推理和 JSON 正文留出空间，其他模型仍为 4,096。耗尽预算或空输出继续报错，不降级重试。该改动不构成事实准确性保证，且调用可能更慢。

真实正文、源资料和页面读回记录只保存在私有 workspace 数据目录；公开回归材料必须去标识化。评估时分别报告自动生成稿、协调者改稿和网页读回结果，不把人工式 AI 编辑后的质量计作自动生成器成绩。没有用户认可的写作样本时，语气仍标为 `uncalibrated`。

## 离线预览与评估入口

基准套件 [writing-cases.json](../scripts/evals/writing-cases.json) 只使用虚构材料。默认命令只列出待运行案例，不调用模型、不写报告。`--use-llm` 才执行选定案例；必须为报告指定新路径。

```powershell
../run.ps1 writing benchmark --suite scripts/evals/writing-cases.json --output writing/eval-new.json
../run.ps1 writing benchmark --suite scripts/evals/writing-cases.json --output writing/eval-new.json --use-llm --variant new
../run.ps1 writing evaluate --suite scripts/evals/writing-cases.json --results writing/eval-new.json
```

基准如何区分确定性结果、真实模型输出、人工评分及未验证项，参见 [写作评估说明](application-writing-evaluation.md)。这些命令不产生提交能力或事实晋升。
