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
  Object.assign(state,{inbox:null,inboxLoading:false,inboxError:null,inboxGeneration:0,inboxDrafts:new Map(),inboxQuery:{q:"",scope:"open",sort:"newest",offset:0,record_id:""},inboxDetailGeneration:0,inboxRecordLabel:null});
  Object.assign(state,{jobReport:null,jobReportLoading:false,jobReportError:null,jobReportGeneration:0,jobStatusOptionsReady:false,jobReportDownloading:false,jobReportQuery:{q:"",since:"",until:"",date_field:"updated_at",statuses:[],offset:0,record_id:""},jobReportRecordLabel:null});
  Object.assign(state,{reminders:null,reminderLoading:false,reminderError:null,reminderGeneration:0,reminderLimit:100,reminderBusy:false,reminderUncertain:false,reminderDownloading:false});
  const stageNames = {materials:"目标与材料",diagnosis:"差距诊断",route:"学习路线",review:"产物与评审",inbox:"申请待处理","job-progress":"求职进度"};
  const validRecordId=(value)=>typeof value==="string"&&/^application-[a-f0-9]{16}$/.test(value);
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
    for(const name of ["task","inbox","job-reminder"]) {
    const dialog=$(name+"-dialog");
    if(dialog.open) {
      let inline=dialog.querySelector("[data-dialog-feedback]");
      if(!inline) {inline=document.createElement("div");inline.dataset.dialogFeedback="";inline.setAttribute("role","status");dialog.insertBefore(inline,$(name+"-dialog-content"));}
      inline.className="notice "+(error?"":"info");inline.replaceChildren();
      const messageSpan=document.createElement("span");messageSpan.textContent=message;inline.append(messageSpan);
      if(action){const button=document.createElement("button");button.type="button";button.className="button subtle";button.textContent=label;button.addEventListener("click",action);inline.append(button);}
    }
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
    if(err.status===403&&err.code==="session_required") {feedback("页面会话已变化，请刷新后核对再操作。",{error:true,action:()=>{$("task-dialog").close();loadBootstrap();},label:"刷新会话"});return;}
    feedback(err.message || "操作未完成。",{error:true,...(err.network&&retry?{action:retry,label:"重试本次操作"}:{})});
  }
  // A retry retains the exact intent payload and request key; conflicts require a fresh user action.
  async function intent(path, body, onSuccess, button, {independent=false}={}) {
    if(state.busy||(!independent&&state.selectionPending)) return;
    const context=independent?null:selectionContext(), current=()=>independent||currentSelection(context);
    const payload=JSON.parse(JSON.stringify({...body,request_key:crypto.randomUUID()}));
    const perform=async()=>{
      if(state.busy||!current()) return;
      state.busy=true; if(button) button.disabled=true;
      try {const result=await api(path,payload);if(!current())return;state.retry=null;$("feedback").hidden=true;await onSuccess(result,context);}
      catch(err) {if(current()){state.retry=err.network?perform:null;showError(err,perform);}}
      finally {state.busy=false;if(button?.isConnected) button.disabled=false;}
    };
    await perform();
  }
  function showStage(stage, focus=false, {read=true}={}) {
    state.stage=stage;
    document.querySelectorAll("[data-stage]").forEach((button)=>{if(button.dataset.stage===stage)button.setAttribute("aria-current","step");else button.removeAttribute("aria-current");});
    Object.keys(stageNames).forEach((name)=>{$("stage-"+name).hidden=name!==stage;});
    $("page-title").textContent=stageNames[stage];remember();if(focus)$("main").focus();
    $("target-strip").hidden=["inbox","job-progress"].includes(stage);renderRuns();
    if(read&&stage==="inbox")loadInbox();
    if(read&&stage==="job-progress"){loadJobProgress();loadReminders();}
  }
  function renderInbox() {
    const data=state.inbox, counts=data?.counts||{};
    const recordBox=$("inbox-record-filter"),recordLabel=data?.record_filter?.record_id===state.inboxQuery.record_id?data.record_filter:state.inboxRecordLabel;
    recordBox.hidden=!state.inboxQuery.record_id;
    recordBox.innerHTML=state.inboxQuery.record_id?`<div><strong>限定为对应求职记录的申请项</strong><p>${e(recordLabel?.title||"所选求职记录")}${recordLabel?.company?` · ${e(recordLabel.company)}`:""}</p><small>按精确保存的来源入口关联；搜索、范围与分页继续保留此限定。</small></div><button type="button" class="button subtle" data-inbox-clear-record>清除限定，查看全部</button>`:"";
    $("inbox-search-submit").disabled=state.inboxLoading;
    $("inbox-counts").innerHTML=[["shown","本页条目"],["open","本页开放"],["needs_response","本页待答复"],["resume_requested","本页等待接续"],["blocked","本页需另行处理"]].map(([key,label])=>`<div class="metric"><strong>${e(data?.available===true&&Number.isFinite(counts[key])?counts[key]:"—")}</strong>${label}${data?.truncated?` <span class="hint">（仅本页 ${e(data.limit||100)} 条以内）</span>`:""}</div>`).join("");
    const pagination=data?.pagination, nav=$("inbox-pagination");
    nav.hidden=data?.available!==true||!pagination;
    if(pagination){
      const offset=Number.isInteger(pagination.offset)?pagination.offset:0,total=Number.isFinite(pagination.total)?pagination.total:"—",length=data.items.length;
      const previous=Number.isInteger(pagination.previous_offset)&&pagination.previous_offset>=0,next=Number.isInteger(pagination.next_offset)&&pagination.next_offset>=0&&pagination.has_more===true;
      nav.innerHTML=`<p class="hint">${length?`第 ${e(offset+1)}–${e(offset+length)} 条`:"本页没有记录"} · 匹配共 ${e(total)} 条<span>概览计数与状态筛选仅限本页</span></p><div class="actions"><button type="button" class="button subtle" data-inbox-page="previous" ${!previous||state.inboxLoading||state.inboxError?"disabled":""}>← 上一页</button><button type="button" class="button subtle" data-inbox-page="next" ${!next||state.inboxLoading||state.inboxError?"disabled":""}>下一页 →</button></div>`;
    }
    const box=$("inbox-content");box.setAttribute("aria-busy",String(state.inboxLoading));
    if(state.inboxLoading&&!data){box.innerHTML='<div class="empty"><span class="spinner" aria-hidden="true"></span>正在读取待处理申请…</div>';return;}
    const error=state.inboxError?`<div class="notice" role="alert">${e(state.inboxError)}${data?" 下方保留上次读取的列表。":""} <button type="button" class="button subtle" data-inbox-refresh>重新读取</button></div>`:"";
    if(!data){box.innerHTML=error;return;}
    if(data.available!==true){box.innerHTML=error+'<div class="empty"><h3>申请待处理暂不可用</h3><p>请确认本地申请工作空间已就绪，再刷新列表。</p></div>';return;}
    const filter=$("inbox-filter").value||"all", items=data.items.filter((item)=>filter==="all"||item.status===filter);
    const statusNames={awaiting_response:"待答复",ready_to_resume:"可申请继续",resume_requested:"等待申请进程接续",blocked:"需要另行处理",expired:"已过期",resolved:"已解决",failed:"流程未完成"};
    box.innerHTML=error+(items.length?`<div class="inbox-list">${items.map((item)=>{
      const job=item.job||{},url=safeURL(job.url),tone=["blocked","expired","failed"].includes(item.status)?"warn":item.status==="ready_to_resume"?"good":"";
      return `<article class="inbox-card"><div class="inbox-card-head"><div><p class="inbox-company">${e(job.company||"公司未提供")}</p><h3>${e(job.title||"岗位未提供")}</h3></div><span class="tag ${tone}">${e(item.status_label||statusNames[item.status]||"状态待核对")}</span></div><div class="inbox-meta"><span>${e(item.kind_label||"申请暂停项")}</span>${item.created_at?`<span>${e(displayDate(item.created_at))}</span>`:""}${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">查看岗位 ↗</a>`:""}</div><p class="inbox-prompt">${e(item.prompt||"请查看下一步说明。")}</p><div class="inbox-next"><strong>${e(item.next_action?.label||"下一步")}</strong><p>${e(item.next_action?.description||"请在原申请流程核对当前状态。")}</p></div>${item.status==="resume_requested"?'<p class="inbox-wait">已请求继续，等待申请进程接续。可手动刷新查看变化。</p>':""}<div class="actions"><button type="button" class="button subtle" data-inbox-progress="${e(item.id)}">查看进度</button>${item.can_respond===true?`<button type="button" class="button ${item.response_recorded?"subtle":"primary"}" data-inbox-action="respond" data-inbox-id="${e(item.id)}">${item.response_recorded?"修改答复":"填写答复"}</button>`:""}${item.can_resume===true?`<button type="button" class="button secondary" data-inbox-action="resume" data-inbox-id="${e(item.id)}">申请继续 →</button>`:""}${item.response_recorded?'<span class="hint">答复已保存</span>':""}</div></article>`;
    }).join("")}</div>`:`<div class="empty"><h3>${data.items.length?"本页没有符合此状态的记录":"没有匹配的申请记录"}</h3><p>${data.items.length?"切换本页状态，或翻页查看其它记录。":"调整关键词与范围后点击搜索；已解决的条目可在历史记录中查看。"}</p></div>`)+(data.truncated?`<p class="hint">列表与概览计数仅覆盖本页，最多 ${e(data.limit||100)} 条。可翻页继续查看。</p>`:"");
  }
  async function loadInbox() {
    const generation=++state.inboxGeneration;state.inboxLoading=true;state.inboxError=null;renderInbox();
    const query=state.inboxQuery;
    try {const data=await api("/application-inbox?q="+segment(query.q)+"&scope="+segment(query.scope)+"&sort="+segment(query.sort)+"&offset="+query.offset+(query.record_id?"&record_id="+segment(query.record_id):""));if(generation!==state.inboxGeneration)return;state.inbox={...data,items:Array.isArray(data.items)?data.items:[]};}
    catch(error){if(generation===state.inboxGeneration){
      if(error.code==="progress_record_changed"||error.code==="progress_source_unavailable"){state.inbox=null;state.inboxError=error.code==="progress_record_changed"?"原记录的来源身份已变化，无法继续关联。请回到求职进度核对记录，或清除限定后人工查看全部申请项。":"这条记录没有唯一的原申请入口，无法精确关联。可清除限定后人工查看全部申请项。";}
      else state.inboxError=error.message||"申请列表读取失败，请重试。";
    }}
    finally {if(generation===state.inboxGeneration){state.inboxLoading=false;renderInbox();}}
  }
  async function inboxPage(direction) {
    if(state.inboxLoading||state.inboxError)return;
    const page=state.inbox?.pagination, offset=direction==="next"?page?.next_offset:page?.previous_offset;
    if(!Number.isInteger(offset)||offset<0||(direction==="next"&&page?.has_more!==true))return;
    state.inboxQuery.offset=offset;await loadInbox();
  }
  async function showInboxProgress(id) {
    const dialog=$("inbox-progress-dialog"), box=$("inbox-progress-content"),generation=++state.inboxDetailGeneration;
    box.innerHTML='<p><span class="spinner" aria-hidden="true"></span>正在读取申请进度…</p>';box.setAttribute("aria-busy","true");
    if(!dialog.open)dialog.showModal();
    try {
      const data=await api("/application-inbox/"+segment(id));
      if(generation!==state.inboxDetailGeneration||!dialog.open)return;
      const item=data.item||{},timeline=Array.isArray(data.timeline)?data.timeline:[];
      const dateText=(event)=>{
        if(!["recorded","deadline"].includes(event.time_basis)||!event.occurred_at)return "未记录";
        const date=new Date(event.occurred_at);
        return Number.isNaN(date.getTime())?"未记录":new Intl.DateTimeFormat("zh-CN",{year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit",hour12:false}).format(date);
      };
      box.innerHTML=`<p class="inbox-company">${e(item.job?.company||"公司未提供")}</p><h3>${e(item.job?.title||"岗位未提供")}</h3><p><span class="tag">${e(item.status_label||"状态待核对")}</span></p><div class="inbox-next"><strong>${e(item.next_action?.label||"当前下一步")}</strong><p>${e(item.next_action?.description||"请在原申请流程核对当前状态。")}</p></div><p class="hint">此视图只读取已有进度。请求继续表示等待原申请进程接续，不表示已经恢复或完成投递。</p><h3 class="inbox-timeline-title">已记录的时间线</h3>${timeline.length?`<ol class="inbox-timeline">${timeline.map((event)=>`<li><p class="inbox-event-time">${event.time_basis==="deadline"?"期限：":"记录时间："}${e(dateText(event))}</p><h4>${e(event.label||"进度记录")}</h4><p>${e(event.description||"未提供说明")}</p></li>`).join("")}</ol>`:'<p class="hint">尚无可展示的时间线记录。</p>'}${timeline.some((event)=>event.time_basis==="deadline")?'<p class="notice info">期限是约定截止时间，不代表申请进程已在该时刻处理过期状态。</p>':""}${data.timeline_truncated?'<p class="hint">时间线仅展示部分记录。</p>':""}`;
    } catch(error){if(generation===state.inboxDetailGeneration&&dialog.open)box.innerHTML=`<div class="notice" role="alert">${e(error.message||"进度读取失败。")}</div><button type="button" class="button subtle" data-inbox-progress="${e(id)}">重试读取</button>`;}
    finally {if(generation===state.inboxDetailGeneration)box.setAttribute("aria-busy","false");}
  }
  async function inboxAction(action,id,button) {
    const item=state.inbox?.items.find((item)=>item.id===id);if(!item||state.busy)return;
    if(!state.token){feedback("工作空间正在连接，请稍后再操作。",{error:true});return;}
    const endpoint="/application-inbox/"+segment(id), revision=item.revision;
    const completed=async(result,savedAnswer)=>{
      if(result.item)state.inbox.items=state.inbox.items.map((entry)=>entry.id===id?result.item:entry);
      if(savedAnswer!==undefined){if(state.inboxDrafts.get(id)===savedAnswer)state.inboxDrafts.delete(id);$("inbox-dialog").close();}
      renderInbox();await loadInbox();
      feedback(action==="resume"?"已请求继续，等待申请进程接续":result.message||"答复已保存。请核对下一步，再点击申请继续。");
    };
    if(action==="resume"&&item.can_resume===true){await intent(endpoint+"/resume",{revision},(result)=>completed(result),button,{independent:true});return;}
    if(action!=="respond"||item.can_respond!==true)return;
    const dialog=$("inbox-dialog");dialog.querySelector("[data-dialog-feedback]")?.remove();
    $("inbox-dialog-content").innerHTML=`<p class="inbox-company">${e(item.job?.company||"公司未提供")}</p><h3>${e(item.job?.title||"岗位未提供")}</h3><blockquote>${e(item.prompt||"请填写原申请需要的答复。")}</blockquote><p class="notice info" id="inbox-answer-help">登录、验证码与安全验证请在原申请页面完成。这里不要填写密码或验证码。答复保存后仍需明确申请继续。</p><form id="inbox-answer-form"><label for="inbox-answer">你的答复</label><textarea id="inbox-answer" rows="6" maxlength="4000" required aria-describedby="inbox-answer-help inbox-answer-count">${e(state.inboxDrafts.get(id)||"")}</textarea><p class="hint" id="inbox-answer-count"></p><button type="submit" class="button primary">保存答复</button></form>`;
    const answer=$("inbox-answer"),count=()=>{$("inbox-answer-count").textContent=`${answer.value.length} / 4000 字 · 未保存的草稿仅保留在当前页面`;};count();
    answer.addEventListener("input",()=>{state.inboxDrafts.set(id,answer.value);count();});
    $("inbox-answer-form").addEventListener("submit",async(event)=>{
      event.preventDefault();if(state.busy)return;const value=answer.value.trim();
      if(!value||value.length>4000){feedback("请填写不超过 4000 字的答复。",{error:true});answer.focus();return;}
      state.inboxDrafts.set(id,value);answer.disabled=true;
      try{await intent(endpoint+"/respond",{revision,answer:value},(result)=>completed(result,value),event.submitter,{independent:true});}
      finally{answer.disabled=false;}
    });
    dialog.showModal();answer.focus();
  }
  function jobProgressParams(query=state.jobReportQuery, withOffset=true) {
    const parts=["q="+segment(query.q),"date_field="+segment(query.date_field)];
    if(query.since)parts.push("since="+segment(query.since));if(query.until)parts.push("until="+segment(query.until));
    query.statuses.forEach((status)=>parts.push("status="+segment(status)));
    if(query.record_id)parts.push("record_id="+segment(query.record_id));
    if(withOffset)parts.push("offset="+query.offset);return parts.join("&");
  }
  function syncJobRecordInputs() {
    const query=state.jobReportQuery;$("job-progress-query").value=query.q;$("job-progress-since").value=query.since;$("job-progress-until").value=query.until;$("job-progress-date-field").value=query.date_field;
    $("job-progress-status-options").querySelectorAll("input[type=checkbox]").forEach((input)=>{input.checked=query.statuses.includes(input.value);});
  }
  function syncInboxRecordInputs() {
    const query=state.inboxQuery;$("inbox-query").value=query.q;$("inbox-scope").value=query.scope;$("inbox-sort").value=query.sort;$("inbox-filter").value="all";
  }
  async function viewReminderRecord(id) {
    if(!validRecordId(id))return;const item=Object.values(state.reminders?.groups||{}).flatMap((group)=>Array.isArray(group)?group:[]).find((item)=>item.record_id===id&&item.target_available===true);if(!item)return;
    state.jobReportQuery={q:"",since:"",until:"",date_field:"updated_at",statuses:[],offset:0,record_id:id};state.jobReportRecordLabel={title:item.title,company:item.company};state.jobReport=null;syncJobRecordInputs();showStage("job-progress",false,{read:false});await loadJobProgress();
    if(state.stage==="job-progress"&&state.jobReportQuery.record_id===id&&!state.jobReportError){$("job-progress-content").scrollIntoView({block:"start"});$("job-progress-content").focus({preventScroll:true});}
  }
  async function viewAssociatedApplications(id) {
    if(!validRecordId(id))return;const row=state.jobReport?.rows.find((row)=>row.record_id===id);if(!row)return;
    state.inboxQuery={q:"",scope:"all",sort:"newest",offset:0,record_id:id};state.inboxRecordLabel={title:row.title,company:row.company};state.inbox=null;syncInboxRecordInputs();showStage("inbox",true,{read:false});await loadInbox();
  }
  async function clearJobRecordFilter() {
    state.jobReportQuery={q:"",since:"",until:"",date_field:"updated_at",statuses:[],offset:0,record_id:""};state.jobReportRecordLabel=null;state.jobReport=null;syncJobRecordInputs();await loadJobProgress();
  }
  async function clearInboxRecordFilter() {
    state.inboxQuery={q:"",scope:"all",sort:"newest",offset:0,record_id:""};state.inboxRecordLabel=null;state.inbox=null;syncInboxRecordInputs();await loadInbox();
  }
  function progressStatus(value) {
    const labels={not_started:"未开始",in_progress:"执行中",applying:"执行中",applied:"记录为已投递",already_applied:"已有投递记录",failed:"执行未完成",previewed:"仅预览",submission_uncertain:"投递结果待核对",expired:"已过期",skipped:"已跳过",blocked:"需要处理",needs_human:"需要人工处理",manual:"需要人工处理",captcha:"需要人工验证",duplicate:"重复记录",submission_unverified:"投递尚未核实",unknown:"状态未核实",contacted:"已联系",assessment:"测评阶段",offer:"收到 offer",accepted:"已接受",declined:"已谢绝",rejected:"已拒绝",withdrawn:"已撤回",conflict:"记录存在冲突",interview_unknown:"面试轮次未记录"};
    const interview=/^interview_([1-9][0-9]*)$/.exec(String(value||""));if(interview)return `第 ${interview[1]} 轮面试`;
    return labels[value]||state.jobReport?.status_options?.find((option)=>option.value===value)?.label||(value?"状态待核对":"未记录");
  }
  function progressDate(value) {
    if(typeof value!=="string"||!value.trim())return "未记录";const date=new Date(value);
    return Number.isNaN(date.getTime())?"未记录":new Intl.DateTimeFormat("zh-CN",{timeZone:"UTC",year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false}).format(date);
  }
  function progressTodo(value) {
    const labels={submission_confirmation_missing:"投递回执待核对",followup_action_pending:"存在尚未完成的后续行动",retry_blocked_requires_review:"原流程阻止重试，需人工核对",application_readiness_requires_review:"申请准备情况待核对",expired:"岗位或申请已过期",already_applied:"已有投递记录，需核对重复情况",duplicate:"存在重复申请线索",not_a_job_application:"申请入口性质待核对",explicit_do_not_apply:"原记录注明不应申请",security_challenge_required:"安全验证需要人工处理",human_verification_required:"登录或人机验证需要人工处理",assessment_required:"需要处理测评",sensitive_identity_boundary:"敏感身份或财务步骤需要人工核对",unsupported_legal_declaration:"法律声明需要真实信息与人工核对",required_material_missing:"必要申请材料待补",cover_letter_missing:"求职信材料待核对",resume_upload_failed:"简历附件上传结果待核对",credential_relay_missing:"登录能力尚未就绪",mailbox_route_unavailable:"邮箱申请流程尚未就绪",visual_control_unavailable:"页面操作能力尚未就绪",browser_interaction_unavailable:"浏览器交互尚未就绪",browser_mcp_unavailable:"浏览器连接尚未就绪",agent_result_conflict:"执行记录相互矛盾，需核对",site_blocked:"网站访问受阻",runtime_budget_exhausted:"原执行时间预算已用尽",unknown:"原流程结果待核对"};
    if(!value)return "暂无待核对线索";
    return String(value).split(";").map((reason)=>labels[reason.trim()]||"存在需要核对的原流程线索").join("；");
  }
  function renderJobProgress() {
    const data=state.jobReport,summary=data?.summary||{},box=$("job-progress-content");
    const recordBox=$("job-progress-record-filter"),recordLabel=state.jobReportRecordLabel;
    recordBox.hidden=!state.jobReportQuery.record_id;
    recordBox.innerHTML=state.jobReportQuery.record_id?`<div><strong>来自提醒的记录筛选</strong><p>${e(recordLabel?.title||"所选求职记录")}${recordLabel?.company?` · ${e(recordLabel.company)}`:""}</p><small>精确限定此记录；继续筛选、分页与下载都保留此范围。</small></div><button type="button" class="button subtle" data-job-progress-clear-record>清除限定，查看全部记录</button>`:"";
    $("job-progress-apply").disabled=state.jobReportLoading;box.setAttribute("aria-busy",String(state.jobReportLoading));
    for(const id of ["job-progress-csv","job-progress-markdown","job-progress-html"])$(id).disabled=data?.available!==true||state.jobReportLoading||!!state.jobReportError||state.jobReportDownloading;
    $("job-progress-summary").innerHTML=[["record_count","匹配记录"],["todo_count","含待核对线索的记录"],["open_action_record_count","含开放行动的记录"]].map(([key,label])=>`<div class="metric"><strong>${e(data?.available===true&&Number.isFinite(summary[key])?summary[key]:"—")}</strong>${label}</div>`).join("");
    const options=data?.status_options;
    if(Array.isArray(options)){
      const selected=state.jobStatusOptionsReady?[...$("job-progress-status-options").querySelectorAll("input:checked")].map((input)=>input.value):state.jobReportQuery.statuses;
      $("job-progress-status-options").innerHTML=options.length?options.map((option)=>`<label><input type="checkbox" value="${e(option.value)}" ${selected.includes(option.value)?"checked":""}><span>${e(option.label||progressStatus(option.value))}</span></label>`).join(""):'<p class="hint">暂无可选状态。</p>';state.jobStatusOptionsReady=true;
    }
    const page=data?.pagination,nav=$("job-progress-pagination");nav.hidden=data?.available!==true||!page;
    if(page){const offset=Number.isInteger(page.offset)?page.offset:0,length=data.rows.length,total=Number.isFinite(page.total)?page.total:"—";
      const previous=Number.isInteger(page.previous_offset)&&page.previous_offset>=0,next=Number.isInteger(page.next_offset)&&page.next_offset>=0&&page.has_more===true,unavailable=state.jobReportLoading||state.jobReportError;
      nav.innerHTML=`<p class="hint">${length?`第 ${e(offset+1)}–${e(offset+length)} 条`:"本页没有记录"} · 共 ${e(total)} 条<span>摘要与下载覆盖全部匹配记录</span></p><div class="actions"><button type="button" class="button subtle" data-job-progress-page="previous" ${!previous||unavailable?"disabled":""}>← 上一页</button><button type="button" class="button subtle" data-job-progress-page="next" ${!next||unavailable?"disabled":""}>下一页 →</button></div>`;
    }
    if(state.jobReportLoading&&!data){box.innerHTML='<div class="empty"><span class="spinner" aria-hidden="true"></span>正在读取求职记录…</div>';return;}
    const error=state.jobReportError?`<div class="notice" role="alert">${e(state.jobReportError)}${data?" 下方保留上次读取的记录。":""}<button class="button subtle" type="button" data-job-progress-refresh>重新读取</button></div>`:"";
    if(!data){box.innerHTML=error;return;}if(data.available!==true){box.innerHTML=error+'<div class="empty"><h3>求职进度暂不可用</h3><p>请确认本地工作空间已就绪后刷新。</p></div>';return;}
    const filters=data.filters||{},dateNames={updated_at:"最近观察更新",submitted_at:"记录的投递日期",discovered_at:"发现日期"};
    const applied=`关键词：${filters.q||"不限"} · ${dateNames[filters.date_field]||"最近观察更新"}：${filters.since||"不限起始"} 至 ${filters.until||"不限结束"}（UTC） · 状态：${filters.statuses?.length?filters.statuses.map(progressStatus).join("、"):"全部"}`;
    const bases={verified:"已核验记录",user_confirmed:"用户已确认",reported:"仅报告，待核实",unverified:"尚未核实",uncertain:"结果不确定"};
    box.innerHTML=error+`<p class="hint job-progress-applied">已应用筛选 · ${e(applied)}<br>汇总生成时间：${e(progressDate(data.generated_at))}（UTC）</p>`+(data.rows.length?`<div class="job-progress-table-wrap" role="region" aria-label="本页求职记录，可横向滚动" tabindex="0"><table class="job-progress-table"><thead><tr><th scope="col">岗位</th><th scope="col">公司</th><th scope="col">执行状态</th><th scope="col">招聘阶段</th><th scope="col">投递依据</th><th scope="col">日期（UTC）</th><th scope="col">待核对线索</th><th scope="col">关联的开放行动</th><th scope="col">记录操作</th></tr></thead><tbody>${data.rows.map((row)=>`<tr><td><strong>${e(row.title||"岗位未提供")}</strong>${row.location?`<small>${e(row.location)}</small>`:""}</td><td>${e(row.company||"公司未提供")}</td><td>${e(progressStatus(row.application_status))}</td><td>${e(progressStatus(row.recruiting_stage))}</td><td>${e(bases[row.submission_basis]||"未记录")}</td><td class="job-progress-dates"><span>最近观察更新：${e(progressDate(row.updated_at))}</span><span>记录的投递：${e(progressDate(row.submitted_at))}</span><span>发现：${e(progressDate(row.discovered_at))}</span></td><td>${e(progressTodo(row.todo_reason))}${row.todo_due_at?`<small>关联行动期限：${e(progressDate(row.todo_due_at))}（UTC）</small>`:""}</td><td>${e(Number.isFinite(row.open_action_count)?row.open_action_count:"—")}</td><td><div class="actions">${validRecordId(row.record_id)?`<button type="button" class="button subtle" data-inbox-record="${e(row.record_id)}">查看关联申请</button>`:""}${typeof row.record_id==="string"&&row.record_id?`<button type="button" class="button subtle" data-reminder-create="${e(row.record_id)}" data-reminder-write ${state.reminderBusy||state.reminderUncertain?"disabled":""}>设置提醒</button>`:"未提供可用记录"}</div></td></tr>`).join("")}</tbody></table></div>`:`<div class="empty"><h3>没有匹配的求职记录</h3><p>${state.jobReportQuery.record_id?"当前限定下没有匹配记录。若原记录已变化，请刷新提醒后重新选择，或清除记录限定查看全部。":"调整关键词、日期或状态后点击应用筛选。"}</p>${state.jobReportQuery.record_id?'<button class="button subtle" type="button" data-reminder-refresh>刷新提醒，重新选择</button>':""}</div>`);
  }
  async function loadJobProgress() {
    const generation=++state.jobReportGeneration;state.jobReportLoading=true;state.jobReportError=null;renderJobProgress();
    try{const data=await api("/job-progress?"+jobProgressParams());if(generation===state.jobReportGeneration)state.jobReport={...data,rows:Array.isArray(data.rows)?data.rows:[]};}
    catch(error){if(generation===state.jobReportGeneration)state.jobReportError=error.message||"求职记录读取失败。";}
    finally{if(generation===state.jobReportGeneration){state.jobReportLoading=false;renderJobProgress();}}
  }
  async function jobProgressPage(direction) {
    if(state.jobReportLoading||state.jobReportError)return;const page=state.jobReport?.pagination,offset=direction==="next"?page?.next_offset:page?.previous_offset;
    if(!Number.isInteger(offset)||offset<0||(direction==="next"&&page?.has_more!==true))return;state.jobReportQuery.offset=offset;await loadJobProgress();
  }
  async function downloadJobProgress(format) {
    if(!["csv","markdown","html"].includes(format)||state.jobReportDownloading||state.jobReportLoading||state.jobReportError||state.jobReport?.available!==true)return;
    const query=jobProgressParams(state.jobReportQuery,false);state.jobReportDownloading=true;renderJobProgress();
    let objectURL;
    try{
      const response=await fetch(BASE+"/job-progress/download?format="+format+"&"+query,{headers:{Accept:format==="csv"?"text/csv":format==="html"?"text/html":"text/markdown"},cache:"no-store"});
      if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.error?.message||"下载未完成，请检查本地服务。");}
      const blob=await response.blob();objectURL=URL.createObjectURL(blob);const link=document.createElement("a");
      const filename=/filename="?([^";]+)/i.exec(response.headers.get("Content-Disposition")||"")?.[1]?.trim();
      link.href=objectURL;link.download=filename&&!/[\\/]/.test(filename)?filename:`job-progress.${format==="csv"?"csv":format==="html"?"html":"md"}`;document.body.append(link);link.click();link.remove();
      feedback("已生成并下载全部匹配记录。未发送、投递或分享。");
    }catch(error){feedback(error.message||"下载连接中断，请检查本地服务后重试。",{error:true});}
    finally{if(objectURL)setTimeout(()=>URL.revokeObjectURL(objectURL),1000);state.jobReportDownloading=false;renderJobProgress();}
  }
  function reminderStep(value) {
    const defaults={"Review the saved application progress, then dismiss or postpone this local reminder.":"核对已保存的申请进度，再决定是否隐藏或延后本地提醒。","Review the existing submission receipt before deciding any further action.":"先核对已有投递回执，再决定下一步。","Review this closed or conflicting recruiting record and decide whether to keep the local task.":"核对已结束或有冲突的招聘记录，再决定是否保留本地跟进任务。"};
    if(defaults[value])return defaults[value];
    const labels={follow_up:"核对跟进进展",check_status:"核对申请进度",review_progress:"查看已有进度",followup_action_pending:"核对尚未完成的跟进行动","Follow up":"核对跟进进展","Check application status":"核对申请进度"};
    return labels[value]||value||"核对进度并决定下一步";
  }
  function syncReminderControls() {
    document.querySelectorAll("[data-reminder-write]").forEach((button)=>{button.disabled=state.reminderBusy||state.reminderUncertain;});
  }
  function renderReminders() {
    const data=state.reminders,counts=data?.counts||{},groups=data?.groups||{},labels={overdue:"逾期",today:"今天",future:"未来",undated:"未定日期"},box=$("reminder-content");
    for(const id of ["reminder-html","reminder-ics"])$(id).disabled=!data||state.reminderLoading||!!state.reminderError||state.reminderDownloading;
    $("reminder-date").textContent=`清单日期：${data?.date||"未记录"}（UTC） · 各组计数为全量；本清单不受上方进度筛选影响`;
    $("reminder-counts").innerHTML=Object.entries(labels).map(([key,label])=>`<div class="metric"><strong>${e(Number.isFinite(counts[key])?counts[key]:"—")}</strong>${label}</div>`).join("");
    box.setAttribute("aria-busy",String(state.reminderLoading));
    if(state.reminderLoading&&!data){box.innerHTML='<p class="hint"><span class="spinner" aria-hidden="true"></span>正在读取本地提醒…</p>';return;}
    const error=state.reminderError?`<div class="notice" role="alert">${e(state.reminderError)} <button class="button subtle" type="button" data-reminder-refresh>刷新提醒清单</button></div>`:"";
    const uncertain=state.reminderUncertain?'<div class="notice" role="alert">上次写入结果尚未核对。请刷新清单核对后再操作；不会自动重试。</div>':"";
    if(!data){box.innerHTML=error+uncertain;syncReminderControls();return;}
    let hasMore=false;
    box.innerHTML=error+uncertain+`<p class="hint">${e(Number.isFinite(data.unscheduled_count)?data.unscheduled_count:"—")} 条记录尚未设置本地提醒，可在下方进度表逐行设置。${data.target_unavailable_count?` ${e(data.target_unavailable_count)} 条提醒的目标记录不可用，仍保留在清单中，需核对。`:""}</p>`+Object.entries(labels).map(([key,label])=>{
      const items=Array.isArray(groups[key])?groups[key]:[],total=Number.isFinite(counts[key])?counts[key]:null;
      if(total!==null&&total>items.length)hasMore=true;
      return `<section class="reminder-group"><div class="reminder-group-head"><h3>${label}</h3><span class="muted">显示 ${e(items.length)} / ${e(total??"—")} 条${total!==null&&total>items.length?" · 还有未显示记录":""}</span></div>${items.length?items.map((item)=>`<article class="reminder-card"><div class="reminder-card-head"><div><p class="inbox-company">${e(item.company||"公司未提供")}</p><h4>${e(item.title||"原岗位未提供")}</h4></div><span class="tag">${item.source==="existing_action"?"已有行动 · 清单视图":item.source==="local_reminder"?"本地提醒":"来源待核对"}</span></div><p class="reminder-status">执行状态：${e(progressStatus(item.application_status))} · 招聘阶段：${e(progressStatus(item.recruiting_stage))}</p>${item.target_available===false?'<p class="notice">目标记录不可用，请核对原流程；提醒仍被保留。</p>':""}<p class="reminder-next">${e(reminderStep(item.next_step))}</p><div class="reminder-card-foot"><span class="hint">提醒日期（UTC）：${e(item.due_date||"未定日期")}</span><div class="actions">${item.target_available===true&&validRecordId(item.record_id)?`<button type="button" class="text-button" data-reminder-record="${e(item.record_id)}">查看求职记录</button>`:""}<button type="button" class="text-button" data-reminder-snooze="${e(item.reminder_id)}" data-reminder-write>延后</button><button type="button" class="text-button" data-reminder-dismiss="${e(item.reminder_id)}" data-reminder-write>隐藏提醒</button></div></div></article>`).join(""):'<p class="hint">本组没有提醒。</p>'}</section>`;
    }).join("")+ (hasMore?`<div class="reminder-more"><p class="hint">每组最多显示 ${e(state.reminderLimit)} 条，计数包含未显示记录。${state.reminderLimit>=500?"已达每组 500 条显示上限，仍有未显示记录。":""}</p><button type="button" class="button subtle" data-reminder-more ${state.reminderLimit>=500||state.reminderLoading?"disabled":""}>显示更多（每组增加 100 条）</button></div>`:"");
    syncReminderControls();
  }
  async function loadReminders({reconcile=false}={}) {
    const generation=++state.reminderGeneration;state.reminderLoading=true;state.reminderError=null;renderReminders();
    try{const data=await api("/job-progress/reminders?limit="+state.reminderLimit);if(!["overdue","today","future","undated"].every((key)=>Array.isArray(data?.groups?.[key])&&Number.isFinite(data?.counts?.[key])))throw new Error("提醒清单格式不完整，请重新读取。");if(generation===state.reminderGeneration){state.reminders=data;if(reconcile)state.reminderUncertain=false;}}
    catch(error){if(generation===state.reminderGeneration)state.reminderError=error.message||"提醒清单读取失败。";}
    finally{if(generation===state.reminderGeneration){state.reminderLoading=false;renderReminders();}}
  }
  function findReminder(id) {return Object.values(state.reminders?.groups||{}).flatMap((items)=>Array.isArray(items)?items:[]).find((item)=>item.reminder_id===id);}
  async function downloadReminders(format="html") {
    if(!["html","ics"].includes(format)||!state.reminders||state.reminderLoading||state.reminderError||state.reminderDownloading)return;
    const bucket=$("reminder-download-bucket").value||"all";if(!["all","overdue","today","future","undated"].includes(bucket))return;
    state.reminderDownloading=true;if(format==="ics")$("reminder-calendar-result").hidden=true;renderReminders();let objectURL;
    try{
      const response=await fetch(BASE+"/job-progress/reminders/download?format="+format+"&bucket="+segment(bucket),{headers:{Accept:format==="ics"?"text/calendar":"text/html"},cache:"no-store"});
      if(!response.ok){let data;try{data=await response.json();}catch{}throw new Error(data?.error?.message||"提醒下载未完成，请检查本地服务。");}
      objectURL=URL.createObjectURL(await response.blob());const link=document.createElement("a");link.href=objectURL;link.download=`followup-reminders.${format}`;document.body.append(link);link.click();link.remove();
      if(format==="ics"){
        const count=(name)=>{const raw=response.headers.get(name);const value=raw!==null&&/^\d+$/.test(raw)?Number(raw):null;return Number.isSafeInteger(value)?value:null;};
        const events=count("X-ApplyPilot-Calendar-Events"),undated=count("X-ApplyPilot-Calendar-Omitted-Undated"),unavailable=count("X-ApplyPilot-Calendar-Omitted-Unavailable");
        const skippedDate=undated===null?"无日期跳过数量未提供":`无日期跳过 ${undated} 条`,skippedTarget=unavailable===null?"目标不可用跳过数量未提供":`目标不可用跳过 ${unavailable} 条`;
        const message=(events===0?"已下载空日历（0 个全天事件）":events===null?"已下载日历（事件数量未提供）":`已下载日历（${events} 个全天事件）`)+`；${skippedDate}，${skippedTarget}。请自行导入；文件不会随后续编辑自动更新，重复导入可能产生重复事件。`;
        const names={all:"全部",overdue:"逾期",today:"今天",future:"未来",undated:"未定日期"};$("reminder-calendar-result").textContent=`上次下载 · ${names[bucket]}组：${message}`;$("reminder-calendar-result").hidden=false;feedback(message);
      }else feedback("已下载所选组的全部提醒，可离线打开或打印。");
    }catch(error){feedback(error.message||"提醒下载连接中断，请检查本地服务后重试。",{error:true});}
    finally{if(objectURL)setTimeout(()=>URL.revokeObjectURL(objectURL),1000);state.reminderDownloading=false;renderReminders();}
  }
  async function writeReminder(path,body,{closeDialog=false,message="本地提醒已保存。"}={}) {
    if(state.reminderBusy||state.reminderUncertain)return;
    if(!state.token){feedback("工作空间正在连接，请稍后再操作。",{error:true});return;}
    state.reminderBusy=true;syncReminderControls();
    try{await api(path,body);if(closeDialog)$("job-reminder-dialog").close();await loadReminders();feedback(message);}
    catch(error){
      if(error.network){state.reminderUncertain=true;feedback("写入结果暂不确定。刷新清单核对后再操作；不会自动重试。",{error:true,action:()=>loadReminders({reconcile:true}),label:"刷新提醒清单"});}
      else feedback(error.message||"提醒未保存，请核对输入。",{error:true});
    }finally{state.reminderBusy=false;renderReminders();syncReminderControls();}
  }
  function openReminder(action,id) {
    if(state.reminderBusy||state.reminderUncertain)return;
    const item=action==="create"?state.jobReport?.rows.find((row)=>row.record_id===id):findReminder(id);if(!item)return;
    const snooze=action==="snooze",current=snooze?item.due_date||"":"",dialog=$("job-reminder-dialog");
    dialog.querySelector("[data-dialog-feedback]")?.remove();$("job-reminder-title").textContent=snooze?"延后本地提醒":"设置本地提醒";
    $("job-reminder-dialog-content").innerHTML=`<p class="inbox-company">${e(item.company||"公司未提供")}</p><h3>${e(item.title||"岗位未提供")}</h3><p class="hint">执行状态：${e(progressStatus(item.application_status))} · 招聘阶段：${e(progressStatus(item.recruiting_stage))}</p><p class="notice info">${snooze&&item.source==="existing_action"?"延后只改变本清单中的提醒日期，原行动期限保留。新 ICS 使用延期后的清单日期，已导入文件不会自动更新。":"保存只记录本地提醒，不会投递申请、联系他人或改变招聘状态。"}</p><form id="job-reminder-form"><label for="reminder-due-date">${snooze?"新的提醒日期":"提醒日期"}（UTC）</label><input id="reminder-due-date" type="date" required ${current?`min="${e(current)}"`:""}>${snooze?`<p class="hint">当前日期：${e(current||"未定日期")}。延期不能早于当前提醒日期。</p>`:'<label for="reminder-next-step">短下一步</label><textarea id="reminder-next-step" rows="3" maxlength="200" required placeholder="例如：核对面试时间，整理下一轮准备事项"></textarea>'}<button type="submit" class="button primary" data-reminder-write>${snooze?"保存延后日期":"保存本地提醒"}</button></form>`;
    $("job-reminder-form").addEventListener("submit",async(event)=>{
      event.preventDefault();if(state.reminderBusy||state.reminderUncertain)return;const due_date=$("reminder-due-date").value,next_step=snooze?null:$("reminder-next-step").value.trim();
      if(!due_date||(current&&due_date<current)){feedback("请选择有效日期；延期不能早于当前提醒日期。",{error:true});$("reminder-due-date").focus();return;}
      if(!snooze&&(!next_step||next_step.length>200)){feedback("请填写不超过 200 字的短下一步。",{error:true});$("reminder-next-step").focus();return;}
      const fields=[$("reminder-due-date"),...(!snooze?[$("reminder-next-step")]:[])];fields.forEach((field)=>{field.disabled=true;});
      try{await writeReminder(snooze?"/job-progress/reminders/"+segment(id)+"/snooze":"/job-progress/reminders",snooze?{due_date}:{record_id:id,due_date,next_step},{closeDialog:true,message:snooze?"提醒已延后，仅更新本地清单。":"本地提醒已保存，不改变申请或联系状态。"});}
      finally{fields.forEach((field)=>{field.disabled=false;});}
    });
    dialog.showModal();$("reminder-due-date").focus();
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
    box.hidden=["inbox","job-progress"].includes(state.stage)||!visible.length;
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
  async function refresh() {return state.stage==="inbox"?loadInbox():state.stage==="job-progress"?Promise.all([loadJobProgress(),loadReminders({reconcile:true})]):loadBootstrap();}
  async function resultOfRun(run, navigate=false) {
    const refs=run.result_refs||{}, selectedId=state.target?.target_id;
    if(!navigate&&(["inbox","job-progress"].includes(state.stage)||state.busy||state.selectionPending||(refs.target_id&&selectedId&&refs.target_id!==selectedId)))return false;
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
    if(button.hasAttribute("data-inbox-refresh"))await loadInbox();
    if(button.dataset.inboxPage)await inboxPage(button.dataset.inboxPage);
    if(button.dataset.inboxProgress)await showInboxProgress(button.dataset.inboxProgress);
    if(button.hasAttribute("data-job-progress-refresh"))await loadJobProgress();
    if(button.dataset.jobProgressPage)await jobProgressPage(button.dataset.jobProgressPage);
    if(button.dataset.jobProgressDownload)await downloadJobProgress(button.dataset.jobProgressDownload);
    if(button.hasAttribute("data-job-progress-clear-record"))await clearJobRecordFilter();
    if(button.hasAttribute("data-inbox-clear-record"))await clearInboxRecordFilter();
    if(button.dataset.reminderRecord)await viewReminderRecord(button.dataset.reminderRecord);
    if(button.dataset.inboxRecord)await viewAssociatedApplications(button.dataset.inboxRecord);
    if(button.hasAttribute("data-reminder-refresh")){$("job-reminders-section").open=true;await loadReminders({reconcile:true});}
    if(button.hasAttribute("data-reminder-download"))await downloadReminders(button.dataset.reminderDownload||"html");
    if(button.hasAttribute("data-reminder-more")&&!state.reminderLoading&&state.reminderLimit<500){state.reminderLimit+=100;await loadReminders();}
    if(button.dataset.reminderCreate)openReminder("create",button.dataset.reminderCreate);
    if(button.dataset.reminderSnooze)openReminder("snooze",button.dataset.reminderSnooze);
    if(button.dataset.reminderDismiss&&findReminder(button.dataset.reminderDismiss))await writeReminder("/job-progress/reminders/"+segment(button.dataset.reminderDismiss)+"/dismiss",{},{message:"提醒已从本清单隐藏，不代表已完成或已联系。"});
    if(button.dataset.inboxAction)await inboxAction(button.dataset.inboxAction,button.dataset.inboxId,button);
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
    if(event.target.id==="inbox-filter")renderInbox();
    if(event.target.id==="route-history")try {await loadRoute(event.target.value);}catch(err){showError(err);}
  });
  document.addEventListener("keydown",(event)=>{if(event.key==="Escape")state.dialogGeneration++;});
  $("refresh").addEventListener("click",refresh);
  $("inbox-search-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(state.inboxLoading)return;
    state.inboxQuery={q:$("inbox-query").value.trim(),scope:$("inbox-scope").value||"open",sort:$("inbox-sort").value||"newest",offset:0,record_id:state.inboxQuery.record_id||""};
    await loadInbox();
  });
  $("inbox-progress-dialog").addEventListener("close",()=>{state.inboxDetailGeneration++;});
  $("job-progress-form").addEventListener("submit",async(event)=>{
    event.preventDefault();if(state.jobReportLoading)return;
    const since=$("job-progress-since").value,until=$("job-progress-until").value;
    if(since&&until&&since>until){feedback("起始日期不能晚于结束日期。",{error:true});$("job-progress-since").focus();return;}
    state.jobReportQuery={q:$("job-progress-query").value.trim(),since,until,date_field:$("job-progress-date-field").value||"updated_at",statuses:[...$("job-progress-status-options").querySelectorAll("input:checked")].map((input)=>input.value),offset:0,record_id:state.jobReportQuery.record_id||""};await loadJobProgress();
  });
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
