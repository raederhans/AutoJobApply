# JSON Resume 互通

ApplyPilot 提供 `json-resume export`、`json-resume import` 和 `json-resume check`，用于在选定的简历文件与 [JSON Resume](https://jsonresume.org/) 之间交换内容。标准当前由 `jsonresume.org` 仓库中的 `packages/schema` 维护；本工具按官方 `@jsonresume/schema` 1.3.1 标识输出版本。

```powershell
# 从选定的 TXT、DOCX 或 ApplyPilot 内部 JSON 导出
applypilot --workspace .\data json-resume export --input .\resume.docx --output .\resume.json

# 检查 JSON Resume 支持字段的结构、URI 和日期格式
applypilot --workspace .\data json-resume check .\resume.json

# 导入为可检查草稿，默认写到工作区的 imports 子目录
applypilot --workspace .\data json-resume import --input .\resume.json

# 也可以指定草稿目录
applypilot --workspace .\data json-resume import --input .\resume.json --output-dir .\review-drafts
```

导出只读取 `--input` 指定的文件，不读取候选人 profile 或数据库。TXT/DOCX 走现有结构化简历解析，映射 `basics`、`work`、`projects`、`education` 和 `skills`。ApplyPilot 内部 JSON 的 `title` 是岗位路由信息，不会被当作候选人职位；`evidence_map` 不会导出。命令报告列出实际输出字段和省略项。无法无损表示的经历日期保存在 `x-applypilot-originalSubtitle` 扩展字段中，并在报告中警告。名称、角色或成果缺失时保持缺失，不会补写或推断。

导入会在 `<工作区>/imports/` 下新建唯一子目录，包含原始 `resume.json`、便于人工检查的 `resume.txt` 和 `report.json`。未知字段仍保存在原始 JSON，并在报告列出扩展字段路径；无法映射的部分会给出警告。导入标记为 `unvalidated_draft`，不覆盖 profile、简历库或已验证材料，也不执行候选人事实核验。相同输出路径和已存在的目标都会报错，不会静默覆盖。

`check` 和导出报告使用的是 **supported subset validation（受支持字段子集检查）**，覆盖本实现读取或写入的常用字段类型、URI 和 JSON Resume 日期格式；它不是官方完整 JSON Schema 验证，不代表简历内容、候选人事实或申请材料已通过验证。此功能不安装完整 Node.js 应用或 schema 依赖。
