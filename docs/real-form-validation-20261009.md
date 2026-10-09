# 真实岗位填写测评（2026-10-09）

本轮在三个雇主的真实申请页面完成多轮实际写入，没有提交申请。共 21 次成功字段写入，覆盖 19 个不同字段；结束前独立读取页面，19 个字段全部符合最后一次预期值。计数不包含搜索输入、失败的地点输入、被拒绝的请求或观察操作，也不代表整份申请已经填完。

## 岗位与实际覆盖

| 真实岗位 | 平台 | 成功写入 / 不同字段 | 主要覆盖 |
| --- | --- | ---: | --- |
| [ShopBack — Site Reliability Engineer Intern](https://jobs.lever.co/shopback-2/df5ed7cf-eb1f-4f17-ad11-35ad6011412c/apply) | Lever | 9 / 9 | 姓名、公司、公开职业链接、原生下拉框、实习时间、在读阶段、学校类型、岗位来源 |
| [Visier — Software Developer Intern, January–June 2027](https://job-boards.greenhouse.io/visiersolutionsinc/jobs/4711074006) | Greenhouse | 8 / 8 | 姓名、常用名、职业链接、城市/国家、异步学校搜索及选择、自定义来源下拉框 |
| [Unravel Carbon — Machine Learning Intern](https://www.careers-page.com/unravel-carbon-pte-ltd/job/8X595793/apply) | Manatal | 4 / 2 | 姓名、事实有依据的求职文本，后续两次文本修订和多段换行保留 |

填写内容依据当前 candidate-facts.md 和项目事实主档。未填写私人联系方式、详细地址、签证/身份和人口统计答案；未上传文件、勾选协议、操作账号或点击提交。岗位只作为真实表单样本，不表示本轮已完成资格、材料和正式投递放行审查。

## 并发及恢复结果

- 三个真实岗位各有独立页面和 prepare host。两个重叠的 execute 调用共享调度，实测同时执行的 host 操作峰值为 2；第三个岗位排队。此数值不表示服务端请求并发或模型吞吐量。
- 首轮三个岗位合计 10 次普通字段写入全部通过回读。Greenhouse 学校控件另行搜索、观察实际选项，再选择并验证已选显示值。
- 保留网页内容，关闭旧 host 并以修复后的观察器重新接管；先重新观察，再继续填写。这个过程没有刷新网页，不证明刷新后或服务器端保存。
- 第二轮跨三个岗位执行 7 次写入，覆盖原生下拉框、普通文本和多段长文本，全部通过失焦回读。
- 第三轮混合执行普通写入、动态自定义下拉选择和一个暂停/恢复前的旧请求。前两项完成，旧请求被明确拒绝：`Stale observation; observe again before input`，没有影响其他岗位。
- 重新观察后修订 Manatal 文本，同时对同一请求发起两个 execute 调用。只执行一次写入，另一次返回 `Host already scheduled or executing`。
- 最后通过独立 DOM 读取核对 19 个字段，包括 Greenhouse 的已选显示值；协议复选框均未选中，文件输入均为空。随后保存截图并关闭三个测试页和 host。

## 真实页面暴露的问题与修复

1. **Lever 自定义问题标题缺失或混入控件内容。** 对只有一个控件、一个可见问题标题的 `.application-question` 使用其确切标题，识别 `✱` 必填标记。真实页面复验了九个自定义问题标题和简历必填标记。地点控件含隐藏伴随输入，其标签仍走原有路径；没有声称所有 Lever 标签均已规范化。
2. **Greenhouse 隐藏辅助输入被计为独立字段。** 仅排除已观察到的严格结构：同一个 select-shell 内、唯一已有必填断言的主 combobox、无独立名字/标签且 aria-hidden 的辅助输入。真实页面字段数从 38 降为 27，11 个辅助输入被排除，学校和来源控件仍可正确填写。
3. **Greenhouse 附件标题和必填信息位于上传组。** 对唯一绑定标题、仅包含一个文件输入的上传组读取标题和明确的 aria-required。真实页面正确识别简历、成绩单为必填，Cover Letter 保持 `not_asserted`；未实际上传，不能将此结果称为上传成功。

生产改动位于 `scripts/browser-form-state.mjs`，针对性 DOM 回归位于 `scripts/browser-form-shadow.test.mjs`。保留已有未提交改动。

## 尚未解决的实站问题

Lever 地点输入填入城市后，失焦即被清空。按字段重新检查、普通 fill 和逐字输入均未出现可选地点结果，失焦仍清空。桥接返回 `parked / readback_not_verified`，没有将动作调用成功误当成填写成功。该字段最终保持空白，未尝试写入隐藏字段或绕过页面控件。

本轮没有确定是地点数据服务、页面脚本、浏览器环境还是控件输入契约造成这一行为。它是一个明确的未解决真实站点阻碍，不计入已验证成功字段，也不能报告为已修复。本报告没有用 21 次成功写入推算总体成功率。

## 回归与证据

- `node --test scripts/browser-form-state.test.mjs scripts/browser-form-shadow.test.mjs`：18/18 通过，退出码 0。
- `node --test scripts/visual-bridge-host.test.mjs scripts/browser-field-batch.test.mjs`：16/16 通过，退出码 0。
- 本轮合计 34 项相关回归通过；这些是修复后的回归支持，不能替代上面的实际页面写入结果。
- `git diff --check` 通过。未提交代码、推送或发布。

私有测评资料保存于工作区 `../data/reports/real-fill-validation-20261009/`，包含 `summary.json`、每轮桥接请求/响应、`final-independent-readback.json` 和三个真实页面截图。真实姓名和文本值没有写入源码测试夹具。

本轮证明的是人工监督下、三个实际 ATS 页面上的分批填写、错误隔离与页面内值保留。整份申请完成、附件上传、最终提交、雇主回执、无人值守吞吐量、其他 ATS 和跨 iframe 填写仍未验证。
