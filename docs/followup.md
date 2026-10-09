# Local application followup

`followup` keeps recruiting feedback, interview rounds and next actions in the
workspace database. Its observations are separate from `jobs.apply_status`,
submission receipts and company-priority history. It consumes JSON supplied by
the operator; it does not scan or send email.

Always select the workspace before the command:

```powershell
applypilot --workspace C:\demo\workspace followup import --file C:\demo\events.json
applypilot --workspace C:\demo\workspace followup pending
applypilot --workspace C:\demo\workspace followup resolve --event-id <id-from-pending> --url https://example.test/jobs/42
applypilot --workspace C:\demo\workspace followup timeline --url https://example.test/jobs/42
applypilot --workspace C:\demo\workspace followup add-action --url https://example.test/jobs/42 --summary "Prepare examples" --due-at 2026-10-12T09:00:00+08:00
applypilot --workspace C:\demo\workspace followup due --at 2026-10-12T09:00:00+08:00
applypilot --workspace C:\demo\workspace followup complete-action --action-id <id-from-timeline>
applypilot --workspace C:\demo\workspace followup export-ics --file C:\demo\followup.ics
```

The import file is an array of reviewed observations:

```json
[
  {
    "provider": "outlook",
    "message_id": "example-message-42",
    "occurred_at": "2026-10-09T10:00:00+08:00",
    "event_type": "interview_invited",
    "job_url": "https://example.test/jobs/42",
    "company": "Example Company",
    "title": "Analyst Intern",
    "evidence_ref": "local-reviewed-mail:example-message-42",
    "summary": "First interview invitation",
    "scheduled_at": "2026-10-13T14:00:00+08:00",
    "round": 1
  }
]
```

Required fields are `provider`, `message_id`, `occurred_at`, `event_type`,
`summary`, and `evidence_ref` for recruiting observations. Supported types are
`recruiter_feedback`, `rejected`, `assessment`, `interview_invited`, `interview`,
`interview_completed`, `offer`, `withdrawn`, and `manual_note`. A `manual_note`
may omit evidence and is presented as a note, not a confirmed recruiting result.
Evidence references record operator-supplied provenance; the importer does not
independently verify their contents. `company`, `title` and `job_url` are optional
candidates. `scheduled_at` is optional for interviews; `round` is an optional
positive integer. All dates require an explicit UTC offset. Dates are stored in UTC.

Only an exact existing `jobs.url` is automatically associated. Missing or unknown
URLs stay pending even if a company/title candidate looks unique. `resolve`
records the operator's explicit choice without replacing the original source
URL or observation. An already matched event cannot be reassigned.

The case-insensitive provider plus case-sensitive message ID is the immutable
import identity. Identical re-imports are ignored; any conflicting observation
rejects the entire batch, preserving all earlier data. An operator must choose a
different message ID for a distinct observation. Invalid fields also reject the
entire batch. Action completion is idempotent.

Calendar export includes matched dated interviews and open actions, optionally
filtered by `--url`. Entries have stable UIDs, UTC dates, escaped text, UTF-8
line folding and CRLF line endings. Pending interviews are excluded until resolved.
This first version exports point-in-time calendar entries without duration,
email delivery, or an editable web interface.

Dashboard integration uses
`applypilot.followup.followup_summary(conn, now=None, limit=20)`. It returns
`pending_count`, `open_action_count`, `due_action_count`, `due_actions`,
`upcoming_interviews` and `recent_events`. `timeline(conn, job_url)`,
`pending_events(conn)` and `due_actions(conn, at=None)` are also read-only.
A missing connection or missing followup tables returns empty data and creates
no schema. Writes lazily create only the two followup tables after the existing
database initializer has accepted the workspace schema.
# 申请进度统计 / Application progress

