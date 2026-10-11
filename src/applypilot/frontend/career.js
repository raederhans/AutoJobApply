/* Local career workbench. Server values enter markup only through escapeHTML. */
"use strict";
(() => {
  const BASE = "/api/career/v1";
  const $ = (id) => document.getElementById(id);
  const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (c) => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
  const e = escapeHTML;
  const segment = (value) => encodeURIComponent(String(value));
  const text = (value) => Array.isArray(value) ? value.join("、") : (value ?? "");
  const displayDate = (value) => {
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value || "") : new Intl.DateTimeFormat("zh-CN", {month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);
  };
  const state = {token:null, catalog:{companies:[],jobs:[]}, materials:[], targets:[], runs:[], target:null, diagnosis:null, progress:null, stage:"materials", file:null, polling:null, retry:null, busy:false, observedRuns:new Set(),pendingResults:new Map(),selectionGeneration:0,selectionPending:false,dialogGeneration:0,dataGeneration:0};
  const stageNames = {materials:"目标与材料",diagnosis:"差距诊断",route:"学习路线",review:"产物与评审"};
  const kindNames = {resume:"简历",resume_copy:"简历副本",project_evidence:"项目证据",generated:"生成 / 练习产物",candidate_facts:"事实记录",candidate_fact_master:"求职事实主档",project_fact_context:"项目事实与贡献范围",project_fact_master:"项目事实主档"};
  const roleNames={ux_design_research:"用户体验设计与研究",software:"软件工程",software_engineering:"软件工程",frontend:"前端开发",frontend_engineering:"前端开发",backend:"后端开发",fullstack:"全栈开发",data_analytics:"数据分析",data_analysis:"数据分析",data_science:"数据科学",machine_learning:"机器学习",ai_engineering:"AI 工程",product_management:"产品管理",business_analysis:"商业分析",technical_build:"技术开发",design:"设计",ux:"用户体验",sales_engineering:"售前技术"};
  const companyName=(id)=>state.catalog.companies.find((c)=>c.company_id===id)?.name||"公司未提供";
  const friendlyMaterialTitle=(m)=>["candidate_fact_master","project_fact_context","project_fact_master"].includes(m.kind)?kindNames[m.kind]:/^M\d+$/i.test(m.title||"")?(kindNames[m.kind]||"来源")+"材料":m.title||kindNames[m.kind]||"引用材料";
  const statusNames = {related_evidence:"有关联经历材料",transferable_evidence:"存在可迁移证据",evidence_missing:"材料待补，能力未知",needs_verification:"需要任务验证，能力未知",observed_task_gap:"已观察到任务未通过",qualification_unknown:"资格未知",unresolved:"原句范围待澄清",uncovered:"当前目录未覆盖",pending:"待开始",in_progress:"进行中",completion_reported:"已报告完成",submitted:"已提交，待评审",passed:"本任务验收通过",needs_revision:"需要修改",skipped:"已跳过",pass:"通过",fail:"未通过",not_observed:"尚未观察",failed:"未通过",artifact_review:"产物评审",self_task_review:"本人任务自评",independent_task_review:"独立任务评审",artifact:"产物",candidate:"本人任务"};
  const runStatuses = {pending:"等待运行",running:"正在处理",completed:"已完成",succeeded:"已完成",failed:"运行失败",cancelled:"已取消",interrupted:"运行中断",blocked:"需要处理"};
  const activeRun = (r) => ["pending","running"].includes(r.status);
  const goodRun = (r) => ["completed","succeeded"].includes(r.status);
  const runId = (r) => r.job_id || r.run_id || r.id;
  function observeHistoricalRuns() {
    state.pendingResults.clear();
    state.runs.filter((run)=>!activeRun(run)).forEach((run)=>state.observedRuns.add(runId(run)));
  }
  function priorityReason(reason) {
    const [kind,...detail]=String(reason).split(":");
    if(kind==="requirement_strength")return {required:"岗位必要条件",preferred:"岗位优先条件",unspecified:"岗位条件强度未明确"}[detail.join(":")]||"按岗位原句的条件强度排序";
    if(kind==="qualification_feasibility_first")return "先核对资格与申请时间，资格独立于技能学习";
    if(kind==="user_preferred_skill")return "优先考虑所选技能方向";
    if(kind==="evidence_bypass")return "相同前置任务已有兼容的本人验收观察，免于重复练习";
    if(["observed_failure","existing_evidence_first","transitive_learning_prerequisite","evidence_collection_reviewed"].includes(kind))return detail.join(":")||"按已有任务观察安排";
    return "按条件与任务依赖安排";
  }
  const materialName = (id, materials=state.materials) => {const m=materials.find((m)=>m.id===id);return m?friendlyMaterialTitle(m):"引用材料";};
  const badge = (value) => `<span class="tag ${["passed","pass","related_evidence"].includes(value)?"good":["fail","failed","needs_revision","observed_task_gap"].includes(value)?"bad":["qualification_unknown","unresolved","skipped"].includes(value)?"warn":""}">${e(statusNames[value] || value || "待核对")}</span>`;
  function safeURL(value) { try { const u = new URL(value); return u.protocol === "https:" && !u.username && !u.password ? u.href : null; } catch { return null; } }
  function remember() { try { localStorage.setItem("career-workbench-selection", JSON.stringify({targetId:state.target?.target_id,routeId:state.progress?.route?.route_id,stage:state.stage})); } catch { /* Storage may be unavailable. */ } }
  function savedSelection() { try { return JSON.parse(localStorage.getItem("career-workbench-selection") || "{}"); } catch { return {}; } }
  function selectionContext() {return {generation:state.selectionGeneration,targetId:state.target?.target_id,routeId:state.progress?.route?.route_id,dialogGeneration:state.dialogGeneration};}
  function currentSelection(context) {return context.generation===state.selectionGeneration&&context.targetId===state.target?.target_id&&context.routeId===state.progress?.route?.route_id&&!state.selectionPending;}
  function closeDialogs() {state.dialogGeneration++;$("task-dialog").close();$("source-dialog").close();}
  function beginSelection() {state.selectionPending=true;closeDialogs();return ++state.selectionGeneration;}
  const currentGeneration=(generation)=>generation===state.selectionGeneration;
  function feedback(message, {error=false, action=null, label=""}={}) {
    const box=$("feedback"); box.hidden=false; box.className=error?"error":""; box.replaceChildren();
    const span=document.createElement("span"); span.textContent=message; box.append(span);
    if(action) { const button=document.createElement("button");button.type="button";button.className="button subtle";button.textContent=label;button.addEventListener("click",action);box.append(button); }
    const dialog=$("task-dialog");
    if(dialog.open) {
      let inline=dialog.querySelector("[data-dialog-feedback]");
      if(!inline) {inline=document.createElement("div");inline.dataset.dialogFeedback="";inline.setAttribute("role","status");dialog.insertBefore(inline,$("task-dialog-content"));}
      inline.className="notice "+(error?"":"info");inline.replaceChildren();
      const messageSpan=document.createElement("span");messageSpan.textContent=message;inline.append(messageSpan);
      if(action){const button=document.createElement("button");button.type="button";button.className="button subtle";button.textContent=label;button.addEventListener("click",action);inline.append(button);}
    }
  }
  async function api(path, body) {
    const options={headers:{Accept:"application/json"},cache:"no-store"};
    if(body!==undefined) { options.method="POST";options.headers["Content-Type"]="application/json";options.headers["X-Career-Token"]=state.token;options.body=JSON.stringify(body); }
    let response;
    try { response=await fetch(BASE+path,options); } catch { const err=new Error("连接中断。请检查本地服务，再重试这次操作。");err.network=true;throw err; }
    let data;
    try { data=await response.json(); } catch { const err=new Error("本地服务未返回有效数据。请刷新或检查服务状态。");err.network=response.ok;throw err; }
    if(!response.ok) { const err=new Error(data.error?.message || "操作未完成，请核对输入。");err.status=response.status;err.code=data.error?.code;throw err; }
    return data;
  }
  function showError(err, retry) {
    if(err.status===409) { state.retry=null;feedback("数据已发生变化，本次操作没有重新提交。刷新后请核对最新内容。",{error:true,action:()=>{$("task-dialog").close();refresh();},label:"刷新最新数据"});return; }
    if(err.status===403&&err.code==="session_required") {feedback("页面会话已变化，请刷新后核对再操作。",{error:true,action:()=>{$("task-dialog").close();refresh();},label:"刷新会话"});return;}
    feedback(err.message || "操作未完成。",{error:true,...(err.network&&retry?{action:retry,label:"重试本次操作"}:{})});
  }
  // A retry retains the exact intent payload and request key; conflicts require a fresh user action.
  async function intent(path, body, onSuccess, button) {
    if(state.busy||state.selectionPending) return;
    const context=selectionContext();
    const payload=JSON.parse(JSON.stringify({...body,request_key:crypto.randomUUID()}));
    const perform=async()=>{
      if(state.busy||!currentSelection(context)) return;
      state.busy=true; if(button) button.disabled=true;
      try {const result=await api(path,payload);if(!currentSelection(context))return;state.retry=null;$("feedback").hidden=true;await onSuccess(result,context);}
      catch(err) {if(currentSelection(context)){state.retry=err.network?perform:null;showError(err,perform);}}
      finally {state.busy=false;if(button?.isConnected) button.disabled=false;}
    };
    await perform();
  }
  function showStage(stage, focus=false) {
    state.stage=stage;
    document.querySelectorAll("[data-stage]").forEach((button)=>{if(button.dataset.stage===stage)button.setAttribute("aria-current","step");else button.removeAttribute("aria-current");});
    Object.keys(stageNames).forEach((name)=>{$("stage-"+name).hidden=name!==stage;});
    $("page-title").textContent=stageNames[stage];remember();if(focus)$("main").focus();
  }
  function checkedMaterials(container) {return [...$(container).querySelectorAll("input:checked")].map((input)=>input.value);}
  function materialPicks(selected=[]) {
    return state.materials.length ? state.materials.map((m)=>`<label class="pick"><input type="checkbox" value="${e(m.id)}" ${selected.includes(m.id)?"checked":""}><span>${e(friendlyMaterialTitle(m))}<small>${e(kindNames[m.kind]||m.kind)} · ${e(m.claim_count??0)} 条记录</small></span></label>`).join("") : '<p class="hint">尚无材料。请在右侧添加可回源原文。</p>';
  }
  function renderMaterials(reset=false) {
    const chosen=reset?[]:checkedMaterials("target-materials");
    $("target-materials").innerHTML=materialPicks(chosen.length?chosen:state.target?.material_ids||[]);
    $("supplement-materials").innerHTML=materialPicks();
    $("material-count").textContent=`${state.materials.length} 项`;
    $("material-library").innerHTML=state.materials.length?state.materials.map((m)=>`<article class="material-row"><div class="material-row-head"><span>${e(friendlyMaterialTitle(m))}</span><span class="tag">${e(kindNames[m.kind]||m.kind)}</span></div><p>${e(m.preview || "已保存原文，可打开核对。")}</p><button type="button" class="text-button" data-material="${e(m.id)}">查看原文 ↗</button></article>`).join(""):'<div class="empty"><h3>建立你的证据库</h3><p>先加入一份材料，开始核对岗位条件。</p></div>';
    $("supplement-panel").hidden=!state.target;
  }
  function renderCatalog() {
    const company=$("company").value;
    $("company").innerHTML='<option value="">全部公司</option>'+state.catalog.companies.map((c)=>`<option value="${e(c.company_id)}">${e(c.name)}</option>`).join("");
    if([...$("company").options].some((o)=>o.value===company))$("company").value=company;
    renderJobs();
  }
  function renderJobs() {
    const selected=$("job").value, company=$("company").value;
    const jobs=state.catalog.jobs.filter((j)=>!company || j.company_id===company);
    $("job").innerHTML='<option value="">请选择岗位</option>'+jobs.map((j)=>`<option value="${e(j.id)}">${e(j.title)} · ${e(state.catalog.companies.find((c)=>c.company_id===j.company_id)?.name || j.company_id)}</option>`).join("");
    if(jobs.some((j)=>j.id===selected))$("job").value=selected;
    renderJobContext();
  }
  function renderJobContext() {
    const job=state.catalog.jobs.find((j)=>j.id===$("job").value);
    if(!job) {$("job-context").replaceChildren();return;}
    const url=safeURL(job.url), locations=Array.isArray(job.locations)?job.locations:[];
    $("job-context").innerHTML=`${e(locations.length?locations.slice(0,3).join("、"):text(job.locations)||"地点未提供")}${locations.length>3?` 等 ${e(locations.length)} 个地点<details><summary>查看全部工作地点</summary><p>${e(locations.join("；"))}</p></details>`:""} · ${e(job.native_level||"级别未提供")} · ${e(job.employment_type||"雇用类型未提供")}<br>样本记录：${e(job.observed_at||"时间未提供")}${url?` · <a href="${e(url)}" target="_blank" rel="noopener noreferrer">查看职位来源 ↗</a>`:""}${job.historical?"<br>历史职位样本，仅用于条件分析。":""}`;
  }
  function renderTargets() {
    const counts=new Map();
    for(const target of state.targets){const key=target.label+"\u0000"+target.company_id;counts.set(key,(counts.get(key)||0)+1);}
    $("current-target").innerHTML='<option value="">建立或选择一个目标</option>'+state.targets.map((t)=>{
      const duplicate=counts.get(t.label+"\u0000"+t.company_id)>1;
      const job=state.catalog.jobs.find((j)=>j.id===t.bindings?.job_ids?.[0]);
      return `<option value="${e(t.target_id)}">${e(t.label)} · ${e(companyName(t.company_id))}${duplicate&&job?` · ${e(job.title)}`:""}</option>`;
    }).join("");
    $("current-target").value=state.target?.target_id||"";
    $("target-context").textContent=state.target?[companyName(state.target.company_id),roleNames[state.target.role_family]||"所选职位方向",state.target.region,{full_time:"全职",fulltime:"全职",internship:"实习",contract:"合同制",part_time:"兼职"}[state.target.employment_track]||state.target.employment_track].filter(Boolean).map(text).join(" · "):"先选择岗位与材料";
    $("supplement-panel").hidden=!state.target;
  }
  function staleNotice(stale, kind) {return stale?`<div class="notice"><strong>${kind}已过期</strong><p>目标、材料或评审发生变化。当前内容保留为历史观察，请使用最新输入重新诊断。</p><button type="button" class="button subtle" data-action="diagnose">以当前目标重新诊断</button></div>`:"";}
  function renderDiagnosis() {
    const box=$("diagnosis-content"), d=state.diagnosis;
    if(!state.target) {box.innerHTML='<div class="empty"><h3>先建立分析目标</h3><p>选择岗位样本和材料，再核对条件。</p><button type="button" class="button secondary" data-go="materials">选择目标与材料</button></div>';return;}
    if(!d) {box.innerHTML='<div class="empty"><h3>准备开始条件诊断</h3><p>将岗位原句与所选材料逐项关联，保留未知项。</p><button type="button" class="button primary" data-action="diagnose">生成差距诊断</button></div>';return;}
    const rows=d.requirements||[], counts=d.summary?.status_counts||{}, linked=(counts.related_evidence||0)+(counts.transferable_evidence||0), materials=d.evidence?.materials||state.materials;
    const refs=(r)=> (r.evidence_refs||[]).flatMap((ref)=> (ref.source_refs||[]).map((sr)=>({...ref,...sr})));
    const requirement=(r)=> {
      const url=safeURL(r.job?.url), sources=refs(r);
      const evidenceHTML=sources.length?sources.map((ref)=>`<div class="source-item"><strong>${e(materialName(ref.material_id,materials))}</strong>${ref.line_start?` · 第 ${e(ref.line_start)}${ref.line_end?`–${e(ref.line_end)}`:""} 行`:""}<blockquote>${e(ref.quote||"未提供摘录")}</blockquote><p>贡献范围：${e(ref.ownership||"未记录")}</p>${ref.does_not_prove?.length?`<p>${e(text(ref.does_not_prove))}</p>`:""}<button class="text-button" type="button" data-material="${e(ref.material_id)}">打开材料全文 ↗</button></div>`).join(""):'<p>暂无可引用的材料片段，能力保持未知。</p>';
      const observations=(r.task_observations||[]).map((o)=>`<p>${e(statusNames[o.method]||o.method)} · ${e(o.reviewer)} · ${e(statusNames[o.outcome]||o.outcome)}</p><p>情境：${e(o.context)}；协助：${e(o.assistance)}</p>${(o.acceptance_results||[]).map((c)=>`<p>${e(c.criterion)}：${badge(c.status)}</p>${(c.evidence_refs||[]).map((ref)=>`<blockquote>${e(ref.quote)}</blockquote>`).join("")}`).join("")}<p>${e(text(o.limits))}</p>`).join("");
      return `<article class="requirement"><div class="requirement-head"><h4>${e(r.quote||"岗位条件")}</h4>${badge(r.status)}</div><p class="next-action">下一步：${e(r.next_action?.reason_zh||"核对原始条件与材料")}</p><details class="requirement-detail"><summary>查看判读依据与来源 · ${e(sources.length)} 条材料引用</summary><p>${e(r.reason_zh||"")}</p><h4>职位原句</h4><blockquote>${e(r.quote)}</blockquote><p>${e(r.job?.title||"职位样本")} · ${e(r.job?.observed_at||"记录时间未提供")}${r.job?.historical?" · 历史职位":""}</p><p>${e(r.source?.section||"")}${r.source?.line_start?` · 第 ${e(r.source.line_start)}${r.source.line_end?`–${e(r.source.line_end)}`:""} 行`:""}</p>${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">查看职位来源 ↗</a>`:""}<div class="sources"><h4>材料依据</h4>${evidenceHTML}</div>${(r.model_suggestions||[]).length?`<h4>模型建议（未改变规则状态）</h4>${r.model_suggestions.map((s)=>`<p>${e(s.reason)}</p>`).join("")}`:""}${observations?`<h4>限定任务观察</h4>${observations}`:""}</details></article>`;
    };
    const quals=rows.filter((r)=>r.kind==="qualification"||r.kind==="administrative"), others=rows.filter((r)=>!quals.includes(r));
    box.innerHTML=staleNotice(d.stale,"诊断")+`<div class="metrics"><div class="metric"><strong>${e(rows.length)}</strong>岗位条件</div><div class="metric"><strong>${e(linked)}</strong>有材料关联</div><div class="metric"><strong>${e(counts.observed_task_gap||0)}</strong>观察到的任务差距</div><div class="metric"><strong>未测</strong>通用技能等级</div></div><div class="notice info">书面材料只证明记录可回源；相关经历、课程完成与任务通过分别核对。缺少观察不表示能力为零。</div><div class="actions"><button class="button primary" type="button" data-action="plan" ${d.stale?"disabled":""}>生成学习路线 →</button><button type="button" class="button subtle" data-action="diagnose">重新诊断</button></div><details class="qualification-group"><summary>资格与行政条件 · ${e(quals.length)} 项 <span class="tag">独立核对</span></summary><p class="hint">资格独立于技能学习；当前样本的材料关联不构成资格结论。</p>${quals.map(requirement).join("")||'<p class="hint">当前样本未提取资格条件；这不代表资格已经确认。</p>'}</details><div class="section-head"><h3>技能、职责与其他条件</h3></div>${others.map(requirement).join("")||'<p class="hint">没有其他已提取条件。</p>'}<section class="panel"><h3>涉及的技能主题</h3><p class="hint">所有级别保持未测；材料关联不转化为技能等级。</p><div class="skill-list">${(d.skills||[]).map((s)=>`<span class="skill-chip">${e(s.name_zh||s.name||"技能主题")}<span>未测</span></span>`).join("")}</div><details><summary>解读边界</summary><ul>${(d.boundaries||[]).map((b)=>`<li>${e(b)}</li>`).join("")}</ul></details></section>`;
  }
  function taskName(id) {return state.progress?.tasks.find((t)=>t.task_id===id)?.title||"前置任务";}
  function renderRoute() {
    const box=$("route-content"), p=state.progress;
    if(!p) {box.innerHTML='<div class="empty"><h3>学习路线从诊断开始</h3><p>先查看岗位条件与材料，再生成按依赖排序的任务。</p><button class="button secondary" type="button" data-go="diagnosis">查看差距诊断</button></div>';renderReview();return;}
    const route=p.route, tasks=p.tasks||[], history=p.historical_routes||[];
    box.innerHTML=staleNotice(p.stale,"学习路线")+`<div class="route-toolbar"><label for="route-history">路线记录</label><select id="route-history" aria-label="选择历史路线">${history.map((r,index)=>`<option value="${e(r.route_id)}" ${r.route_id===route.route_id?"selected":""}>${e(r.created_at?displayDate(r.created_at):`路线 ${index+1}`)}${r.parent_route_id?" · 后续路线":" · 初始路线"}</option>`).join("")||`<option value="${e(route.route_id)}">当前路线</option>`}</select><button type="button" class="button subtle" data-action="rediagnose">重新诊断并衔接路线</button></div><div class="route-summary"><strong>${e(tasks.length)} 个任务 · ${e(tasks.filter((t)=>t.state==="passed").length)} 个已通过验收</strong><p>先完成前置项，再推进目标任务。${route.budget?.mode==="budgeted"?"此路线使用已记录的时间预算。":"未设置每周时间预算，当前只提供优先顺序。"}估时来自试点量规，未测量本人能力。</p></div>${p.diagnosis_refresh_needed?'<div class="notice info">评审结果已变化。可重新诊断，让新的任务观察进入下一条路线。</div>':""}<div class="task-list">${tasks.map((t,index)=>`<article class="task-card"><span class="task-number">${String(index+1).padStart(2,"0")}</span><div><div class="task-head"><h3>${e(t.title)}</h3>${badge(t.state)}</div><div class="task-meta"><span class="tag">优先顺序 ${index+1}</span><span>${e({evidence_collection:"材料整理",verification:"任务验证",practice:"定向练习",qualification:"资格核对",qualification_review:"资格核对"}[t.kind]||"学习任务")}</span><span>${t.estimate_hours?`参考估时 ${e(t.estimate_hours.min)}–${e(t.estimate_hours.max)} 小时`:"未提供估时"}</span><span>${t.ready?"前置项已满足":t.blocked_by?.length?"等待前置任务":"当前状态已记录"}</span></div><p><strong>交付：</strong>${e(t.deliverable)}</p>${t.blocked_by?.length?`<p class="hint">前置任务：${t.blocked_by.map((id)=>e(taskName(id))).join("；")}</p>`:""}<details><summary>优先原因、任务情境与验收</summary><h4>为什么安排在这里</h4><ul>${(t.priority_reasons||[]).map((reason)=>`<li>${e(priorityReason(reason))}</li>`).join("")||"<li>按条件与任务前置关系安排。</li>"}</ul><dl><dt>任务情境</dt><dd>${e(t.context)}</dd><dt>允许协助</dt><dd>${e(t.assistance_policy)}</dd><dt>验收标准</dt><dd><ul>${(t.acceptance||[]).map((c)=>`<li>${e(c)}</li>`).join("")}</ul></dd><dt>观察限制</dt><dd>${e(text(t.limits))}</dd></dl></details><details><summary>参考资料${t.resources?.length?` · ${e(t.resources.length)} 项`:""}</summary>${(t.resources||[]).map((r)=>{const url=safeURL(r.url);return `<div class="resource">${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">${e(r.title)} ↗</a>`:`<strong>${e(r.title)}</strong>`}<small>官方资料 · 核对日期 ${e(r.verified_at||"未提供")} · ${e(r.language||"")}</small><p>使用基础：${e(r.foundation)}</p><p>${e(r.limits)}</p></div>`;}).join("")||'<p class="hint">暂无已核实且匹配的官方资料。</p>'}<p class="hint">${e(t.resources_note)}</p></details><div class="actions"><button class="button secondary" type="button" data-task-action="started" data-task="${e(t.task_id)}" ${!t.ready||p.stale?"disabled":""}>开始任务</button><button class="button subtle" type="button" data-task-action="marked_done" data-task="${e(t.task_id)}" ${!t.ready||p.stale?"disabled":""}>记录完成</button><button class="button subtle" type="button" data-task-action="submit" data-task="${e(t.task_id)}" ${!t.ready||p.stale?"disabled":""}>提交产物</button><button class="text-button" type="button" data-task-action="skipped" data-task="${e(t.task_id)}" ${p.stale?"disabled":""}>跳过</button><button class="text-button" type="button" data-task-action="reopened" data-task="${e(t.task_id)}" ${p.stale?"disabled":""}>重新打开</button></div>${t.state==="completion_reported"?'<p class="hint">完成记录不表示验收通过，仍需提交与逐项评审。</p>':""}</div></article>`).join("")}</div><details class="panel route-history-panel"><summary>路线边界与记录</summary><ul>${[...(p.boundaries||[]),...(route.boundaries||[])].map((b)=>`<li>${e(b)}</li>`).join("")}</ul>${(p.history||[]).slice().reverse().map((event)=>`<p class="hint">${e(taskName(event.task_id))} · ${e({started:"开始",marked_done:"记录完成",skipped:"跳过",reopened:"重新打开",submitted:"提交产物",assessment_recorded:"记录评审"}[event.event_type]||"任务更新")} · ${e(event.created_at||"")}${event.payload?.reason?`<br>${e(event.payload.reason)}`:""}</p>`).join("")}</details>`;
    renderReview();
  }
  function renderReview() {
    const box=$("review-content"), p=state.progress;
    if(!p) {box.innerHTML='<div class="empty"><h3>先选择一条学习路线</h3><p>任务交付与评审会在这里集中展示。</p><button type="button" class="button secondary" data-go="route">查看学习路线</button></div>';return;}
    const tasks=p.tasks.filter((t)=>t.submission||t.assessment||t.state==="completion_reported");
    box.innerHTML=staleNotice(p.stale,"学习路线")+'<div class="notice info">默认评审对象为产物。产物通过不会提升通用技能等级，也不自动证明本人独立完成任务。</div>'+ (tasks.length?tasks.map((t)=>`<article class="review-card"><div class="task-head"><h3>${e(t.title)}</h3>${badge(t.state)}</div><p>${e(t.deliverable)}</p>${t.assessment?`<div class="review-result"><p><strong>${e(statusNames[t.assessment.method]||t.assessment.method)}</strong> · 对象：${e(statusNames[t.assessment.subject]||t.assessment.subject)} · 评审者：${e(t.assessment.reviewer)}</p><p>情境：${e(t.assessment.context)}<br>实际协助：${e(t.assessment.assistance)}</p>${t.assessment_current===false?'<p class="notice">此为历史评审；当前输入下尚未重新核对。</p>':""}${(t.assessment.acceptance_results||[]).map((c)=>`<div class="criterion-result">${e(c.criterion)} ${badge(c.status)}${(c.evidence_refs||[]).map((r)=>`<blockquote>${e(r.quote)}</blockquote>`).join("")}</div>`).join("")}<small>结论只限所验收任务；通用技能等级仍为未测。</small></div>`:""}<div class="actions">${t.submission?`<button class="button primary" type="button" data-task-action="review" data-task="${e(t.task_id)}" ${p.stale||t.blocked_by?.length?"disabled":""}>逐条评审</button><button class="button subtle" type="button" data-task-action="view-submission" data-task="${e(t.task_id)}">查看已提交产物</button>`:`<button class="button secondary" type="button" data-task-action="submit" data-task="${e(t.task_id)}" ${!t.ready||p.stale?"disabled":""}>提交产物</button>`}<button class="text-button" type="button" data-task-action="reopened" data-task="${e(t.task_id)}" ${p.stale?"disabled":""}>重新打开任务</button></div></article>`).join(""):'<div class="empty"><h3>让学习留下产物</h3><p>在路线中选择任务，完成后提交材料并逐项记录观察。</p><button type="button" class="button secondary" data-go="route">回到任务路线</button></div>');
  }
  function renderRuns() {
    const box=$("run-region"), visible=state.runs.filter((r)=>!state.target||!r.result_refs?.target_id||r.result_refs.target_id===state.target.target_id);
    const alerts=visible.filter((r)=>!goodRun(r)&&r.status!=="cancelled");
    const history=visible.filter((r)=>goodRun(r)||r.status==="cancelled");
    box.hidden=!visible.length;
    const row=(r)=>`<div class="run-row"><div>${activeRun(r)?'<span class="spinner" aria-hidden="true"></span>':""}<strong>${e(r.cancel_requested?"正在取消":runStatuses[r.status]||"状态待核对")}</strong> · ${e({diagnose:"差距诊断",diagnosis:"差距诊断",route:"学习路线",plan:"学习路线",rediagnose:"更新诊断与路线",supplement:"补充材料"}[r.operation||r.kind||r.operation_type]||"职业分析")}<p>${e(r.error?.message||(r.error?({interrupted:"运行已中断，可恢复处理。",input_changed:"输入已经变化，请刷新后重新核对。",cancelled:"运行已取消。"}[r.error]||"处理未完成，请核对目标与材料后恢复。"):""))}</p></div><div class="run-actions">${activeRun(r)?`<button class="text-button" type="button" data-run-action="cancel" data-run="${e(runId(r))}" ${r.cancel_requested?"disabled":""}>取消运行</button>`:""}${["interrupted","failed","blocked","cancelled"].includes(r.status)?`<button class="text-button" type="button" data-run-action="resume" data-run="${e(runId(r))}">恢复运行</button>`:""}${goodRun(r)?`<button class="text-button" type="button" data-run-action="view" data-run="${e(runId(r))}">查看结果 →</button>`:""}</div></div>`;
    box.innerHTML=`<div class="run-box">${alerts.length?`<div class="run-head"><strong>后台处理</strong><span class="muted">可继续浏览材料</span></div>${alerts.map(row).join("")}`:""}${history.length?`<details class="run-history"><summary>后台记录（${e(history.length)}）</summary>${history.map(row).join("")}</details>`:""}</div>`;
  }
  // Read a complete selection without exposing any partial response to actions or renderers.
  function validateSelection(bundle) {
    const id=bundle.target?.target_id;
    if(bundle.diagnosis&&(bundle.diagnosis.target_id||bundle.diagnosis.target?.target_id)!==id)throw new Error("诊断与所选目标不一致，请重新选择目标。");
    if(bundle.progress&&bundle.progress.route?.target_id!==id) {
      const error=new Error("该路线不属于所选目标，请重新选择路线。");error.status=404;throw error;
    }
    return bundle;
  }
  async function readTargetBundle(id, {routeId=null,diagnosisId=null,generation}={}) {
    const response=id?await api("/targets/"+segment(id)):null;
    if(!currentGeneration(generation))return null;
    const target=response?.target||response;
    const diagnosisRef=diagnosisId||target?.latest_diagnosis_id, routeRef=routeId||target?.latest_route_id;
    const [diagnosis,progress]=await Promise.all([
      diagnosisRef?api("/diagnoses/"+segment(diagnosisRef)):null,
      routeRef?api("/routes/"+segment(routeRef)):null,
    ]);
    return currentGeneration(generation)?validateSelection({target,diagnosis,progress}):null;
  }
  function publishSelection(bundle, generation, extra={}) {
    if(!bundle||!currentGeneration(generation))return false;
    validateSelection(bundle);
    if(bundle.target?.target_id!==state.target?.target_id||bundle.progress?.route?.route_id!==state.progress?.route?.route_id)closeDialogs();
    Object.assign(state,extra,bundle,{selectionPending:false});
    renderTargets();renderMaterials(true);renderDiagnosis();renderRoute();renderRuns();remember();
    return true;
  }
  function selectionFailed(generation, error) {
    if(!currentGeneration(generation))return false;
    state.selectionPending=false;renderTargets();throw error;
  }
  async function loadTarget(id, {routeId=null}={}) {
    const generation=beginSelection();
    try {return publishSelection(await readTargetBundle(id,{routeId,generation}),generation);}
    catch(error) {return selectionFailed(generation,error);}
  }
  async function loadRoute(id) {
    const target=state.target, diagnosis=state.diagnosis, generation=beginSelection();
    try {
      const progress=await api("/routes/"+segment(id));
      return publishSelection({target,diagnosis,progress},generation);
    } catch(error) {return selectionFailed(generation,error);}
  }
  async function loadBootstrap(restoreStage=false) {
    const saved=savedSelection(), selected=$("current-target").value||state.target?.target_id||saved.targetId;
    const selectedRoute=selected===state.target?.target_id?state.progress?.route?.route_id:!state.target&&selected===saved.targetId?saved.routeId:null;
    const generation=beginSelection();
    try {
      const data=await api("/bootstrap");
      if(!currentGeneration(generation))return false;
      const targets=data.targets||[], id=targets.some((t)=>t.target_id===selected)?selected:targets[0]?.target_id;
      let bundle;
      try {bundle=await readTargetBundle(id,{routeId:id===selected?selectedRoute:null,generation});}
      catch(error) {if(currentGeneration(generation)&&selectedRoute&&error.status===404)bundle=await readTargetBundle(id,{generation});else throw error;}
      if(!publishSelection(bundle,generation,{token:data.token,catalog:data.catalog||{companies:[],jobs:[]},materials:data.materials||[],targets,runs:data.runs||[]}))return false;
      observeHistoricalRuns();$("workspace-label").textContent=data.workspace_label||"本地工作空间";renderCatalog();
      showStage(restoreStage&&stageNames[saved.stage]?saved.stage:state.stage);schedulePoll();
      if(!restoreStage)feedback("数据已刷新。");
      return true;
    } catch(error) {
      if(currentGeneration(generation)){state.selectionPending=false;renderTargets();showError(error,refresh);}
      return false;
    }
  }
  async function refresh() {return loadBootstrap();}
  async function resultOfRun(run, navigate=false) {
    const refs=run.result_refs||{}, selectedId=state.target?.target_id;
    if(!navigate&&(state.busy||state.selectionPending||(refs.target_id&&selectedId&&refs.target_id!==selectedId)))return false;
    const generation=navigate?beginSelection():state.selectionGeneration, dataGeneration=state.dataGeneration;
    try {
      const targets=await api("/targets");
      if(!currentGeneration(generation))return false;
      const bundle=await readTargetBundle(refs.target_id||selectedId,{routeId:refs.route_id||null,diagnosisId:refs.diagnosis_id||null,generation});
      if(!navigate&&(state.busy||dataGeneration!==state.dataGeneration))return false;
      if(!publishSelection(bundle,generation,{targets:targets.targets||[]}))return false;
      if(navigate)showStage(refs.route_id?"route":"diagnosis",true);
      return true;
    } catch(error) {
      if(currentGeneration(generation)){state.selectionPending=false;renderTargets();showError(error);}
      return false;
    }
  }
  function schedulePoll() {
    clearTimeout(state.polling);
    if(!state.runs.some(activeRun)&&!state.pendingResults.size)return;
    state.polling=setTimeout(pollRuns,1800);
  }
  async function pollRuns() {
    try {
      const previouslyActive=new Set(state.runs.filter(activeRun).map(runId));
      const data=await api("/runs");state.runs=data.runs||[];renderRuns();
      for(const run of state.runs) {
        const id=runId(run);
        if(goodRun(run)&&previouslyActive.has(id)&&!state.observedRuns.has(id))state.pendingResults.set(id,run);
      }
      // Runs arrive newest first. Load one current result, so an older completion cannot replace it.
      const newest=state.runs.find((run)=>state.pendingResults.has(runId(run))&&(!state.target||!run.result_refs?.target_id||run.result_refs.target_id===state.target.target_id));
      if(newest) {
        const context=selectionContext(), applied=await resultOfRun(newest);
        if(!applied&&currentSelection(context)){schedulePoll();return;}
        if(applied)feedback("后台处理已完成，结果已更新。");
      }
      for(const id of state.pendingResults.keys())state.observedRuns.add(id);
      state.pendingResults.clear();schedulePoll();
    } catch(err) {showError(err,pollRuns);}
  }
  async function queueResult(data) {
    const run=data.run||data;
    state.runs=[run,...state.runs.filter((r)=>runId(r)!==runId(run))];renderRuns();
    if(goodRun(run)) {if(await resultOfRun(run,true))feedback("后台处理已完成，结果已更新。");state.observedRuns.add(runId(run));} else {
      feedback(run.cancel_requested?"已请求取消，等待运行确认。":run.status==="cancelled"?"运行已取消。":activeRun(run)?"后台处理已开始，可继续浏览。完成后将加载结果。":"运行未完成，可查看状态并恢复处理。");schedulePoll();
    }
  }
  async function viewMaterial(id) {
    if(state.selectionPending)return;
    const context=selectionContext();closeDialogs();const dialogGeneration=state.dialogGeneration;
    try {const material=state.diagnosis?.evidence?.materials?.find((m)=>m.id===id)||await api("/materials/"+segment(id));if(!currentSelection(context)||dialogGeneration!==state.dialogGeneration)return;$("source-title").textContent=friendlyMaterialTitle(material);$("source-text").textContent=material.text||"未提供原文";$("source-dialog").showModal();}catch(err){if(currentSelection(context)&&dialogGeneration===state.dialogGeneration)showError(err);}
  }
  function openTask(title, html) {$("task-dialog").querySelector("[data-dialog-feedback]")?.remove();$("task-dialog-title").textContent=title;$("task-dialog-content").innerHTML=html;$("task-dialog").showModal();}
  async function updateProgress(data, context=selectionContext()) {
    if(!currentSelection(context))return false;
    const progress=data.progress||data, diagnosis=state.diagnosis?await api("/diagnoses/"+segment(state.diagnosis.diagnosis_id)):null;
    if(!currentSelection(context))return false;
    validateSelection({target:state.target,progress,diagnosis});
    Object.assign(state,{progress,diagnosis});
    state.dataGeneration++;
    if(context.dialogGeneration===state.dialogGeneration)$("task-dialog").close();
    renderRoute();renderDiagnosis();remember();feedback("任务记录已保存。");return true;
  }
  async function taskAction(action, id) {
    if(state.selectionPending)return;
    const task=state.progress?.tasks.find((t)=>t.task_id===id),routeId=state.progress?.route.route_id;if(!task)return;
    const context=selectionContext();closeDialogs();const dialogGeneration=state.dialogGeneration;
    const currentTaskDialog=()=>currentSelection(context)&&dialogGeneration===state.dialogGeneration;
    const endpoint="/routes/"+segment(routeId), revision=state.progress.revision;
    if(["started","marked_done","skipped","reopened"].includes(action)) {
      const labels={started:"开始任务",marked_done:"记录完成",skipped:"跳过任务",reopened:"重新打开任务"};
      openTask(labels[action],`<p>${e(task.title)}</p><p class="hint">${action==="marked_done"?"记录完成不表示验收通过。请继续提交产物并逐项评审。":"记录本次原因，保留可追溯的任务进度。"}</p><form id="event-form"><label for="event-reason">原因或进展说明</label><textarea id="event-reason" rows="4" required></textarea><button class="button primary" type="submit">保存记录</button></form>`);
      $("event-form").addEventListener("submit",async(event)=>{event.preventDefault();if(!currentTaskDialog())return;await intent(endpoint+"/events",{task_id:id,event:action,reason:$("event-reason").value,expected_revision:revision},updateProgress,event.submitter);});return;
    }
    if(action==="submit") {
      openTask("提交任务产物",`<h3>${e(task.title)}</h3><p class="hint">提交前确认材料来自本次任务。需要新增材料时，可先到材料库保存。</p><form id="submission-form"><fieldset><legend>选择产物材料</legend><div class="material-picks" id="submission-materials">${materialPicks()}</div></fieldset><button class="button primary" type="submit">提交所选产物</button></form>`);
      $("submission-form").addEventListener("submit",async(event)=>{event.preventDefault();if(!currentTaskDialog())return;const ids=checkedMaterials("submission-materials");if(!ids.length){feedback("请至少选择一项产物材料。",{error:true});return;}await intent(endpoint+"/submissions",{task_id:id,material_ids:ids,expected_revision:revision},updateProgress,event.submitter);});return;
    }
    if(action==="review"||action==="view-submission") {
      try {
        const submission=await api(endpoint+"/tasks/"+segment(id)+"/submission"), materials=submission.materials||[];
        if(!currentTaskDialog())return;
        const sources=`<div class="review-sources">${materials.map((m)=>`<details><summary>${e(friendlyMaterialTitle(m))} · ${e(kindNames[m.kind]||m.kind)} · 展开原文</summary><pre>${e(m.text)}</pre></details>`).join("")}</div>`;
        if(action==="view-submission"){openTask("已提交的任务产物",sources);return;}
        const options=materials.map((m)=>`<option value="${e(m.id)}">${e(friendlyMaterialTitle(m))}</option>`).join("");
        openTask("逐条记录验收观察",`<h3>${e(task.title)}</h3><p class="hint">通过或未通过必须引用已提交产物的原句。默认“尚未观察”，不会自动判为通过。</p>${sources}<form id="review-form"><div class="form-row"><div><label for="review-method">评审方式</label><select id="review-method"><option value="artifact_review">产物评审</option><option value="self_task_review">本人任务自评</option><option value="independent_task_review">独立任务评审</option></select></div><div><label for="review-subject">评审对象</label><select id="review-subject"><option value="artifact">产物</option><option value="candidate">本人任务</option></select></div></div><label for="reviewer">评审者</label><input id="reviewer" required placeholder="填写评审者姓名或本人"><label for="review-context">实际任务情境</label><textarea id="review-context" rows="3" required>${e(task.context)}</textarea><label for="review-assistance">实际使用的协助</label><textarea id="review-assistance" rows="3" required placeholder="记录本人、团队与工具的实际协助；如无协助请明确填写。"></textarea>${(task.acceptance||[]).map((criterion,index)=>`<fieldset class="criterion" data-criterion-index="${index}"><legend>${e(criterion)}</legend><label for="criterion-status-${index}">本项观察</label><select id="criterion-status-${index}" class="criterion-status"><option value="not_observed">尚未观察</option><option value="pass">通过</option><option value="fail">未通过</option></select><label for="criterion-material-${index}">来源材料</label><select id="criterion-material-${index}" class="criterion-material"><option value="">选择已提交的材料</option>${options}</select><label for="criterion-quote-${index}">支持本项判断的原句</label><textarea id="criterion-quote-${index}" class="criterion-quote" rows="3" placeholder="从上述原文逐字摘录"></textarea></fieldset>`).join("")}<div class="notice info">评审结论只限本任务与实际情境。产物验收不会提升通用技能等级。</div><button class="button primary" type="submit">保存逐项评审</button></form>`);
        const method=$("review-method"),subject=$("review-subject");subject.disabled=true;
        method.addEventListener("change",()=>{subject.disabled=method.value==="artifact_review";if(subject.disabled)subject.value="artifact";});
        $("review-form").addEventListener("submit",async(event)=>{
          event.preventDefault();if(!currentTaskDialog())return;const results=[];
          for(const [index,criterion] of task.acceptance.entries()) {
            const status=$("criterion-status-"+index).value, materialId=$("criterion-material-"+index).value, quote=$("criterion-quote-"+index).value;
            if(status!=="not_observed"&&(!materialId||!quote.trim())){feedback("每条已观察的验收项都需要选择材料并引用原句。",{error:true});$("criterion-quote-"+index).focus();return;}
            if(quote&&(!materialId||!materials.find((m)=>m.id===materialId)?.text.includes(quote))){feedback("引用须逐字存在于所选的已提交产物中。",{error:true});$("criterion-quote-"+index).focus();return;}
            results.push({criterion,status,evidence_refs:quote?[{material_id:materialId,quote}]:[]});
          }
          await intent(endpoint+"/reviews",{task_id:id,expected_revision:revision,review:{method:method.value,subject:subject.value,reviewer:$("reviewer").value,context:$("review-context").value,assistance:$("review-assistance").value,acceptance_results:results}},updateProgress,event.submitter);
        });
      } catch(err){if(currentTaskDialog())showError(err);}
    }
  }
  async function handleAction(action,button) {
    if(!state.target||state.selectionPending)return;
    if(action==="diagnose")await intent("/targets/"+segment(state.target.target_id)+"/diagnoses",{expected_revision:state.target.revision},queueResult,button);
    if(action==="plan"&&state.diagnosis&&!state.diagnosis.stale)await intent("/diagnoses/"+segment(state.diagnosis.diagnosis_id)+"/routes",{expected_revision:state.target.revision},queueResult,button);
    if(action==="rediagnose"&&state.progress)await intent("/routes/"+segment(state.progress.route.route_id)+"/rediagnose",{expected_revision:state.progress.revision},queueResult,button);
  }
  document.addEventListener("click",async(event)=>{
    const button=event.target.closest("button");if(!button||button.disabled)return;
    if(button.dataset.stage)showStage(button.dataset.stage,true);
    if(button.dataset.go)showStage(button.dataset.go,true);
    if(button.dataset.close){state.dialogGeneration++;$(button.dataset.close).close();}
    if(button.dataset.material)await viewMaterial(button.dataset.material);
    if(button.dataset.action)await handleAction(button.dataset.action,button);
    if(button.dataset.taskAction)await taskAction(button.dataset.taskAction,button.dataset.task);
    if(button.dataset.runAction) {
      const run=state.runs.find((r)=>runId(r)===button.dataset.run);if(!run)return;
      if(button.dataset.runAction==="view") {try {await resultOfRun(run,true);}catch(err){showError(err);}return;}
      await intent("/runs/"+segment(runId(run))+"/"+button.dataset.runAction,{},queueResult,button);
    }
  });
  document.addEventListener("change",async(event)=>{
    if(event.target.id==="route-history")try {await loadRoute(event.target.value);}catch(err){showError(err);}
  });
  document.addEventListener("keydown",(event)=>{if(event.key==="Escape")state.dialogGeneration++;});
  $("refresh").addEventListener("click",refresh);
  $("company").addEventListener("change",renderJobs);$("job").addEventListener("change",renderJobContext);
  $("current-target").addEventListener("change",async(event)=>{try{if(await loadTarget(event.target.value))showStage(state.stage);}catch(err){showError(err);}});
  $("target-form").addEventListener("submit",async(event)=>{
    event.preventDefault();const ids=checkedMaterials("target-materials");if(!ids.length){feedback("请选择至少一项分析材料。",{error:true});return;}
    const job=state.catalog.jobs.find((j)=>j.id===$("job").value);
    await intent("/targets",{job_id:$("job").value,material_ids:ids,label:$("target-label").value.trim()||job?.title||"职业分析目标"},async(data)=>{
      const target=data.target||data, generation=beginSelection();
      try {
        const targets=await api("/targets");if(!currentGeneration(generation))return;
        const bundle=await readTargetBundle(target.target_id,{generation});
        if(publishSelection(bundle,generation,{targets:targets.targets||[]})){showStage("diagnosis",true);feedback("目标已建立。可以开始差距诊断。");}
      } catch(error) {if(currentGeneration(generation)){state.selectionPending=false;renderTargets();showError(error);}}
    },event.submitter);
  });
  $("supplement-form").addEventListener("submit",async(event)=>{
    event.preventDefault();const ids=checkedMaterials("supplement-materials").filter((id)=>!(state.target?.material_ids||[]).includes(id));
    if(!ids.length){feedback("请选择当前目标尚未使用的新增材料。",{error:true});return;}
    await intent("/targets/"+segment(state.target.target_id)+"/supplement",{material_ids:ids,parent_route_id:state.progress?.route.route_id||null,expected_revision:state.target.revision},queueResult,event.submitter);
  });
  $("material-file").addEventListener("change",async(event)=>{
    const file=event.target.files?.[0];state.file=null;if(!file)return;
    if(file.size>2_000_000){event.target.value="";feedback("文件超过 2 MB，请缩小文件或粘贴相关原文。",{error:true});return;}
    if(!/\.(txt|md|pdf)$/i.test(file.name)){event.target.value="";feedback("请选择 TXT、Markdown 或 PDF 文件。",{error:true});return;}
    $("material-name").value=file.name;
    try {
      const isPDF=/\.pdf$/i.test(file.name);
      const data=await new Promise((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(reader.result);reader.onerror=()=>reject(new Error("文件读取失败，请重新选择。"));if(isPDF)reader.readAsDataURL(file);else reader.readAsText(file);});
      if(isPDF){state.file={name:file.name,data_base64:String(data).split(",")[1]};$("material-text").value="";$("material-text").placeholder="已选择 PDF。需改用粘贴原文时，直接在此输入。";}
      else {$("material-text").value=String(data);const first=String(data).split(/\r?\n/).find((line)=>line.trim());if(!$("material-quote").value)$("material-quote").value=first||"";}
      feedback(`已读取 ${file.name}。请核对说明、贡献范围和支持原句后保存。`);
    }catch(err){showError(err);}
  });
  $("material-form").addEventListener("submit",async(event)=>{
    event.preventDefault();const content=$("material-text").value, quote=$("material-quote").value;
    if(!content.trim()&&!state.file){feedback("请选择文件或粘贴材料原文。",{error:true});return;}
    if(content&&!content.includes(quote)){feedback("支持原句须逐字存在于材料原文中。",{error:true});$("material-quote").focus();return;}
    if(new TextEncoder().encode(content).length>2_000_000){feedback("材料原文超过 2 MB，请选取相关内容。",{error:true});return;}
    const body={name:$("material-name").value,kind:$("material-kind").value,claim:$("material-claim").value,ownership:$("material-ownership").value,quote,...(content.trim()?{text:content}:{data_base64:state.file.data_base64})};
    await intent("/materials",body,async(data,context)=>{const material=data.material||data,bootstrap=await api("/bootstrap");if(!currentSelection(context))return;state.materials=bootstrap.materials||[];renderMaterials();$("material-form").reset();state.file=null;$("import-details").open=false;feedback(`已保存材料：${material.title||body.name}。`);},event.submitter);
  });
  window.addEventListener("pagehide",()=>clearTimeout(state.polling));
  async function start() {
    return loadBootstrap(true);
  }
  start();
})();
