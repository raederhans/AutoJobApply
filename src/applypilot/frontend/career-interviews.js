/* Interview flow inside the existing career workbench; shared session and intents. */
"use strict";
window.createCareerInterviewPanel=({state,api,intent,feedback,showError,showStage,displayDate,safeURL,escapeHTML:e,segment})=>{
  const $=(id)=>document.getElementById(id),zone=Intl.DateTimeFormat().resolvedOptions().timeZone||"UTC";
  const statuses={pending:"等待安排",scheduled:"已安排",completed:"已完成",cancelled:"已取消"};
  const pad=(value)=>String(value).padStart(2,"0");
  function dateInput(value){if(!value)return "";const d=new Date(value);return Number.isFinite(d.getTime())?`${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`:"";}
  function instant(value,required=false){
    if(!value){if(required)throw new Error("请选择时间。");return null;}
    const d=new Date(value);if(!Number.isFinite(d.getTime())||dateInput(d)!==value)throw new Error("这个本地时间无效，请重新选择。");
    if([-3600000,3600000].some((offset)=>dateInput(new Date(d.getTime()+offset))===value))throw new Error("这个时间存在夏令时歧义，请通过已有 CLI 用明确偏移记录。");
    const offset=-d.getTimezoneOffset();return `${value}:00${offset<0?"-":"+"}${pad(Math.floor(Math.abs(offset)/60))}:${pad(Math.abs(offset)%60)}`;
  }
  function jobOptions(selected=""){return '<option value="">选择邀请对应的已有岗位</option>'+(state.interviews?.jobs||[]).map((job)=>`<option value="${e(job.job_url)}" ${job.job_url===selected?"selected":""}>${e(job.company||"公司未提供")} · ${e(job.title||"岗位未提供")}</option>`).join("");}
  function render(){
    const box=$("interview-list");if(!box)return;
    if(state.interviewsLoading&&!state.interviews){box.innerHTML='<div class="empty">正在读取面试记录…</div>';return;}
    const scope=$("interview-filter").value||"active",items=(state.interviews?.items||[]).filter((item)=>scope==="all"||(scope==="active"?["pending","scheduled"].includes(item.status):item.status===scope));
    box.innerHTML=items.length?items.map((item)=>`<article class="interview-card"><div class="interview-card-head"><div><p class="inbox-company">${e(item.company||"公司未提供")}</p><h3>${e(item.title||"岗位未提供")}</h3><p>${e(item.round_name||(item.round?`第 ${item.round} 轮`:"轮次未记录"))}</p></div><span class="tag ${item.status==="cancelled"?"warn":""}">${e(statuses[item.status]||"状态待核对")}</span></div><p class="interview-time">${item.scheduled_at?e(displayDate(item.scheduled_at)):"时间尚未确定"}${item.timezone?` <span class="hint">原记录时区：${e(item.timezone)}</span>`:""}</p><p class="hint">${e(item.mode||"方式未记录")} · ${(item.actions||[]).filter((action)=>!action.completed_at).length} 项未完成待办</p><button type="button" class="button secondary" data-interview-view="${e(item.id)}">查看与管理 →</button></article>`).join(""):'<div class="empty">此范围暂无面试。收到邀请后，关联已有岗位即可开始准备。</div>';
    const pending=state.interviews?.pending_invitations||[];
    $("interview-pending").innerHTML=pending.length?`<section class="interview-pending"><h3>邀请待关联岗位</h3>${pending.map((item)=>`<article class="reminder-card"><p>${e(item.summary||"已有面试邀请")}</p><p class="hint">${e(item.company||"")} ${e(item.title||"")}</p><button type="button" class="button subtle" data-interview-resolve="${e(item.event_id)}">选择对应岗位</button></article>`).join("")}</section>`:"";
  }
  async function load(){
    const generation=++state.interviewGeneration;state.interviewsLoading=true;render();
    try{const data=await api("/interviews");if(generation===state.interviewGeneration)state.interviews=data;}
    catch(error){if(generation===state.interviewGeneration)showError(error,load);}
    finally{if(generation===state.interviewGeneration){state.interviewsLoading=false;render();}}
  }
  function preparationMarkup(prep={}){
    const pack=prep.content,sections=pack?.sections;
    if(prep.status!=="ready"||!sections){const labels={not_prepared:"尚未生成准备包。",missing_materials:"缺少可核对的岗位材料或简历，请先在现有流程补齐。",unavailable:"当前材料暂不可读取。",source_changed:"材料已变化，原准备包需要重新核对生成。"};return `<p class="notice info">${e(labels[prep.status]||"尚未生成准备包。")}</p>${(prep.missing_materials||[]).length?`<ul>${prep.missing_materials.map((item)=>`<li>${e(item)}</li>`).join("")}</ul>`:""}`;}
    const sources=(item)=>(item.sources||[]).map((ref)=>`<button type="button" class="text-button" data-interview-source="${e(ref)}">查看原句 ${e(ref)}</button>`).join(" ");
    return `<p class="hint">${e(pack.resume?.binding_label||"按当前材料准备，请核对实际投递版本。")}</p>${(pack.warnings||[]).map((warning)=>`<p class="notice info">${e(warning)}</p>`).join("")}<h4>岗位重点</h4><ul>${(sections.role_focus||[]).map((item)=>`<li>${e(item.text)} ${sources(item)}</li>`).join("")}</ul><h4>练习问题</h4><p>${e(sections.round_guidance||"")}</p><ul>${(sections.questions||[]).map((item)=>`<li>${e(item.prompt)} ${sources(item)}</li>`).join("")}</ul><h4>STAR 证据与待补内容</h4>${(sections.star_evidence||[]).map((item)=>`<blockquote>${e(item.evidence_quote)} ${sources(item)}<p>${e(item.situation)}<br>${e(item.task)}<br>${e(item.action)}<br>${e(item.result)}</p></blockquote>`).join("")}<ul>${(sections.gaps||[]).map((item)=>`<li>${e(item.requirement)}：${e(item.note)} ${sources(item)}</li>`).join("")}</ul><h4>准备反问</h4><ul>${(sections.questions_to_ask||[]).map((item)=>`<li>${e(item.prompt)} ${sources(item)}</li>`).join("")}</ul>`;
  }
  function detail(item){
    state.interviewItem=item;const active=["pending","scheduled"].includes(item.status),url=safeURL(item.job_url);
    $("interview-dialog-title").textContent=`${item.company||"公司未提供"} · ${item.round_name||(item.round?`第 ${item.round} 轮`:"面试")}`;
    $("interview-dialog-content").innerHTML=`<p class="interview-detail-title">${e(item.title||"岗位未提供")} <span class="tag">${e(statuses[item.status]||item.status)}</span></p><p>${item.scheduled_at?e(displayDate(item.scheduled_at)):"时间尚未确定"} ${e(item.mode||"")}</p>${item.result?`<p class="interview-result">面试结果：${e(state.interviews?.result_options?.[item.result]||item.result)}</p>`:""}${url?`<a href="${e(url)}" target="_blank" rel="noopener noreferrer">查看岗位 ↗</a>`:""}<p class="hint">页面时间按本设备时区 ${e(zone)} 显示。</p><div class="interview-actions actions"><button type="button" class="button secondary" data-interview-edit="reschedule" ${active?"":"disabled"}>${item.scheduled_at?"改期":"安排时间"}</button><button type="button" class="button subtle" data-interview-edit="result" ${active?"":"disabled"}>记录面试结果</button><button type="button" class="button subtle" data-interview-edit="cancel" ${active?"":"disabled"}>取消本轮</button><button type="button" class="button subtle" data-interview-reminders>查看跟进清单</button></div><section class="interview-detail-section"><div class="interview-card-head"><h3>准备包</h3><button type="button" class="button secondary" data-interview-prepare ${active?"":"disabled"}>${item.preparation?.status==="ready"?"重新生成":"生成准备包"}</button></div>${preparationMarkup(item.preparation)}</section><section class="interview-detail-section"><div class="interview-card-head"><h3>准备与跟进待办</h3><button type="button" class="button subtle" data-interview-edit="actions" ${item.status==="cancelled"?"disabled":""}>添加待办</button></div><p class="hint">这些待办也会出现在现有跟进清单。</p>${(item.actions||[]).map((action)=>`<div class="interview-todo"><div><strong>${e(action.summary)}</strong><p class="hint">${e(displayDate(action.due_at))} · ${action.completed_at?"已完成":"待完成"}</p></div>${action.completed_at?"":`<button type="button" class="button subtle" data-interview-complete="${e(action.action_id)}">标记完成</button>`}</div>`).join("")||'<p class="empty">暂无待办。可添加准备任务或等待反馈的下一步。</p>'}</section><details class="interview-detail-section"><summary>邀请与变更历史</summary><ol class="interview-history">${(item.history||[]).map((event)=>`<li><strong>${e({interview_invited:"收到邀请",interview:"记录面试",interview_rescheduled:"改期",interview_cancelled:"取消",interview_completed:"面试结果",manual_note:"本地记录"}[event.event_type]||"本地记录")}</strong><p>${e(event.source_text||event.summary||"")}</p><small>${e(displayDate(event.occurred_at))}${event.scheduled_at?` · 面试时间 ${e(displayDate(event.scheduled_at))}`:""}</small></li>`).join("")}</ol></details>`;
  }
  async function show(id){const generation=++state.interviewDetailGeneration;try{const data=await api("/interviews/"+segment(id));if(generation!==state.interviewDetailGeneration||state.stage!=="interviews")return;detail(data.item);if(!$("interview-dialog").open)$("interview-dialog").showModal();}catch(error){if(generation===state.interviewDetailGeneration)showError(error);}}
  function form(action,item=state.interviewItem){
    const identity=action==="resolve"?item.event_id:item?.id||"new",key=action+":"+identity,draft=state.interviewDrafts.get(key)||{},value=(name,fallback="")=>e(draft[name]??fallback);
    state.interviewDetailGeneration++;
    $("interview-dialog-title").textContent={create:"记录面试邀请",resolve:"关联已有面试邀请",reschedule:"安排或更改面试时间",cancel:"取消本轮面试",result:"记录面试结果",actions:"添加准备或跟进待办"}[action];
    let fields="";
    if(action==="create"||action==="resolve")fields+=`<label for="interview-job">对应岗位</label><select id="interview-job" name="job_url" required>${jobOptions(draft.job_url||"")}</select>`;
    if(action==="resolve")fields+=`<p class="notice info">${e(item.summary||"已有邀请")}。关联后会保留原邀请与已记录的轮次。</p>`;
    if(action==="create")fields+=`<div class="interview-form-grid"><div><label for="interview-round">轮次</label><input id="interview-round" name="round" type="number" min="1" max="100" value="${value("round",1)}" required></div><div><label for="interview-round-name">轮次名称</label><input id="interview-round-name" name="round_name" maxlength="160" value="${value("round_name")}" placeholder="技术面 / HR 面"></div></div>`;
    if(action==="create"||action==="reschedule")fields+=`<label for="interview-time">面试时间（${e(zone)}）</label><input id="interview-time" name="scheduled_at" type="datetime-local" value="${value("scheduled_at",dateInput(action==="reschedule"?item.scheduled_at:null))}" ${action==="reschedule"?"required":""}><p class="hint">${action==="create"?"邀请尚未确定时间时可留空。":"确认新的面试时间后保存。"}</p>`;
    if(action==="create")fields+=`<label for="interview-mode">方式或地点</label><input id="interview-mode" name="mode" maxlength="160" value="${value("mode")}" placeholder="视频面试 / 办公室地点">`;
    if(action==="reschedule")fields+=`<p class="hint">原方式或地点：${e(item.mode||"未记录")}。变更说明可写在下方来源中。</p>`;
    if(action==="result")fields+=`<label for="interview-result">本轮结果</label><select id="interview-result" name="result" required><option value="">选择已确认的结果</option>${Object.entries(state.interviews?.result_options||{completed:"面试已完成，等待反馈",next_round:"进入下一轮",offer:"收到 offer",rejected:"本轮未通过"}).map(([id,label])=>`<option value="${e(id)}" ${draft.result===id?"selected":""}>${e(label)}</option>`).join("")}</select>`;
    if(action==="actions")fields+=`<label for="interview-action-summary">待办事项</label><input id="interview-action-summary" name="summary" maxlength="200" value="${value("summary")}" required placeholder="练习项目介绍 / 跟进结果"><label for="interview-action-due">截止时间（${e(zone)}）</label><input id="interview-action-due" name="due_at" type="datetime-local" value="${value("due_at")}" required>`;
    if(!["resolve","actions"].includes(action))fields+=`<label for="interview-source">${action==="create"?"邀请原文或来源说明":action==="cancel"?"取消原因与来源":action==="result"?"结果记录与来源":"改期说明与来源"}</label><textarea id="interview-source" name="source_text" rows="4" maxlength="20000" required>${value("source_text")}</textarea>`;
    $("interview-dialog-content").innerHTML=`<form id="interview-form"><fieldset class="interview-form-fields">${fields}<div class="actions"><button type="submit" class="button primary">${action==="resolve"?"确认关联":action==="cancel"?"确认取消本轮":"保存本地记录"}</button>${item?.id?'<button type="button" class="button subtle" data-interview-back>返回面试记录</button>':""}</div></fieldset></form>`;
    const element=$("interview-form"),remember=()=>state.interviewDrafts.set(key,Object.fromEntries(new FormData(element)));
    element.addEventListener("input",remember);element.addEventListener("change",remember);
    element.addEventListener("submit",async(event)=>{
      event.preventDefault();if(state.busy)return;remember();const data=Object.fromEntries(new FormData(element)),body={...data};
      try{if(action==="create"||action==="reschedule"){body.scheduled_at=instant(data.scheduled_at,action==="reschedule");body.timezone=zone;}if(action==="create")body.round=Number(data.round);if(action==="actions")body.due_at=instant(data.due_at,true);if(action!=="create")body.expected_revision=item.revision;
        const path=action==="create"?"/interviews":action==="resolve"?"/interviews/invitations/"+segment(item.event_id)+"/resolve":"/interviews/"+segment(item.id)+"/"+action;
        element.querySelector("fieldset").disabled=true;await write(path,body,event.submitter,()=>state.interviewDrafts.delete(key));
      }catch(error){showError(error);}finally{if(element.isConnected)element.querySelector("fieldset").disabled=false;}
    });
    if(!$("interview-dialog").open)$("interview-dialog").showModal();
  }
  async function write(path,body,button,onSaved=()=>{}){
    if(state.busy)return;if(state.interviewUncertain){feedback("上次写入结果尚未确认。可先刷新查看记录，或重试上次相同请求。",{error:true,action:state.interviewRetry,label:"重试上次请求"});return;}
    const generation=state.interviewDetailGeneration;
    await intent(path,body,async(result)=>{state.interviewUncertain=false;state.interviewRetry=null;onSaved();await load();if(generation===state.interviewDetailGeneration&&state.stage==="interviews")await show(result.item.id);feedback("面试记录已保存。");},button,{independent:true,onError:(error,retry)=>{state.interviewUncertain=Boolean(error.network);state.interviewRetry=error.network?retry:null;}});
  }
  async function action(name,button,id=null){const item=state.interviewItem;if(item)await write("/interviews/"+segment(item.id)+"/"+name,{expected_revision:item.revision,...(id?{action_id:id}:{})},button);}
  function source(ref){const pack=state.interviewItem?.preparation?.content,line=pack?.jd?.source_refs?.[ref]??pack?.resume?.source_refs?.[ref];if(line===undefined)return;$("source-title").textContent=`${ref.startsWith("jd:")?"岗位原句":"简历原句"} · ${ref}`;$("source-text").textContent=line;$("source-dialog").showModal();}
  document.addEventListener("click",async(event)=>{
    const button=event.target.closest("button");if(!button||button.disabled)return;
    if(button.hasAttribute("data-interview-refresh"))await load();
    if(button.hasAttribute("data-interview-new")){if(!state.interviews)await load();form("create",null);}
    if(button.dataset.interviewView)await show(button.dataset.interviewView);
    if(button.dataset.interviewResolve){const item=state.interviews?.pending_invitations?.find((item)=>item.event_id===button.dataset.interviewResolve);if(item)form("resolve",item);}
    if(button.dataset.interviewEdit)form(button.dataset.interviewEdit);
    if(button.hasAttribute("data-interview-back")&&state.interviewItem)await show(state.interviewItem.id);
    if(button.hasAttribute("data-interview-prepare"))await action("prepare",button);
    if(button.dataset.interviewComplete)await action("complete-action",button,button.dataset.interviewComplete);
    if(button.dataset.interviewSource)source(button.dataset.interviewSource);
    if(button.hasAttribute("data-interview-reminders")){$("interview-dialog").close();showStage("job-progress",true);$("job-reminders-section").open=true;}
  });
  $("interview-filter").addEventListener("change",render);$("interview-dialog").addEventListener("close",()=>{state.interviewDetailGeneration++;});
  return {load,show};
};