工作台新增第 5 个页签「申请进度」。它读取本地完整申请档案，提供阶段计数、转化漏斗、桑基流向图、每周进展、岗位明细和时间轴。点击数字或图表节点可以查看对应的申请。可按公司/职位、投递日期、新加坡时间的统计截止日和核验范围筛选；明细分页不影响全量统计。

## 统计口径

- 默认统计 `verified`（投递已核验）和 `user_confirmed`（本人明确确认）。`reported`、`unverified`、`uncertain` 只在「全部记录」出现，不能直接当作成功投递。
- 同一申请档案只计一次，浏览器重试、回执、重复扫描和后续邮件都不是新申请。
- 当前状态与「曾到达」分开。收到 offer 与接受 offer 分开；接受、拒绝 offer、撤回和招聘方拒绝是不同事件。
- 面试邀请计入到达该轮，完成面试单独记录。缺少轮次不补成一面；跳过前序记录的申请不用于虚构完整链条。转化率的分子要求同一申请按先后顺序到达相邻两阶段，分母为到达前阶段的申请数，因此尚在等待的申请也在分母中。
- 时间筛选使用投递日作为 cohort；每周进展按事件日期计算，同一申请同一阶段只取首次记录。仅有历史观察日期时标为 `time_basis=observed`，不会称为确切发生时刻。
- 未知投递日期保留在全期统计，启用投递日期筛选时排除并提示数量。历史截止视图基于**当前已更正证据**重建，不是当时数据库的快照；投递日期未知时无法准确还原早期分母。
- 失联、职位下架、失败的投递尝试和平台归档均不自动成为招聘拒绝。不同终态互相冲突时显示待核对，只有明确 `reopened` 重新开启流程。
- 未唯一匹配的反馈不计入图表。数据覆盖区展示每个邮箱来源最近完整扫描截止时间和最近尝试结果；刷新浏览器不扫描邮箱。

## 本地使用

在 `applypilot-local/source` 中通过工作区包装器运行：

```powershell
..\run.ps1 followup refresh
..\run.ps1 followup stats
..\run.ps1 followup stats --since 2026-09-01 --until 2026-09-30
..\run.ps1 followup stats --as-of 2026-09-30T23:59:59+08:00 --scope all
```

`refresh` 将现有岗位中的投递依据关联至进度档案，然后生成 `../data/dashboard.html`。`stats`、查看工作台和生成页面均不扫描网络、不改投递状态。`stats` 是纯读操作；`refresh` 只更新进度档案并生成页面，不修改 `jobs.apply_status` 或投递授权。

旧结构化记录先预览，再导入。预览在内存数据库副本运行实际导入逻辑，不修改原库或旧文件；首次实际导入前保留 SQLite 一致性备份。

```powershell
..\run.ps1 followup import-history --directory ..\data\application-status-sync --dry-run
..\run.ps1 followup import-history --directory ..\data\application-status-sync
..\run.ps1 followup refresh
```

旧 `events.jsonl` 导入器不执行一次性的 prepare/finalize 脚本。Markdown 台账必须先审阅转换为下述事件包，不能直接用近似公司名批量认领岗位。

## 每日同步的写入接口

`followup import --file <bundle.json> --dry-run` 预览，确认内容后使用 `--refresh-dashboard` 导入并重建页面。原来的事件数组仍受支持；事件包新增 `applications`、`events`、`sync_runs`，这些内容在同一事务中提交。页面生成失败会保留已落库事件并返回非零状态，可单独 `followup refresh` 重试。

```json
{
  "applications": [],
  "events": [{
    "provider": "gmail",
    "message_id": "stable-provider-message-id",
    "event_type": "interview_invited",
    "occurred_at": "2026-10-09T02:00:00Z",
    "job_url": "https://careers.example.org/jobs/42",
    "round": 2,
    "stage_id": "round-2",
    "scheduled_at": "2026-10-15T14:00:00+08:00",
    "summary": "Second-round invitation",
    "evidence_ref": "reviewed-mail:stable-provider-message-id"
  }],
  "sync_runs": [{
    "provider": "gmail",
    "run_id": "20261009T021000Z",
    "status": "success",
    "attempted_at": "2026-10-09T02:10:00Z",
    "cutoff": "2026-10-09T02:10:00Z",
    "complete": true
  }]
}
```

