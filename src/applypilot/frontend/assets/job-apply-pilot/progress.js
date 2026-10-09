/* Recruiting projections use complete, privacy-bounded records from Python. */
(function (root) {
  "use strict";
  const confirmed = new Set(["verified", "user_confirmed"]);
  const terminal = new Set(["rejected", "withdrawn", "accepted", "declined", "conflict"]);
  const stages = ["applied", "interview_1", "interview_2", "interview_3", "offer", "accepted"];
  const day = (value) => new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0, 10);
  function select(data, filters = {}) {
    const cutoff = new Date(filters.asOf || data.generated_at).getTime();
    return (data.applications || []).filter(a => {
      if ((filters.scope || "confirmed") === "confirmed" && !confirmed.has(a.basis)) return false;
      if (filters.scope === "verified" && a.basis !== "verified") return false;
      if (!(a.company + " " + a.title).toLowerCase().includes((filters.query || "").toLowerCase())) return false;
      const date = a.submitted_at ? day(a.submitted_at) : null;
      if (a.submitted_at && new Date(a.submitted_at).getTime() > cutoff) return false;
      if (!a.submitted_at && a.first_evidence_at && new Date(a.first_evidence_at).getTime() > cutoff) return false;
      if ((filters.since || filters.until) && (!date || (filters.since && date < filters.since) || (filters.until && date > filters.until))) return false;
      return true;
    }).map(a => {
      const history = a.history.filter(h => !h.at || new Date(h.at).getTime() <= cutoff);
      const latest = history[history.length - 1] || {stage: "applied", at: null};
      const reached = Object.fromEntries(Object.entries(a.reached).filter(([, at]) => at && new Date(at).getTime() <= cutoff));
      if (confirmed.has(a.basis)) reached.applied = a.submitted_at;
      return {...a, history, reached, current: latest.stage, entered_at: latest.at,
        waiting_days: latest.at ? Math.max(0, (cutoff - new Date(latest.at).getTime()) / 86400000) : null,
        timeline: a.timeline.filter(e => new Date(e.at).getTime() <= cutoff)};
    });
  }
  function funnel(applications) {
    return stages.map((stage, index) => {
      const items = applications.filter(a => Object.hasOwn(a.reached, stage));
      const prior = index ? applications.filter(a => Object.hasOwn(a.reached, stages[index - 1])) : [];
      const converted = items.filter(a => prior.includes(a) && (!a.reached[stages[index - 1]] || a.reached[stage] >= a.reached[stages[index - 1]]));
      return {stage, count: items.length, ids: items.map(a => a.id), previous_count: index ? prior.length : null,
        converted_count: index ? converted.length : null, conversion_rate: prior.length ? converted.length / prior.length : null,
        missing_previous: index ? items.filter(a => !prior.includes(a)).length : 0};
    });
  }
  function flow(applications) {
    const nodes = new Map(), links = new Map();
    for (const a of applications) {
      const path = a.history.map(h => h.stage);
      // Every unfinished path gets its own stop, keeping outgoing totals honest.
      if (!terminal.has(path[path.length - 1])) path.push("waiting:" + path[path.length - 1]);
      path.forEach((stage, depth) => {
        const name = depth + ":" + stage;
        if (!nodes.has(name)) nodes.set(name, {name, stage, depth, ids: []});
        nodes.get(name).ids.push(a.id);
        if (!depth) return;
        const source = (depth - 1) + ":" + path[depth - 1];
        const key = source + "/" + name;
        if (!links.has(key)) links.set(key, {source, target: name, value: 0, ids: []});
        links.get(key).value += 1;
        links.get(key).ids.push(a.id);
      });
    }
    return {nodes: [...nodes.values()], links: [...links.values()]};
  }
  function weekly(applications) {
    const weeks = new Map();
    function add(at, kind, id) {
      if (!at) return;
      const d = new Date(day(at) + "T00:00:00Z");
      d.setUTCDate(d.getUTCDate() - (d.getUTCDay() + 6) % 7);
      const key = d.toISOString().slice(0, 10);
      if (!weeks.has(key)) weeks.set(key, {week: key, applied: [], interview: [], offer: [], rejected: []});
      weeks.get(key)[kind].push(id);
    }
    applications.forEach(a => {
      if (confirmed.has(a.basis)) add(a.submitted_at, "applied", a.id);
      for (const key of ["interview", "offer", "rejected"]) add(a.reached[key], key, a.id);
    });
    return [...weeks.values()].sort((a, b) => a.week.localeCompare(b.week));
  }
  const model = {select, funnel, flow, weekly, day};
  if (typeof module !== "undefined" && module.exports) module.exports = model;
  root.ApplicationProgress = model;
  if (typeof document === "undefined") return;

  const names = {
    submission_confirmed: ["投递已核验", "Submission verified"], submission_user_confirmed: ["本人确认投递", "Submission confirmed by user"],
    submission_unverified: ["投递待确认", "Submission unconfirmed"],
    applied: ["等待反馈", "Awaiting response"], contacted: ["招聘方联系", "Recruiter contact"],
    assessment: ["测评 / 筛选", "Assessment"], interview: ["进入面试", "Interview reached"],
    interview_unknown: ["面试 · 轮次未知", "Interview · round unknown"],
    interview_1: ["一面", "Round 1"], interview_2: ["二面", "Round 2"], interview_3: ["三面", "Round 3"],
    offer: ["收到 offer", "Offer received"], accepted: ["已接受 offer", "Offer accepted"],
    declined: ["已拒绝 offer", "Offer declined"], rejected: ["申请被拒", "Rejected"],
    withdrawn: ["已撤回", "Withdrawn"], conflict: ["结果冲突 · 待核对", "Conflicting outcomes"],
    verified: ["已核验投递", "Verified submission"], user_confirmed: ["本人确认", "User confirmed"],
    reported: ["平台标记", "Platform reported"], unverified: ["投递待核验", "Unverified submission"],
    uncertain: ["提交结果不明", "Submission uncertain"], interview_invited: ["面试邀请", "Interview invitation"],
    interview_completed: ["面试完成", "Interview completed"], interview_rescheduled: ["面试改期", "Interview rescheduled"],
    interview_cancelled: ["面试取消", "Interview cancelled"], recruiter_feedback: ["招聘方联系", "Recruiter contact"],
    submission_observed: ["申请回执", "Application receipt"], outgoing_message: ["发出邮件", "Outgoing message"],
    identity_pending: ["申请身份待核对", "Identity pending"], offer_accepted: ["接受 offer", "Offer accepted"],
    offer_declined: ["拒绝 offer", "Offer declined"], reopened: ["申请重新开启", "Application reopened"],
    manual_note: ["记录", "Note"], success: ["扫描完整", "Complete scan"],
    partial: ["扫描不完整", "Partial scan"], failed: ["来源不可用", "Source unavailable"],
  };
  let locale = "zh", data, panel, mounted = false, drill = null, page = 0;
  const charts = new Map();
  const tr = (zh, en) => locale === "zh" ? zh : en;
  const label = key => key.startsWith("waiting:") ? tr("仍在", "Still at ") + label(key.slice(8)) :
    names[key] ? names[key][locale === "zh" ? 0 : 1] : key.startsWith("interview_") ? tr("面试轮次 ", "Interview round ") + key.slice(10) : key;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text != null) n.textContent = text; if (cls) n.className = cls; return n; };
  const byId = id => document.getElementById("progress-" + id);
  const fmt = at => at ? new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-SG", {timeZone: "Asia/Singapore", dateStyle: "medium"}).format(new Date(at)) : tr("日期未知", "Date unknown");
  const fmtTime = at => at ? new Intl.DateTimeFormat(locale === "zh" ? "zh-CN" : "en-SG", {timeZone: "Asia/Singapore", dateStyle: "medium", timeStyle: "short"}).format(new Date(at)) + " SGT" : tr("未知", "Unknown");
  const percent = n => n == null ? "—" : (n * 100).toFixed(1) + "%";
  function button(text, click, cls = "progress-chip") {
    const n = el("button", text, cls); n.type = "button"; n.addEventListener("click", click); return n;
  }
  function drillTo(ids, title) { drill = {ids: new Set(ids), title}; page = 0; render(); byId("records").scrollIntoView({behavior: "auto", block: "start"}); }
  function filters() {
    return {scope: byId("scope").value, query: byId("search").value, since: byId("since").value,
      until: byId("until").value, asOf: byId("asof").value ? byId("asof").value + "T23:59:59.999+08:00" : data.generated_at};
  }
  function chart(id, option, handler) {
    const target = byId(id);
    if (!root.echarts) { target.textContent = tr("图形资源未加载，请使用下方明细。", "Chart assets unavailable; use the records below."); return; }
    let instance = charts.get(id);
    if (!instance) { instance = root.echarts.init(target, null, {renderer: "svg"}); charts.set(id, instance); }
    instance.setOption({animation: false, color: ["#17636b", "#e56843", "#547db1", "#9c7362"],
      textStyle: {fontFamily: '"Microsoft YaHei", sans-serif'}, aria: {enabled: true},
      tooltip: {renderMode: "richText"}, ...option}, true);
    instance.off("click"); if (handler) instance.on("click", handler);
    instance.resize();
  }
  function mount() {
    panel = byId("panel");
    panel.innerHTML = `
      <div class="progress-filterbar">
        <label><span data-i18n="范围|Scope"></span><select id="progress-scope"><option value="confirmed"></option><option value="verified"></option><option value="all"></option></select></label>
        <label><span data-i18n="搜索公司或职位|Company or role"></span><input id="progress-search" type="search"></label>
        <label><span data-i18n="投递起始日|Submitted from"></span><input id="progress-since" type="date"></label>
        <label><span data-i18n="投递结束日|Submitted until"></span><input id="progress-until" type="date"></label>
        <label><span data-i18n="统计截止日|As of"></span><input id="progress-asof" type="date"></label>
        <button id="progress-reset" type="button" class="progress-chip" data-i18n="重置|Reset"></button>
      </div>
      <p id="progress-note" class="progress-note" role="status"></p>
      <div id="progress-metrics" class="progress-metrics"></div>
      <section class="progress-section"><div class="progress-heading"><h2 data-i18n="申请转化|Application conversion"></h2><span data-i18n="曾到达 · 点击数字查看岗位|Ever reached · select a count to see roles"></span></div>
        <div id="progress-funnel" class="progress-funnel"></div><div id="progress-extra" class="progress-chips"></div>
        <details class="progress-definitions"><summary data-i18n="统计口径|How counts work"></summary><p id="progress-definition"></p></details>
      </section>
      <div class="progress-charts">
        <section class="progress-section"><div class="progress-heading"><h2 data-i18n="申请流向|Application paths"></h2><span data-i18n="点击节点或分支查看明细|Select a node or branch"></span></div><div id="progress-flow" class="progress-chart" role="img" aria-label="Application paths"></div></section>
        <section class="progress-section"><div class="progress-heading"><h2 data-i18n="每周进展|Weekly activity"></h2><span data-i18n="按事件记录周 · 新加坡时间|By recorded activity week · Singapore time"></span></div><div id="progress-trend" class="progress-chart" role="img" aria-label="Weekly activity"></div><details><summary data-i18n="查看数字|View values"></summary><div id="progress-weeks"></div></details></section>
      </div>
      <section id="progress-records" class="progress-section"><div class="progress-heading"><h2 id="progress-record-title"></h2><button id="progress-clear" type="button" class="progress-chip" data-i18n="清除图表筛选|Clear chart selection"></button></div><div id="progress-states" class="progress-chips"></div><div id="progress-list"></div><nav id="progress-pages" class="progress-pages"></nav></section>
      <section class="progress-section"><div class="progress-heading"><h2 data-i18n="数据覆盖|Source coverage"></h2><span id="progress-generated"></span></div><div id="progress-sources" class="progress-sources"></div><p id="progress-pending" class="progress-note"></p></section>`;
    for (const control of panel.querySelectorAll("input, select")) control.addEventListener("input", () => { drill = null; page = 0; render(); });
    byId("reset").addEventListener("click", () => { panel.querySelectorAll("input").forEach(n => {n.value = "";}); byId("scope").value = "confirmed"; drill = null; page = 0; render(); });
    byId("clear").addEventListener("click", () => {drill = null; page = 0; render();});
    new ResizeObserver(() => charts.forEach(instance => instance.resize())).observe(panel);
    mounted = true;
  }
  function render() {
    panel.querySelectorAll("[data-i18n]").forEach(n => { n.textContent = n.dataset.i18n.split("|")[locale === "zh" ? 0 : 1]; });
    panel.querySelectorAll(".progress-filterbar label").forEach(n => {n.querySelector("input, select").setAttribute("aria-label", n.querySelector("span").textContent);});
    [...byId("scope").options].forEach((o, i) => {o.textContent = [tr("已核验 + 本人确认", "Verified + user confirmed"), tr("仅已核验", "Verified only"), tr("全部记录", "All records")][i];});
    byId("search").placeholder = tr("例如：Analyst", "e.g. Analyst");
    const f = filters();
    const invalid = f.since && f.until && f.since > f.until;
    const applications = invalid ? [] : select(data, f);
    const unknown = applications.filter(a => !a.submitted_at).length;
    const dateExcluded = select(data, {...f, since: "", until: ""}).filter(a => !a.submitted_at).length;
    byId("note").textContent = invalid ? tr("投递起始日不能晚于结束日。", "Start date must not be after end date.") :
      data.state === "unavailable" ? tr("工作区读取失败，本页不代表零申请。", "Workspace unavailable; this does not mean zero applications.") :
      data.state !== "ready" ? tr("进度记录尚未导入。完成历史导入并刷新后即可查看。", "Progress has not been imported. Import history and refresh first.") :
      `${applications.length} ${tr("条申请记录", "application records")} · ${f.since || f.until ? tr(`日期未知的 ${dateExcluded} 条不计入本次日期筛选`, `${dateExcluded} undated records excluded by date filter`) : tr(`${unknown} 条投递日期未知`, `${unknown} submission dates unknown`)} · ${tr("截至", "As of")} ${fmt(f.asOf)}`;
    byId("definition").textContent = tr(
      "每个申请档案只计一次。默认包含已核验投递及本人确认；全部记录还包含平台标记、待核验和结果不明。漏斗按明确到达阶段统计，面试邀请不等于完成面试，也不会补出未知轮次。阶段转化率 = 同一申请按先后顺序到达前后两阶段的数量 / 前阶段数量。历史视图使用当前已更正的证据重建；投递日期未知的记录无法准确还原早期分母。无回复不会自动视为拒绝。",
      "Count each application once. Default scope includes verified and user-confirmed submissions. All records also includes reported, unverified and uncertain entries. Invitations count as reaching a round, not completing it. Missing rounds are never inferred. Conversion = applications reaching both stages in order / previous-stage applications. Historical views reconstruct corrected evidence; undated submissions cannot establish an exact historical denominator. Silence is never rejection.");
    const metrics = byId("metrics"); metrics.replaceChildren();
    [[tr("申请总数", "Applications"), applications], [tr("进行中", "In progress"), applications.filter(a => !terminal.has(a.current) && a.current !== "submission_unverified")],
      [label("rejected"), applications.filter(a => a.current === "rejected")], [label("offer"), applications.filter(a => Object.hasOwn(a.reached, "offer"))],
      [label("accepted"), applications.filter(a => a.current === "accepted")]].forEach(([title, items]) => {
      const b = button("", () => drillTo(items.map(a => a.id), title), "progress-metric");
      b.append(el("span", title), el("strong", String(items.length))); metrics.append(b);
    });
    const counts = funnel(applications), funnelNode = byId("funnel"); funnelNode.replaceChildren();
    counts.forEach((c, i) => {
      const title = i ? label(c.stage) : tr("确认投递", "Confirmed submissions");
      const b = button("", () => drillTo(c.ids, title), "progress-step");
      b.append(el("span", title), el("strong", String(c.count)), el("small", i ? tr("较上阶段 ", "From previous ") + percent(c.conversion_rate) : tr("统计起点", "Starting point")));
      b.style.setProperty("--fill", (applications.length ? c.count / applications.length * 100 : 0) + "%");
      if (c.missing_previous) b.append(el("small", tr(`${c.missing_previous} 条缺前序记录`, `${c.missing_previous} missing prior stage`)));
      funnelNode.append(b);
    });
    const extra = byId("extra"); extra.replaceChildren();
    for (const stage of ["interview", "interview_unknown", "interview_completed"]) {
      const ids = applications.filter(a => Object.hasOwn(a.reached, stage)).map(a => a.id);
      extra.append(button(`${label(stage)} · ${ids.length}`, () => drillTo(ids, label(stage))));
    }
    const flowData = flow(applications);
    chart("flow", {series: flowData.nodes.length ? [{type: "sankey", left: 8, right: 125, top: 18, bottom: 15, nodeWidth: 10, nodeGap: 14,
      draggable: false, emphasis: {focus: "adjacency"}, lineStyle: {color: "source", opacity: 0.2, curveness: 0.5},
      data: flowData.nodes.map(n => ({...n, value: n.ids.length})), links: flowData.links, label: {color: "#243b50", fontSize: 11, formatter: p => (p.data.depth === 0 ? tr("申请", "Applications") : label(p.data.stage)) + " " + p.data.ids.length}}] : [],
      graphic: flowData.nodes.length ? [] : [{type: "text", left: "center", top: "middle", style: {text: tr("暂无路径记录", "No paths in this selection"), fill: "#61758b"}}]},
      p => { if (p.data?.ids) drillTo(p.data.ids, p.dataType === "edge" ? tr("所选路径", "Selected path") : label(p.data.stage)); });
    const weeks = weekly(applications), kinds = ["applied", "interview", "offer", "rejected"];
    chart("trend", {legend: {bottom: 0}, grid: {left: 38, right: 14, top: 24, bottom: 66},
      tooltip: {trigger: "axis", renderMode: "richText"}, xAxis: {type: "category", data: weeks.map(w => w.week.slice(5)), axisLabel: {color: "#61758b"}},
      yAxis: {type: "value", minInterval: 1, splitLine: {lineStyle: {color: "#e8eef1"}}},
      series: kinds.map(kind => ({name: kind === "applied" ? tr("投递", "Submitted") : label(kind), type: "bar", stack: null,
        data: weeks.map(w => ({value: w[kind].length, ids: w[kind], week: w.week}))}))}, p => {
          if (p.data?.ids) drillTo(p.data.ids, p.data.week + " · " + p.seriesName);
        });
    const weekNode = byId("weeks"); weekNode.replaceChildren();
    weeks.forEach(w => {const row = el("div", null, "progress-week"); row.append(el("span", w.week)); kinds.forEach(k => row.append(button(`${k === "applied" ? tr("投递", "Submitted") : label(k)} ${w[k].length}`, () => drillTo(w[k], w.week + " · " + label(k))))); weekNode.append(row);});
    renderRecords(applications);
    byId("generated").textContent = tr("页面生成于 ", "Generated ") + fmtTime(data.generated_at);
    const sources = byId("sources"); sources.replaceChildren();
    for (const source of data.sources || []) {
      const block = el("div"); block.append(el("strong", source.provider), el("span", label(source.status)),
        el("small", tr("最近完整扫描至 ", "Last complete scan through ") + fmtTime(source.last_successful_cutoff)),
        el("small", tr("最近尝试 ", "Last attempt ") + fmtTime(source.attempted_at))); sources.append(block);
    }
    if (!sources.childElementCount) sources.append(el("p", tr("尚无完整扫描记录，不能据此判断没有新反馈。", "No scan coverage recorded; this does not establish that there are no new updates.")));
    byId("pending").textContent = tr(`${data.pending_count || 0} 条反馈尚未唯一匹配申请，不计入图表。页面是本地快照，刷新浏览器不会扫描邮箱。`, `${data.pending_count || 0} observations await a unique application match and are excluded from charts. This is a local snapshot; reloading does not scan mail.`);
  }
  function renderRecords(applications) {
    let items = drill ? applications.filter(a => drill.ids.has(a.id)) : applications;
    items = [...items].sort((a, b) => (b.entered_at || "").localeCompare(a.entered_at || "") || a.company.localeCompare(b.company));
    byId("record-title").textContent = `${drill ? drill.title : tr("申请明细", "Applications")} · ${items.length}`;
    byId("clear").hidden = !drill;
    const stateNode = byId("states"); stateNode.replaceChildren();
    [...new Set(applications.map(a => a.current))].sort().forEach(stage => {
      const ids = applications.filter(a => a.current === stage).map(a => a.id);
      stateNode.append(button(`${label(stage)} · ${ids.length}`, () => drillTo(ids, label(stage))));
    });
    const list = byId("list"); list.replaceChildren();
    page = Math.max(0, Math.min(page, Math.ceil(items.length / 20) - 1));
    for (const a of items.slice(page * 20, (page + 1) * 20)) {
      const details = el("details", null, "progress-record"), summary = el("summary");
      const title = el("div"); title.append(el("strong", a.company), el("span", a.title));
      const status = el("div"); status.append(el("strong", label(a.current)), el("small", label(a.basis)));
      const wait = terminal.has(a.current) ? tr("已结束", "Closed") : a.waiting_days == null ? tr("停留时长未知", "Age unknown") : tr(`已停留 ${Math.floor(a.waiting_days)} 天`, `${Math.floor(a.waiting_days)} days in stage`);
      summary.append(title, status, el("span", wait)); details.append(summary);
      const content = el("div", null, "progress-record-body");
      content.append(el("p", tr("投递日期：", "Submitted: ") + fmt(a.submitted_at)));
      if (a.url) {try {const url = new URL(a.url); if (["https:", "http:"].includes(url.protocol)) {const link = el("a", tr("打开岗位", "Open job")); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; content.append(link);}} catch { /* Invalid source URLs remain plain metadata. */ }}
      const timeline = el("ol", null, "progress-timeline");
      for (const event of a.timeline) {
        const row = el("li"); row.append(el("time", fmt(event.at)), el("strong", label(event.type) + (event.round ? tr(` · 第 ${event.round} 轮`, ` · Round ${event.round}`) : "")),
          el("small", event.source + (event.corrected ? tr(" · 已更正", " · Corrected") : "") + (event.time_basis === "observed" ? tr(" · 观察日期", " · Observation date") : "") + (event.scheduled_at ? tr(" · 安排于 ", " · Scheduled ") + fmtTime(event.scheduled_at) : ""))); timeline.append(row);
      }
      if (!timeline.childElementCount) content.append(el("p", tr("还没有可匹配的招聘反馈。", "No matched recruiting feedback yet.")));
      content.append(timeline); details.append(content); list.append(details);
    }
    if (!items.length) list.append(el("p", tr("此筛选下没有申请记录。", "No applications match this selection."), "progress-empty"));
    const pages = byId("pages"); pages.replaceChildren();
    if (items.length > 20) {
      const prev = button(tr("上一页", "Previous"), () => {page--; renderRecords(applications);}); prev.disabled = page === 0;
      const next = button(tr("下一页", "Next"), () => {page++; renderRecords(applications);}); next.disabled = (page + 1) * 20 >= items.length;
      pages.append(prev, el("span", `${page + 1} / ${Math.ceil(items.length / 20)}`), next);
    }
  }
  root.renderApplicationProgress = function (payload, nextLocale) {
    data = payload || {state: "not_initialized", generated_at: new Date().toISOString(), applications: [], sources: []};
    if (!data.generated_at) data = {...data, generated_at: new Date().toISOString()};
    locale = nextLocale;
    if (!mounted) mount();
    render();
  };
})(typeof window === "undefined" ? globalThis : window);
