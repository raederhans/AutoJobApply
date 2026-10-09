# 有依据的面试准备包

对工作区中已导入的精确岗位生成中文 Markdown 和 JSON 准备包。默认完全本地运行，不访问浏览器、邮箱或模型，不修改数据库、申请状态或个人事实。

```powershell
..\run.ps1 interview prepare --url "https://careers.example/jobs/123" --round "技术面" --output "C:\prep\job-123-technical"
```

`--output` 必须是不存在的新目录，其中保存 `pack.md` 和 `pack.json`。文件以原子方式发布；拒绝覆盖任何现有路径，包括原简历和已生成的准备包。

准备包包含岗位重点、经历追问、STAR证据原文与填空、待核实差距、反问，以及完整JD和所选简历文字快照。每个问题引用 `jd:L行号` 或 `resume:L行号`。文字匹配只提示准备方向，不证明技能；STAR结果由本人依据真实经历补充，没有依据不生成数字或成果。

岗位重点优先覆盖职责、任职要求及加分条件，跳过职位标题、公司宣传和福利介绍。STAR优先引用经历/项目中的行动条目，并按岗位文字相关度选择；公司名、学位、GPA和技能清单不作为STAR证据。没有识别到具体条目时显示缺口，完整原文仍保留，所有引用维持原始行号。

材料状态明确区分：

- `sent_snapshot_verified`：同一岗位的已接纳回执绑定到精确 gate/attempt/batch，材料记录中的PDF hash与简历库固定 render 匹配，并核对PDF字节和固定文字内容身份。
- `current_unverified`：没有可核对的已投快照，使用岗位当前简历路径。即使岗位状态是 applied，也不称该材料为已投版本。
- `explicit_unverified`：用户显式选定当前材料，无法证明为该次已投附件。

缺失或变动的历史文件会显示警告，并在存在当前岗位材料时使用明确标注的当前版本；两者都不可用则要求选择材料。当前数据库JD会固定保存，不能仅凭材料回执称它为投递当时的JD；岗位指纹变化会显示警告。

显式选择支持UTF-8 `.txt`/`.md`、`.docx`、有可提取文字的 `.pdf`，以及简历库 `artifact_id` / `render_id`：

```powershell
..\run.ps1 interview prepare --url "https://careers.example/jobs/123" --resume "C:\materials\selected.txt" --output "C:\prep\job-123-selected"
```

已登记为另一岗位专用的简历路径会拒绝；artifact或render如果仅绑定另一岗位也会拒绝。没有其他岗位专属绑定，或已绑定本岗位的简历库材料可以显式选择，状态与内容身份会核对。输出不表示事实审核或申请材料重新批准。

可选 `--use-llm` 会将所选JD和简历文字发送至现有LLM配置的提供方。模型仅可返回严格JSON的来源ID配对，本地验证后生成练习问题；不能输出候选人技能断言、示例答案或成果。引用不合法、额外字段或调用失败时降级为完整本地准备包，报告 `llm.status= downgraded_to_local`。默认不会调用模型。

模型只收到经过筛选的职责/要求和经历条目。严格JSON解析不接受Markdown围栏或从杂乱输出中截取JSON；空内容、客户端标记的截断，以及格式错误分别记录 `empty_content`、`response_truncated`、`invalid_json`。`llm.response_diagnostics`保存现有客户端提供的结束原因、字符数、token计数，以及解析错误位置，不保存原始模型响应或推理正文。诊断信息用于判断降级原因，不自动重试或修改全局模型设置。

来源配对的单次调用最多使用8192个输出token，结果仍限制为最多8组来源ID。实际验证发现，具备推理能力的提供方会先用输出预算生成推理，原1600预算耗尽时可能尚未输出JSON正文；此处只扩大该调用的预算，不更改提供方、模型、thinking或重试设置。仍保留严格验证与本地降级，扩大预算不保证所有模型均能完成。