示例 URL 必须替换为已有的精确 `jobs.url`，否则事件进入待匹配。自动同步须先读取 `followup stats` 中各 provider 的 `last_successful_cutoff`，沿用各来源独立的重叠窗口和稳定邮件 ID。扫描失败同样导入 `sync_runs`，使用 `status=failed`、`complete=false`、`cutoff=null`；部分成功使用 `partial`。只有完整扫描、候选分类和事件持久化均成功，才记录成功截止点。零新事件的成功扫描也需要写入记录。未匹配事件可以作为待匹配记录落库，不能被静默丢弃。

同一来源的重复事实由 `(provider, message_id, fact_key)` 去重。单个事实省略 `fact_key`；一封邮件有多个事实时使用稳定不同的 key，后续运行保持不变。重复相同内容是 no-op；同 key 内容不同会拒绝整个包，不能覆盖旧事实。无原邮件 ID 的历史回填使用 `provider=automation_record` 和稳定台账记录 ID，不冒充新读取的邮件。

外部申请尚未在 jobs 中登记时，可在 `applications` 写入 `source_key`、`company`、`title`、可选 `job_url`、`submitted_at` 和 `submission_basis`；`submitted_at` 接受日期或带时区的时间。`source_key` 必须稳定，原内容重复注册是 no-op。事件用 `application_key` 指向同包或既有档案的 `source_key`，也可直接用 `application_id`。同一 URL 不重复创建档案，跨平台身份需要明确核对后关联。

已确认回执可使用 `submission_confirmed`，本人明确确认使用 `submission_user_confirmed`；两者都要求 `evidence_ref`。确知原投递日期时附 `submitted_at`，仅知道回执日期时省略，不能拿回执时间假造投递时间。这些事件只提升统计档案的依据等级，不替代原有 receipt reconciler，不改变投递是否可重试的安全判断。

待匹配事件可使用以下任一方式明确关联：

```powershell
..\run.ps1 followup pending
..\run.ps1 followup resolve --event-id <event-id> --url <exact-jobs-url>
..\run.ps1 followup resolve --event-id <event-id> --application-id <application-id>
```

事件更正使用新事件的 `supersedes_event_id`；撤销事实用 `event_type=retracted`，不删除原事件。已经更正的事件只能继续更正它的替代事件。改期、取消须指明同一个 `stage_id` 或 `round`，日历只保留当前有效安排。完整 CLI 时间轴保留原始证据记录；工作台只展示有效投影，不嵌入邮件正文、原邮件 ID、证据链接或敏感签名链接。

只有日期的历史观察使用该日新加坡时间零点作为排序位置，同时传入 `date_precision=day`；这不是精确发生时刻。已知观察日期但不知道事件实际日期时再设 `time_basis=observed`。

## 实现与兼容

数据库版本 3 新增申请身份、关联引用和来源扫描表，并保留原 followup 事件 ID、内容及自定义索引。代码分别位于 `application_progress.py`、`progress_import.py`、`storage/followup_schema.py` 和离线前端资源 `progress.js` / `progress.css`。旧版本 schema 的页面读取仍然兼容；读操作不隐式建表或导入。

图表使用本地打包的 [Apache ECharts 6.1.0](https://github.com/apache/echarts/releases/tag/6.1.0)，来自官方固定版本，许可证和 NOTICE 随资源分发。页面不依赖 CDN、网络字体或新后台服务。测试使用虚构申请验证 100 → 10 → 5 → 3 → 2 → 1、各阶段下钻、重复导入、更正、截止日、失败检查点和 Python/浏览器端口径一致性。

---
