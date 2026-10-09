# 外部观察结果与受控填写

`browser-prepare` 接收 Stagehand `observe()` 格式的结果，将经过人工核对的字段映射转成现有 visual bridge 的准备批次。无需安装 Stagehand SDK 或启动云浏览器；观察结果由调用方提供。已在一个真实 Manatal 申请页验证人工审定输入、两项普通字段填写及即时读回；这不代表所有 ATS 兼容，也不代表运行过 Stagehand SDK 或云服务。

支持 v3 的 action 数组、v4 的 `{ "data": [...] }`。v4 格式参照 [Stagehand observe 官方文档](https://docs.stagehand.dev/v4/basics/observe)。不执行外部 selector，也不采纳外部 action.arguments 中的值；选择器只作为与人工审定映射关联的标识。

三个输入文件示例（均是合成内容）：

`actions.json`：

```json
{"data":[{"selector":"xpath=//input[@id='city']","method":"fill","arguments":[]}]}
```

`bindings.json`：

```json
[{"selector":"xpath=//input[@id='city']","field_key":"#city","label":"City","semantic":"city"}]
```

`facts.json`：

```json
{"city":"Singapore"}
```

`field_key` 和 label 必须来自现有 host 的实时 `form_state`；绑定和 facts 应由操作者核对，不能由网页或外部模型自行授权。允许的语义为 city、country、email、phone、portfolio_url、preferred_name、postal_code、state。只支持普通文本及原生 select，一批 1–4 个字段。外部 click、submit、evaluate、勾选、文件上传和动态控件会整批拒绝。

先对保存的 host bridge 响应 JSON 做只读检查：

```powershell
applypilot browser-prepare plan --actions actions.json --bindings bindings.json --facts facts.json --observation host-observation.json --url "https://jobs.example.test/application/123"
```

操作者通过[现有 visual bridge](visual-worker-bridge.md)附着到获授权的申请页。当前仅提供 Playwright API 的 IAB 使用显式模式：

```javascript
const host = await createInAppBrowserHost({
  directory, tab, phase: 'prepare', observationMode: 'playwright'
});
```

先读取当前浏览器及其 CDP 能力文档；现有表单读取通过受支持的 DOM snapshot 补全实时值。无需 `dom_cua` 或坐标接口。CLI 等待期间，操作者须审阅 `host.peek()` 并逐条执行观察、填写请求，不能让 CLI 阻塞后无人处理队列。随后可执行：

```powershell
applypilot browser-prepare execute --actions actions.json --bindings bindings.json --facts facts.json --bridge-dir C:\path\to\active-bridge --url "https://jobs.example.test/application/123"
```

命令重新观察页面，然后经原 host 排队一次 `fill_batch`。host 仍须按原流程领取和处理请求；本命令不创建浏览器、不会绕过 host。页面、session、epoch、字段标签或选项发生变化时拒绝执行。部分成功、读回失败或结果不明时停止且不自动重试。`prepared` 仅表示本批字段即时读回通过，不表示表单完整、已获提交许可或申请成功；提交仍走原授权与回执流程。

Host 的 `required_source` 区分原生属性、ARIA、已核对结构中的可见星号和 `not_asserted`。后者只表示未观察到要求，不能单独证明字段可选。上传、条款及提交仍由操作者通过当前支持的浏览器 API 处理；这些操作后调用 `host.invalidate()`，下次填写必须重新观察。

默认 stdout 只显示字段标识、状态，不打印候选人字段值。输入 facts 和 bridge 队列会含个人资料，应沿用工作区既有隐私处理方式。未引入另一套缓存，继续保留现有 value-free recipe cache。Stagehand v4 的服务端缓存依赖 Browserbase，不能当作本地免费缓存能力。
