"use strict";
(() => {
  const $ = (selector, root = document) => root.querySelector(selector);
  const offlineMode = Boolean(window.ShijieNative) || new URLSearchParams(location.search).get("offline") === "1";
  let offlineReady;
  async function offlineStore() {
    if (!offlineReady) offlineReady = (async () => {
      if (!window.ShijieOffline) throw new Error("离线组件未能加载，请重新打开拾阶。");
      const response = await fetch("/offline/seed.json",{cache:"no-store"});
      if (!response.ok) throw new Error("应用内的学习资料未能读取，请重新安装可信的完整安装包。");
      const seed = await response.json(), prompts = {};
      const names = {onboarding:"first-run",learning:"project-coach",reference:"reference"};
      await Promise.all(Object.entries(names).map(async ([mode,name]) => {
        const result = await fetch("/prompts/"+name+".md");
        if (!result.ok) throw new Error("应用内的学习协议不完整，请重新打开或安装完整应用。");
        prompts[mode] = await result.text();
      }));
      const store = await window.ShijieOffline.createStore({seed,prompts});
      window.ShijieOffline.appStore = store;
      return store;
    })();
    return offlineReady;
  }
  const state = {token:"",projects:[],limits:{},project:null,flows:[],flowId:"",flowStepId:"",traceCaseId:"",traceIndex:0,flowReturn:null,concepts:[],activities:[],materials:[],summary:{},projectSummary:{},reviewState:null,reviewDrafts:new Map(),reviewRequestId:0,checkFeedback:new Map(),group:"",conceptId:"",activityId:"",activityDetail:null,view:"overview",dueOnly:false,practiceFilterInitialized:false,drafts:new Map(),requestId:0,packDraft:null,bundleDraft:null,bundleFiles:[],restore:null,coachPrompts:{},check:null,checkQuestions:[],checkAnswers:new Map(),checkIndex:0,checkHistory:[],checkResults:null,checkRequestId:0};
  const kinds = {predict:"预测结果",trace:"跟踪执行",debug:"定位故障",reproduce:"独立复现",transfer:"迁移变式"};
  const statuses = {correct:"固定答案符合",incorrect:"需要重看",review_required:"待复核",not_started:"尚未练习",unattempted:"尚未练习",new:"尚未练习",due:"待回看",stale:"来源已更新"};
  const helps = {none:"未借助外部帮助",agent:"借助 Agent",reference:"查阅资料"};
  const conceptNames = {"java-types":"类型与数值边界","control-flow":"分支与执行顺序","interfaces-di":"接口与依赖注入","http-validation":"HTTP 与输入校验","identity-permission":"可信身份与数据权限","collections-state":"集合与可变状态","concurrency":"并发与任务控制","database-transaction":"持久化与事务","timeouts-reliability":"超时与失败边界","tool-calling":"工具分发与安全调用","rag-citations":"检索与引用边界","tests-evidence":"测试与证据范围","observability":"日志与可观测性","redis-messaging":"缓存与异步消息"};
  let toastTimer, confirmResolve = null;
  function element(tag, className = "", text = "") {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== "" && text != null) node.textContent = String(text);
    return node;
  }
  function append(parent, ...children) { children.flat().filter(Boolean).forEach(child => parent.append(child)); return parent; }
  function textOf(value) { if (Array.isArray(value)) return value.map(textOf).filter(Boolean).join("\n"); if (value && typeof value === "object") return JSON.stringify(value,null,2); return value == null ? "" : String(value); }
  function stamp(value) { if (!value) return "时间未记录"; const date = new Date(value); return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("zh-CN",{month:"numeric",day:"numeric",hour:"2-digit",minute:"2-digit"}); }
  function groupOf(concept) { return concept.group || "其他知识"; }
  function projectVersion() { return state.project?.version ?? "—"; }
  function currentConcept() { return state.concepts.find(c => c.id === state.conceptId); }
  function safeRelative(path) { const normalized = String(path || "").replaceAll("\\","/"); if (/^[A-Za-z]:\//.test(normalized) || normalized.startsWith("/")) return normalized.split("/").filter(Boolean).slice(-2).join("/"); return normalized; }
  function statusText(message, error = false) { $("#status-text").textContent = message; $("#status-line").classList.toggle("error",error); }
  function toast(message, error = false) { const node = $("#toast"); node.textContent = message; node.classList.toggle("error",error); node.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(() => { node.hidden = true; },5000); }
  async function api(path, method = "GET", body) {
    const headers = {"Accept":"application/json"};
    if (body !== undefined) headers["Content-Type"] = "application/json";
    if (method !== "GET") headers["X-Study-Token"] = state.token;
    let response;
    try {
      const options = {method,headers,body:body === undefined ? undefined : JSON.stringify(body),credentials:"same-origin",cache:"no-store"};
      response = offlineMode ? await (await offlineStore()).fetch(path,options) : await fetch(path,options);
    }
    catch (error) { $("#service-reconnect").hidden = false; throw new Error(offlineMode ? (error.message || "本地资料无法读取，请关闭后重新打开应用。已保存的记录不会自动被替换。") : "无法连接拾阶服务。先双击应用目录的 START.cmd，再点击“重新连接”。只打开旧网页不会启动服务。"); }
    let data;
    try { data = await response.json(); } catch { throw new Error("学习资料返回了无法读取的内容，请重新打开应用后重试。"); }
    if (!response.ok) {
      const detail = typeof data.error === "string" ? data.error : data.error?.message;
      if (response.status === 403) $("#service-reconnect").hidden = false;
      throw new Error(detail || data.message || ("操作未完成（" + response.status + "），请检查输入。"));
    }
    return data;
  }
  async function action(fn, buttonNode, label = "正在处理…") {
    if (buttonNode?.disabled) return;
    const oldText = buttonNode?.textContent;
    if (buttonNode) { buttonNode.disabled = true; buttonNode.textContent = label; }
    try { return await fn(); }
    catch (error) { statusText(error.message,true); toast(error.message,true); }
    finally { if (buttonNode) { buttonNode.disabled = false; buttonNode.textContent = oldText; } }
  }
  function button(text, onClick, className = "button quiet") { const node = element("button",className,text); node.type = "button"; node.addEventListener("click",onClick); return node; }
  function openDialog(id) { const dialog = $("#" + id); if (!dialog.open) dialog.showModal(); }
  function closeDialog(id) { const dialog = $("#" + id); if (dialog.open) dialog.close(); }
  function openManagement(sectionId = "onboarding-section") { openDialog("manage-dialog"); $("#" + sectionId)?.scrollIntoView({block:"start",behavior:"auto"}); }
  function confirmAction(title, message, label = "确认") {
    if (confirmResolve) confirmResolve(false);
    $("#confirm-title").textContent = title; $("#confirm-message").textContent = message; $("#confirm-ok").textContent = label;
    openDialog("confirm-dialog");
    return new Promise(resolve => { confirmResolve = resolve; });
  }
  function settleConfirm(value) { const resolve = confirmResolve; confirmResolve = null; closeDialog("confirm-dialog"); resolve?.(value); }
  function download(name, data) {
    if (window.ShijieNative) return nativeExport(name,"application/json",JSON.stringify(data,null,2));
    const url = URL.createObjectURL(new Blob([JSON.stringify(data,null,2)],{type:"application/json;charset=utf-8"}));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url),2000);
  }
  function downloadText(name, text) {
    if (window.ShijieNative) return nativeExport(name,"text/plain",text);
    const url = URL.createObjectURL(new Blob([text],{type:"text/plain;charset=utf-8"}));
    const anchor = document.createElement("a"); anchor.href = url; anchor.download = name; document.body.append(anchor); anchor.click(); anchor.remove(); setTimeout(() => URL.revokeObjectURL(url),2000);
  }
  function nativeExport(name, mime, text) {
    const bytes = new TextEncoder().encode(text);
    if (bytes.length > 64 * 1048576) throw new Error("文件过大，无法导出，请先整理资料。");
    const chunks = [];
    for (let start = 0; start < bytes.length; start += 16384) chunks.push(String.fromCharCode(...bytes.subarray(start,start+16384)));
    window.ShijieNative.exportFile(name,mime,btoa(chunks.join("")));
    toast("请选择文件保存位置，完成后会提示保存结果。");
  }
  window.addEventListener("shijie-native-export",event => {
    const detail = event.detail || {};
    toast(detail.message || (detail.ok ? "文件已保存。" : "文件未保存。"),!detail.ok);
  });
  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); toast("已复制，可以交给你正在使用的 Agent。"); }
    catch { openPlain("Agent 出题提示词",text,"可以选中以下文本手动复制。"); }
  }
  function projectURL(suffix = "") { if (!state.project) throw new Error("请先导入一个项目。"); return "/api/projects/" + encodeURIComponent(state.project.id) + suffix; }
  function applyProject(data) {
    const changed = state.project?.id !== data.project.id || state.project?.version !== data.project.version;
    state.project = data.project; state.flows = Array.isArray(data.flows) ? data.flows : []; state.concepts = data.concepts || []; state.activities = data.activities || []; state.materials = data.materials || []; state.summary = data.summary || {}; state.projectSummary = data.project_summary || {}; state.reviewState = data.review_state || null;
    if (changed) { state.reviewRequestId++; clearCheck(); state.flowId = ""; state.flowStepId = ""; state.traceCaseId = ""; state.traceIndex = 0; state.flowReturn = null; }
    if (!state.flows.some(flow => flow.id === state.flowId)) selectFlowState(state.flows[0]?.id || "");
    if (!state.projects.some(p => p.id === state.project.id)) state.projects.push(state.project);
    else state.projects = state.projects.map(p => p.id === state.project.id ? state.project : p);
    try { localStorage.setItem("shijie-last-project",state.project.id); } catch {}
    let lastConcept; try { lastConcept = localStorage.getItem("shijie-concept-" + state.project.id); } catch {}
    if (!state.concepts.some(c => c.id === state.conceptId)) state.conceptId = state.concepts.find(c => c.id === lastConcept)?.id || state.concepts[0]?.id || "";
    state.group = groupOf(currentConcept() || {});
    renderProjectPicker(); renderWorkspace(); updatePrompt();
    statusText("已读取「" + state.project.name + "」的本机快照 · 版本 " + projectVersion() + " · 原项目未被修改");
  }
  async function loadProject(id) {
    state.activityId = ""; state.activityDetail = null; state.conceptId = ""; state.group = ""; state.dueOnly = false; state.practiceFilterInitialized = false; state.flowReturn = null;
    state.drafts.clear(); state.packDraft = null; $("#pack-preview").hidden = true;
    const requestId = ++state.requestId;
    const data = await api("/api/projects/" + encodeURIComponent(id));
    if (requestId !== state.requestId) return;
    applyProject(data);
    if (state.view === "practice") await selectFirstActivity();
  }
  async function refreshProject() { const data = await api(projectURL()); applyProject(data); return data; }
  function renderProjectPicker() {
    const select = $("#project-select"); select.replaceChildren();
    if (!state.projects.length) { const option = element("option","","尚未导入项目"); option.value = ""; select.append(option); }
    state.projects.forEach(project => { const option = element("option","",project.name); option.value = project.id; select.append(option); });
    select.value = state.project?.id || state.projects[0]?.id || "";
    $("#delete-section").hidden = !state.project;
    $("#materials-section").hidden = !state.project;
    if ($("#update-current-row")) $("#update-current-row").hidden = !state.project;
  }
  function renderWorkspace() {
    $("#welcome").hidden = !!state.project; $("#workspace").hidden = !state.project;
    if (!state.project) return;
    $("#sidebar-project-name").textContent = state.project.name;
    $("#sidebar-project-summary").textContent = "版本 " + projectVersion() + " · " + state.flows.length + " 个功能场景";
    renderOverview(); renderReview(); renderFlows(); renderGroups(); renderConcepts(); renderConceptDetail(); renderPracticeFilters(); renderActivityList(); renderMaterials(); renderCheckFilters(); renderCheckQuestion(); renderCheckHistory(); showView(state.view,false);
  }
  function currentFlow() { return state.flows.find(flow => flow.id === state.flowId); }
  function currentFlowStep() { return currentFlow()?.steps?.find(step => step.id === state.flowStepId); }
  function currentTraceCase() { return currentFlow()?.trace_cases?.find(item => item.id === state.traceCaseId); }
  function selectFlowState(id) {
    state.flowId = id;
    const flow = currentFlow(), trace = flow?.trace_cases?.[0];
    state.traceCaseId = trace?.id || ""; state.traceIndex = 0;
    state.flowStepId = trace?.frames?.[0]?.step_id || flow?.steps?.[0]?.id || "";
  }
  function flowFields(entries) {
    const list = element("dl","flow-fields");
    entries.forEach(([label,value]) => { const row = element("div","flow-field"); append(row,element("dt","",label),element("dd","",textOf(value) || "学习包未提供")); list.append(row); });
    return list;
  }
  function evidenceDetails(evidence, previewFiles) {
    const details = element("details","details-section code-evidence");
    append(details,element("summary","","核对代码依据"),paragraph("这是导入时的只读源码快照，可按需核对这一段讲解。","source-caption"),sources(evidence,previewFiles));
    return details;
  }
  function flowPrompt() {
    return $("#agent-prompt").textContent + "\n\n请补充当前项目的项目导读 flows：先解释项目做什么，再围绕具体用户场景整理有序调用步骤；每步写明输入、执行动作、输出、为何进入下一步、关联 concept_ids 和源码 evidence。不要只罗列类或框架。附上合法与失败示例 trace_cases，逐步写 frames 中的 step_id、解释、变量名、类型、当前值、来源；说明 resources 的归属、生命周期、失败处理，以及 branches 的停止条件和被跳过步骤。依据当前源码和版本生成可预览的追加任务包，保留已有知识与作答。缺少证据时写明缺口，不伪造调用链或实时运行结果。";
  }
  function renderFlows() {
    const list = $("#flow-list"); list.replaceChildren();
    state.flows.forEach((flow,index) => {
      const item = button("",() => { selectFlowState(flow.id); state.flowReturn = null; renderFlows(); showView("flow",false); },"flow-button");
      item.dataset.flowId = flow.id; item.classList.toggle("active",flow.id === state.flowId); item.setAttribute("aria-pressed",String(flow.id === state.flowId));
      append(item,element("span","flow-list-number",String(index + 1).padStart(2,"0")),element("span","",flow.title)); list.append(item);
    });
    const paper = $("#flow-detail"); paper.replaceChildren(); const flow = currentFlow();
    if (!flow) { append(paper,element("h2","","这份内容还缺少功能导读"),paragraph("已有资料仍被保留。请让自己的 Agent 根据实际项目补齐方法链与输入案例，应用不会猜测调用关系。"),button("查看准备说明",() => openManagement())); return; }
    append(paper,element("p","eyebrow","功能 " + (state.flows.indexOf(flow)+1) + " / " + state.flows.length),element("h2","",flow.title),paragraph(flow.summary));
    const context = element("details","flow-context-details"); append(context,element("summary","","使用场景与最终结果"),flowFields([["使用场景",flow.scenario],["功能得到什么",flow.expected]])); paper.append(context);
    const traceSection = element("section","case-selection"), selectorLabel = element("label","trace-case-label"), select = element("select"); select.id = "trace-case-select";
    append(selectorLabel,element("span","","带着这一份输入，走过整条链"),select); traceSection.append(selectorLabel);
    (flow.trace_cases || []).forEach(item => { const option = element("option","",item.title); option.value = item.id; select.append(option); }); select.value = state.traceCaseId;
    select.addEventListener("change",() => { state.traceExpanded = false; state.traceCaseId = select.value; state.traceIndex = 0; state.flowStepId = currentTraceCase()?.frames?.[0]?.step_id || flow.steps[0]?.id || ""; renderFlowSteps(); renderFlowStepDetail(); renderTrace(); });
    const example = element("div","trace-input"); example.id = "trace-input"; traceSection.append(example);
    const jump = button("查看这份输入的传递过程 ↓",() => $("#trace-panel").scrollIntoView({block:"start",behavior:"auto"}),"button primary"); jump.id = "trace-jump"; traceSection.append(jump);
    traceSection.append(paragraph("以下过程是有源码依据的教学推演，不是项目的实时运行日志。","small-note")); paper.append(traceSection);
    const stepsSection = paperSection("功能结构与方法（含分支）",element("ol","flow-steps")); $("ol",stepsSection).id = "flow-steps";
    stepsSection.insertBefore(paragraph("这里整理正常与失败分支涉及的方法。本例实际的先后顺序和参数变化，见输入传递过程。","small-note"),$("ol",stepsSection)); paper.append(stepsSection);
    const detail = element("section","flow-step-detail"); detail.id = "flow-step-detail"; detail.setAttribute("aria-label","当前步骤的知识与设计理由"); paper.append(detail);
    const tracePanel = element("section","paper-section trace-section"); tracePanel.id = "trace-panel";
    tracePanel.append(element("h3","","这份输入的完整传递过程"));
    const controls = element("div","trace-controls"), prev = button("← 上一个参数位置",() => advanceTrace(-1)), next = button("下一个参数位置 →",() => advanceTrace(1)), position = element("span","trace-position");
    prev.id = "trace-prev"; next.id = "trace-next"; position.id = "trace-position"; append(controls,prev,position,next); tracePanel.append(controls);
    const frame = element("section","trace-frame"); frame.id = "trace-frame"; frame.setAttribute("aria-live","polite"); tracePanel.append(frame);
    const values = element("div","trace-values"); values.id = "trace-values"; values.hidden = true; tracePanel.append(values);
    const expand = button("展开全部参数",() => { state.traceExpanded = !state.traceExpanded; renderTrace(); },"button quiet"); expand.id = "trace-expand-all"; tracePanel.append(expand);
    const timeline = element("ol","trace-timeline"); timeline.id = "trace-timeline"; tracePanel.append(timeline);
    const outcome = element("div","trace-outcome"); outcome.id = "trace-outcome"; tracePanel.append(outcome); paper.append(tracePanel);
    append(paper,renderResources(flow.resources),renderBranches(flow.branches,flow.steps)); renderFlowSteps(); renderFlowStepDetail(); renderTrace();
  }
  function renderFlowSteps() {
    const list = $("#flow-steps"), flow = currentFlow(); if (!list || !flow) return; list.replaceChildren();
    const trace = currentTraceCase(), recorded = new Set((trace?.frames || []).map(frame => frame.step_id)), skipped = explicitSkippedSteps();
    (flow.steps || []).forEach((step,index) => {
      const row = element("li","flow-step"), item = button("",() => {
        state.flowStepId = step.id; state.traceIndex = (currentTraceCase()?.frames || []).findIndex(frame => frame.step_id === step.id);
        renderFlowSteps(); renderFlowStepDetail(); renderTrace(); $("#flow-step-detail")?.scrollIntoView({block:"start",behavior:"auto"});
      },"flow-step-button"); row.dataset.stepId = step.id;
      item.dataset.stepId = step.id; item.classList.toggle("active",step.id === state.flowStepId); item.setAttribute("aria-pressed",String(step.id === state.flowStepId));
      append(item,element("span","step-number",index+1),element("strong","",step.title),element("span","step-actor",step.actor)); row.append(item);
      row.append(paragraph(step.action,"step-action"));
      if (skipped.has(step.id)) { row.classList.add("step-not-reached"); row.append(paragraph("本例未调用：已在上游明确停止。","step-skip-label")); }
      else if (trace && !recorded.has(step.id)) { row.classList.add("step-no-frame"); row.append(paragraph("本例未记录本步参数；不能据此判断未调用。","step-skip-label")); }
      const methods = element("ol","method-chain");
      (step.methods || []).forEach(method => {
        const entry = element("li","method-node"); entry.dataset.methodId = method.id;
        const frame = currentTraceCase()?.frames?.[state.traceIndex]; entry.classList.toggle("active",frame?.step_id === step.id && frame?.method_id === method.id);
        const heading = element("div","method-heading"); append(heading,element("span","method-relation relation-"+method.relation,{call:"调用",return:"返回",stop:"停止"}[method.relation] || method.relation),element("span","method-signature",method.signature)); entry.append(heading);
        const details = element("details","method-explanation"); details.append(element("summary","","输入、输出与理由"));
        append(details,flowFields([["输入",method.input],["输出",method.output],["为什么",method.why]]),evidenceDetails(method.evidence)); entry.append(details); methods.append(entry);
      });
      if (methods.children.length) row.append(methods); else row.append(paragraph("当前导读仅记录了步骤职责，具体方法还需要补充。","small-note")); list.append(row);
    });
  }
  function renderFlowStepDetail() {
    const node = $("#flow-step-detail"), step = currentFlowStep(); if (!node) return; node.replaceChildren();
    if (!step) return;
    const index = currentFlow().steps.findIndex(item => item.id === step.id);
    append(node,element("p","eyebrow","当前查看 · 第 " + (index + 1) + " 步"),element("h3","",step.title),flowFields([["谁来处理",step.actor],["收到什么",step.input],["这一步做什么",step.action],["交出什么",step.output],["为什么这样走",step.why]]));
    const links = element("div","flow-concepts");
    (step.concept_ids || []).forEach(id => {
      const concept = state.concepts.find(item => item.id === id);
      const link = button("学习：" + (concept?.title || id) + " ↗",() => openFlowConcept(id),"topic-chip"); link.dataset.flowConceptId = id; links.append(link);
    });
    append(node,element("h4","","看懂这一步需要的知识"),links,evidenceDetails(step.evidence));
    if (!links.children.length) links.append(paragraph("这一步尚未关联知识讲义，可以让 Agent 补充。","small-note"));
  }
  function openFlowConcept(id) {
    const concept = state.concepts.find(item => item.id === id); if (!concept) { toast("当前学习包缺少这份知识讲义，请让 Agent 补充。",true); return; }
    state.knowledgeReturn = "flow"; state.flowReturn = {flowId:state.flowId,stepId:state.flowStepId,caseId:state.traceCaseId,index:state.traceIndex};
    state.conceptId = id; state.group = groupOf(concept); renderGroups(); renderConcepts(); renderConceptDetail(); showView("knowledge",false);
    $("#flow-context").scrollIntoView({block:"start",behavior:"auto"});
  }
  function renderFlowContext() {
    const context = state.flowReturn, flow = state.flows.find(item => item.id === context?.flowId), step = flow?.steps?.find(item => item.id === context?.stepId);
    $("#flow-context").hidden = !flow || !step;
    if (flow && step) { const position = flow.steps.findIndex(item => item.id === step.id) + 1; $("#flow-context-label").textContent = "来自：" + flow.title + " → 第 " + position + " 步「" + step.title + "」"; $("#flow-back").textContent = "← 返回第 " + position + " 步，继续跟踪"; }
  }
  function returnToFlow() {
    const context = state.flowReturn; if (!context) return;
    state.flowId = context.flowId; state.flowStepId = context.stepId; state.traceCaseId = context.caseId; state.traceIndex = context.index;
    renderFlows(); showView("flow",false); $("#flow-step-detail")?.scrollIntoView({block:"start",behavior:"auto"});
  }
  function advanceTrace(delta) {
    const frames = currentTraceCase()?.frames || []; if (!frames.length) return;
    state.traceIndex = Math.max(0,Math.min(frames.length - 1,state.traceIndex + delta)); state.flowStepId = frames[state.traceIndex].step_id;
    renderFlowSteps(); renderFlowStepDetail(); renderTrace();
  }
  function valuesTable(values) {
    const wrapper = element("div","trace-table-wrap"), table = element("table","trace-table");
    const caption = element("caption","sr-only","当前步骤的参数、类型、值与来源"), head = element("thead"), headings = element("tr");
    ["名字","类型","当前值","来源"].forEach(title => { const cell = element("th","",title); cell.scope = "col"; headings.append(cell); }); head.append(headings);
    const body = element("tbody"); (values || []).forEach(value => { const row = element("tr"); [value.name,value.type,value.value,value.source].forEach((content,index) => { const cell = element("td",index < 3 ? "parameter-value" : "",textOf(content)); cell.dataset.label = ["名字","类型","当前值","来源"][index]; row.append(cell); }); body.append(row); });
    append(table,caption,head,body); wrapper.append(table); return wrapper;
  }
  function renderTrace() {
    const select = $("#trace-case-select"), frameNode = $("#trace-frame"), timeline = $("#trace-timeline"); if (!select || !frameNode || !timeline) return;
    const trace = currentTraceCase(), frames = trace?.frames || [], frame = frames[state.traceIndex]; select.disabled = !trace;
    $("#trace-input").replaceChildren(flowFields([["示例输入",trace?.input || currentFlow()?.input || "尚未提供具体输入"]]));
    $("#trace-prev").disabled = !trace || state.traceIndex <= 0; $("#trace-next").disabled = !trace || state.traceIndex >= frames.length-1;
    $("#trace-position").textContent = frame ? "当前位置 " + (state.traceIndex+1) + " / " + frames.length : "这一步未记录参数";
    frameNode.replaceChildren(); timeline.replaceChildren(); $("#trace-outcome").replaceChildren();
    if (!trace) { frameNode.append(paragraph("当前功能尚未提供参数传递案例。","empty-note")); return; }
    frameNode.append(paragraph(frame ? "正在查看：" + (currentFlow().steps.find(s => s.id === frame.step_id)?.title || frame.step_id) + "。下面始终保留整段过程，可直接顺序阅读。" : "当前步骤没有参数记录，完整过程仍显示在下方。"));
    frames.forEach((event,index) => {
      const step = currentFlow().steps.find(s => s.id === event.step_id), method = step?.methods?.find(m => m.id === event.method_id), entry = element("li","trace-event");
      entry.dataset.stepId = event.step_id; if (event.method_id) entry.dataset.methodId = event.method_id; entry.classList.toggle("active",index === state.traceIndex);
      const heading = button((index+1)+". "+(method?.signature || step?.title || event.step_id),() => { state.traceIndex = index; state.flowStepId = event.step_id; renderFlowSteps(); renderFlowStepDetail(); renderTrace(); },"trace-event-heading");
      const parameters = element("details","event-parameters"); parameters.open = !!state.traceExpanded || index === state.traceIndex;
      append(parameters,element("summary","","查看本步参数（"+(event.values || []).length+" 项）"),valuesTable(event.values));
      append(entry,heading,paragraph(event.explanation),parameters); timeline.append(entry);
    });
    if (!frames.length) timeline.append(paragraph("当前案例没有逐方法参数记录，需要 Agent 补全。","empty-note"));
    $("#trace-outcome").append(flowFields([["最终结果",trace.result]]));
    const skipped = explicitSkippedSteps();
    if (skipped.size) $("#trace-outcome").append(paragraph("明确未调用的下游：" + currentFlow().steps.filter(s => skipped.has(s.id)).map(s => s.title).join(" → "),"trace-branch"));
    const missing = currentFlow().steps.filter(s => !skipped.has(s.id) && !frames.some(f => f.step_id === s.id));
    if (missing.length) $("#trace-outcome").append(paragraph("未记录参数的步骤："+missing.map(s => s.title).join("、")+"。缺少记录不等于这些步骤未执行。","small-note"));
    $("#trace-expand-all").textContent = state.traceExpanded ? "收起其他参数" : "展开全部参数";
  }
  function renderResources(resources) {
    const section = element("details","details-section flow-support"), list = element("div","resource-list");
    section.append(element("summary","","资源归属与生命周期 · " + (resources || []).length + " 项资源"));
    (resources || []).forEach(resource => { const item = element("article","resource-card"); append(item,element("h4","",resource.name),flowFields([["归谁管理",resource.ownership],["何时创建与结束",resource.lifecycle],["失败时怎样处理",resource.failure]])); list.append(item); });
    if (!list.children.length) list.append(paragraph("学习包尚未说明资源归属与生命周期，请让 Agent 补充。","small-note")); section.append(list); return section;
  }
  function renderBranches(branches, steps) {
    const section = element("details","details-section flow-support");
    section.append(element("summary","","失败与停止条件 · " + (branches || []).length + " 个分支"));
    (branches || []).forEach(branch => {
      const start = (steps || []).find(item => item.id === branch.from_step_id), item = element("article","branch-card");
      append(item,element("h4","",start?.title || branch.from_step_id),flowFields([["遇到这个条件",branch.condition],["结果",branch.outcome],["不会再执行",branch.skipped_step_ids?.length ? branch.skipped_step_ids.map(id => steps.find(step => step.id === id)?.title || id).join(" → ") : "本分支未跳过其他步骤"]])); section.append(item);
    });
    if (!(branches || []).length) section.append(paragraph("学习包未提供失败分支。不能据此判断项目已经覆盖所有异常情况。","small-note")); return section;
  }
  function renderGroups() {
    const node = $("#group-nav"); node.replaceChildren();
    const groups = [...new Set(state.concepts.map(groupOf))];
    groups.forEach(group => {
      const count = state.concepts.filter(c => groupOf(c) === group).length;
      const item = button("",() => { state.group = group; state.conceptId = state.concepts.find(c => groupOf(c) === group)?.id || ""; renderGroups(); renderConcepts(); renderConceptDetail(); if (state.view !== "knowledge") showView("knowledge"); },"group-button");
      item.classList.toggle("active",state.group === group); item.setAttribute("aria-pressed",String(state.group === group));
      append(item,element("span","",group),element("span","",count)); node.append(item);
    });
  }
  function renderConcepts() {
    const list = $("#concept-list"); list.replaceChildren(); $("#concept-group-title").textContent = state.group || "知识点";
    const concepts = state.concepts.filter(c => groupOf(c) === state.group);
    concepts.forEach(concept => { const item = button(concept.title,() => { state.conceptId = concept.id; renderConcepts(); renderConceptDetail(); },"concept-button"); item.dataset.conceptId = concept.id; item.classList.toggle("active",concept.id === state.conceptId); item.setAttribute("aria-pressed",String(concept.id === state.conceptId)); list.append(item); });
    if (!concepts.length) list.append(element("p","empty-note","当前版本还没有知识讲义。请把资料交给 Agent，生成并导入学习包。"));
  }
  function paragraph(text, className = "content-paragraph") { return element("p",className,textOf(text)); }
  function formattedPrompt(text) {
    const container = element("div","activity-prompt"), ticks = String.fromCharCode(96).repeat(3);
    const pattern = new RegExp(ticks + "(?:[A-Za-z0-9_+-]+)?\\r?\\n([\\s\\S]*?)" + ticks,"g");
    const source = String(text || ""); let position = 0, match;
    while ((match = pattern.exec(source)) !== null) {
      if (match.index > position) container.append(paragraph(source.slice(position,match.index).trim(),"prompt-paragraph"));
      container.append(element("pre","inline-code",match[1].replace(/\s+$/,"")));
      position = pattern.lastIndex;
    }
    if (position < source.length) container.append(paragraph(source.slice(position).trim(),"prompt-paragraph"));
    return container;
  }
  function paperSection(title, children) { const section = element("section","paper-section"); append(section,element("h3","",title),children); return section; }
  function sources(evidence, previewFiles) {
    const list = element("div","evidence-list");
    (evidence || []).forEach(item => { const link = button("",() => action(() => previewFiles ? openPreviewSource(item,previewFiles) : openSource(item),null),"source-link"); link.dataset.sourcePath = item.path; append(link,element("span","",safeRelative(item.path)),element("span","",item.line ? "第 " + item.line + " 行 ↗" : "查看 ↗")); list.append(link); });
    if (!list.children.length) list.append(element("p","source-caption","暂无直接来源，请让 Agent 根据实际资料补充依据。"));
    return list;
  }
  function referenceLinks(references) {
    const list = element("div","reference-list");
    (references || []).forEach(reference => {
      let url; try { url = new URL(String(reference.url || "")); } catch {}
      const item = element("div","reference-item");
      if (url && ["http:","https:"].includes(url.protocol)) {
        const link = element("a","reference-link",reference.title || url.hostname); link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer"; item.append(link);
      } else item.append(element("span","",reference.title || "来源链接不可用"));
      if (reference.note) item.append(paragraph(reference.note,"source-caption"));
      list.append(item);
    }); return list;
  }
  function conceptTeaching(concept) {
    const content = element("div","concept-teaching");
    if (concept.mechanism) content.append(paperSection("它怎样运行",formattedPrompt(textOf(concept.mechanism))));
    if (concept.lesson) content.append(paperSection("完整讲义",formattedPrompt(textOf(concept.lesson))));
    if (concept.why) content.append(paperSection("为什么这样设计",paragraph(concept.why)));
    if (concept.failures?.length) {
      const list = element("ul","failure-list"); (Array.isArray(concept.failures) ? concept.failures : [concept.failures]).forEach(failure => list.append(element("li","",textOf(failure)))); content.append(paperSection("常见错误与边界",list));
    }
    if (concept.references?.length) content.append(paperSection("参考来源",[paragraph("链接保留为出处；应用未联网核验内容。","source-caption"),referenceLinks(concept.references)]));
    return content;
  }
  function renderConceptDetail() {
    const paper = $("#concept-detail"); paper.replaceChildren(); const concept = currentConcept();
    if (!concept) { paper.append(element("p","empty-note","当前版本还没有知识讲义。可以通过内置说明，让自己的 Agent 补充这份项目资料。"),button("查看使用说明",() => openManagement())); return; }
    try { localStorage.setItem("shijie-concept-" + state.project.id,concept.id); } catch {}
    append(paper,element("p","eyebrow",groupOf(concept)),element("h2","",concept.title),paragraph(concept.explanation || "先查看代码出处，核对这个概念在项目中的实际职责。"));
    paper.append(conceptTeaching(concept));
    if (concept.prerequisites?.length) {
      const row = element("div","prerequisite-row");
      concept.prerequisites.forEach(item => {
        const linked = state.concepts.find(c => c.id === item), title = linked?.title || conceptNames[item] || textOf(item);
        if (linked) { const link = button(title,() => { state.conceptId = linked.id; state.group = groupOf(linked); renderGroups(); renderConcepts(); renderConceptDetail(); },"topic-chip"); row.append(link); }
        else row.append(element("span","topic-chip",title));
      }); paper.append(paperSection("需要连接起来的基础",row));
    }
    paper.append(evidenceDetails(concept.evidence));
    const activities = state.activities.filter(a => a.concept_id === concept.id && a.objective === true);
    const practice = paperSection("用小题巩固这个知识",paragraph(activities.length ? "当前有 "+activities.length+" 道相关客观题。按这个知识筛选，再开始逐题复习或测验。" : "目前尚无该知识的客观题。可以让 Agent 依据资料补充，不用其他题目填充。","source-caption"));
    if (activities.length) {
      const quiz = button("复习这个知识 →",() => { $("#check-concept-filter").value = concept.id; $("#check-scope").value = "all"; showView("check"); $("#check-settings").scrollIntoView({block:"start"}); },"button primary"); quiz.id = "concept-quiz"; practice.append(quiz);
    } paper.append(practice);
    paper.append(element("p","version-note","项目快照版本 " + projectVersion() + " · 自动识别用于定位知识，不证明功能已实现或你已独立掌握。"));
  }
  function renderPracticeFilters() {
    const select = $("#practice-concept-filter"), previous = select.value; select.replaceChildren(); const all = element("option","","全部知识点"); all.value = ""; select.append(all);
    state.concepts.forEach(concept => { const option = element("option","",concept.title); option.value = concept.id; select.append(option); });
    select.value = !state.practiceFilterInitialized ? state.conceptId : state.concepts.some(c => c.id === previous) ? previous : "";
    state.practiceFilterInitialized = true;
  }
  function filteredActivities() {
    const concept = $("#practice-concept-filter").value, kind = $("#practice-kind-filter").value;
    return state.activities.filter(activity => (!concept || activity.concept_id === concept) && (!kind || activity.kind === kind) && (!state.dueOnly || (activity.due_at && new Date(activity.due_at).getTime() <= Date.now()) || ["incorrect","review_required","stale"].includes(activity.status)));
  }
  function renderActivityList() {
    const list = $("#activity-list"); list.replaceChildren(); const activities = filteredActivities();
    $("#practice-filter-note").textContent = activities.length + " 项练习" + (state.dueOnly ? " · 待回看筛选中" : "");
    $("#practice-due").textContent = state.dueOnly ? "查看全部" : "待回看"; $("#practice-due").setAttribute("aria-pressed",String(state.dueOnly));
    activities.forEach(activity => { const item = button("",() => action(() => loadActivity(activity.id)),"activity-button"); item.dataset.activityId = activity.id; item.classList.toggle("active",state.activityId === activity.id); item.setAttribute("aria-current",state.activityId === activity.id ? "true" : "false"); append(item,element("span","",activity.title),element("small","",(kinds[activity.kind] || activity.kind) + " · " + (statuses[activity.status] || (activity.attempt_count ? "已有记录" : "尚未练习")))); list.append(item); });
    if (!activities.length) list.append(element("p","empty-note","这里暂时没有练习。调整筛选，或导入任务包。"));
  }
  async function selectFirstActivity() {
    const activities = filteredActivities();
    if (activities.length) await loadActivity(activities.some(a => a.id === state.activityId) ? state.activityId : activities[0].id);
    else { state.activityId = ""; state.activityDetail = null; $("#activity-detail").replaceChildren(element("p","empty-note","这里暂时没有练习。调整筛选，或导入任务包。")); }
  }
  async function loadActivity(id, focusResult = false) {
    const requestId = ++state.requestId, pid = state.project.id;
    const data = await api(projectURL("/activities/" + encodeURIComponent(id)));
    if (requestId !== state.requestId || pid !== state.project?.id) return;
    state.activityId = id; state.activityDetail = data; renderActivityList(); renderActivityDetail();
    if (focusResult) { const result = $("#attempt-result"); result?.focus({preventScroll:true}); result?.scrollIntoView({block:"nearest",behavior:"auto"}); }
  }
  function renderActivityDetail() {
    const paper = $("#activity-detail"); paper.replaceChildren();
    const data = state.activityDetail, activity = data?.activity; if (!activity) return;
    const history = data.history || [], latest = history[history.length - 1], hints = data.hints_requested || [];
    const draft = state.drafts.get(activity.id) || {answer:Array.isArray(latest?.answer) ? [...latest.answer] : latest?.answer || "",assistance:latest?.assistance || "none"}; state.drafts.set(activity.id,draft);
    append(paper,element("span","kind-label",kinds[activity.kind] || activity.kind),element("h2","",activity.title),formattedPrompt(activity.prompt));
    if (activity.due_at) { const overdue = new Date(activity.due_at).getTime() <= Date.now(); paper.append(element("div","due-banner",overdue ? "这项练习已到回看时间。先不查资料，再尝试一个答案。" : "下次回看：" + stamp(activity.due_at) + "。通过一次之后，仍需要延迟验证。")); }
    if (activity.evidence?.length) { const details = element("details","details-section"); append(details,element("summary","","查看题目依据与代码"),sources(activity.evidence)); paper.append(details); }
    const form = element("form","answer-form"); form.id = "answer-form";
    if (activity.options?.length) {
      const fieldset = element("fieldset","answer-options"), multi = activity.option_mode === "multiple";
      append(fieldset,element("legend","",multi ? "选择所有符合的项；需要完整匹配。" : "先选出你认为正确的一项。"));
      const selected = Array.isArray(draft.answer) ? draft.answer : [draft.answer];
      activity.options.forEach(option => {
        const label = element("label","answer-option"), input = element("input"); input.type = multi ? "checkbox" : "radio"; input.name = "answer"; input.value = String(option.id); input.checked = selected.includes(option.id) || selected.includes(String(option.id));
        input.addEventListener("change",() => { draft.answer = [...form.querySelectorAll('input[name="answer"]:checked')].map(i => i.value); });
        append(label,input,element("span","option-id",option.id),element("span","option-text",option.text)); fieldset.append(label);
      });
      form.append(fieldset);
    } else {
      const label = element("label","field-label","留下自己的解释或操作结果"); label.htmlFor = "answer-text";
      const area = element("textarea"); area.id = "answer-text"; area.name = "answer"; area.placeholder = "先说明你的判断和理由。需要手操时，请在自己的工具与环境中完成，再记录执行步骤、关键内容与结果。"; area.value = typeof draft.answer === "string" ? draft.answer : ""; area.required = true; area.maxLength = 12000; area.addEventListener("input",() => { draft.answer = area.value; });
      append(form,label,area,element("p","small-note","应用不执行粘贴的代码或命令。口述与代码答案只记录为待复核。"));
    }
    const assistance = element("fieldset","assistance-row"); assistance.style.borderLeft = "0"; assistance.style.borderRight = "0"; assistance.style.borderBottom = "0"; assistance.style.paddingLeft = "0"; assistance.style.paddingRight = "0"; assistance.append(element("legend","assistance-title","这次作答，实际借助了什么？"));
    Object.entries(helps).forEach(([value,text]) => { const label = element("label"), input = element("input"); input.type = "radio"; input.name = "assistance"; input.value = value; input.checked = draft.assistance === value; input.addEventListener("change",() => { draft.assistance = value; }); append(label,input,element("span","",text)); assistance.append(label); });
    form.append(assistance);
    const submit = element("button","button primary","保存本次作答"); submit.type = "submit"; submit.id = "answer-submit";
    const actionRow = element("div","answer-actions"); append(actionRow,submit,element("p","small-note","首答和每次修改都会保留。使用提示也会如实记录。")); form.append(actionRow);
    form.addEventListener("submit",event => {
      event.preventDefault();
      action(async () => {
        const answer = activity.options?.length ? [...form.querySelectorAll('input[name="answer"]:checked')].map(i => i.value) : $("#answer-text",form).value.trim();
        if (!answer.length) throw new Error("请先留下自己的答案。");
        const assistanceValue = $('input[name="assistance"]:checked',form)?.value || "none";
        await api(projectURL("/attempts"),"POST",{activity_id:activity.id,answer,assistance:assistanceValue});
        state.drafts.delete(activity.id); await refreshProject(); await loadActivity(activity.id,true); toast("本次作答已保存，首答没有被覆盖。");
      },submit,"正在保存…");
    });
    paper.append(form); renderHints(paper,activity,hints);
    if (latest) { renderResult(paper,latest); renderEvidenceForm(paper,latest,hints); }
    if (activity.rubric) {
      const details = element("details","details-section"), list = element("ul","rubric-list");
      (Array.isArray(activity.rubric) ? activity.rubric : [activity.rubric]).forEach(item => list.append(element("li","",textOf(item))));
      append(details,element("summary","","验收时需要说明什么"),list); paper.append(details);
    }
    renderHistory(paper,history); renderEvidenceRecords(paper,data.evidence || []);
    paper.append(element("p","version-note","快照版本 " + projectVersion() + " · 练习结果是证据的一部分，不会自动宣布“已掌握”。"));
  }
  function renderHints(paper, activity, hints) {
    const area = element("section","hint-area");
    append(area,element("h3","","需要帮助时，逐步展开"),element("p","small-note","每次提示都会留下记录，不影响继续练习。先保留一个自己的判断，会更容易发现缺口。"));
    let maximum = 0;
    hints.forEach(hint => { maximum = Math.max(maximum,Number(hint.level)); const block = element("div","hint-block"); append(block,element("strong","","提示 " + hint.level),element("span","",hint.text)); area.append(block); });
    const available = activity.hint_count ?? 3;
    if (maximum < available) {
      const next = maximum + 1, label = next === 1 ? "给我一个方向" : next === 2 ? "再具体一点" : "查看关键步骤";
      const hintButton = button(label,() => action(async () => { await api(projectURL("/hints"),"POST",{activity_id:activity.id,level:next}); await loadActivity(activity.id); toast("提示 " + next + " 已记录。"); },hintButton,"正在读取…"));
      hintButton.id = "hint-next"; hintButton.dataset.level = String(next); area.append(hintButton);
    } else area.append(element("p","small-note",available ? "全部提示已展开。稍后可以脱离提示再做，核验独立能力。" : "这项练习没有额外提示。可以查阅来源，记录实际使用的帮助。"));
    paper.append(area);
  }
  function renderResult(paper, attempt) {
    const result = element("section","result-box " + (attempt.status === "incorrect" ? "incorrect" : attempt.status === "review_required" ? "review" : "")); result.id = "attempt-result"; result.tabIndex = -1;
    const title = attempt.status === "correct" ? "这次固定答案核对符合" : attempt.status === "incorrect" ? "这次答案需要重看" : "已记录，等待你与 Agent 复核";
    const message = attempt.status === "correct" ? "这能证明本次选项与规则吻合。能否解释、排错和独立复现，还需要另外的证据。" : attempt.status === "incorrect" ? "把判断和执行过程对照起来。修正后的答案会作为新记录保存，首答仍然保留。" : "应用没有自动评分你的口述或代码。请结合源码、实际运行与下方验收要求核对。";
    append(result,element("h3","",title),paragraph(message),element("p","small-note","实际帮助：" + (helps[attempt.assistance] || attempt.assistance) + " · 已展开提示：" + (attempt.hint_level || 0) + " 级"));
    if (attempt.stale || (attempt.version != null && attempt.version !== state.project.version)) result.append(element("p","record-tag stale","这条记录基于旧版本，不能直接当作当前代码的验证。"));
    if (attempt.explanation) result.append(paragraph(attempt.explanation,"answer-explanation"));
    paper.append(result);
  }
  function numericField(name, text, value = "") { const label = element("label","",text), input = element("input"); input.type = "number"; input.name = name; input.min = "0"; input.max = "100000"; input.step = "1"; input.value = String(value); input.placeholder = "未运行：留空记 0"; append(label,input); return label; }
  function renderEvidenceForm(paper, attempt, hints) {
    const details = element("details","details-section"); details.id = "evidence-section";
    append(details,element("summary","","补充实际操作与测试证据"),element("p","small-note","记录你实际做过的操作。测试通过与独立掌握分别判断，不用填造出的数字。"));
    const form = element("form","evidence-form"); form.id = "evidence-form";
    const summaryLabel = element("label","","我实际做了什么"), summary = element("textarea"); summary.name = "summary"; summary.required = true; summary.maxLength = 12000; summary.placeholder = "入口、关键变量、操作步骤、失败原因、修改理由，以及实际结果。请不要粘贴密钥、完整个人路径或用户隐私。"; append(summaryLabel,summary); form.append(summaryLabel);
    const typeLabel = element("label","","验证方式"), type = element("select"); type.name = "test_type";
    const types = {none:"还没有运行测试",manual:"手动运行与口述",unit:"纯单元测试",integration:"多组件集成测试",application:"应用链路测试",http_stub:"本地模拟 HTTP 测试",other:"其他实际测试",mvc_slice:"MVC Slice 测试",spring:"Spring 容器测试",jdbc:"数据库组件测试"};
    Object.entries(types).forEach(([value,text]) => { const option = element("option","",text); option.value = value; type.append(option); }); append(typeLabel,type); form.append(typeLabel);
    const fields = element("div","evidence-fields"); append(fields,numericField("tests_run","Tests run"),numericField("passed","Passed"),numericField("failures","Failures"),numericField("errors","Errors"),numericField("skipped","Skipped")); form.append(fields);
    const scopeLabel = element("label","","这些证据能证明什么，还不能证明什么"), scope = element("textarea"); scope.name = "scope"; scope.required = true; scope.maxLength = 4000; scope.placeholder = "写清实际运行的环境与依赖、哪些部分使用了 Mock / Fake 或替身，以及尚未验证的范围。涉及 HTTP、数据库或模型时说明是否真实调用。"; append(scopeLabel,scope); form.append(scopeLabel);
    const independentLabel = element("label","checkbox-label"), independent = element("input"); independent.type = "checkbox"; independent.name = "independent";
    const hasHelp = hints.length || attempt.hint_level || attempt.assistance !== "none"; independent.disabled = Boolean(hasHelp);
    append(independentLabel,independent,element("span","",hasHelp ? "本轮已用过提示或外部帮助，记录为有帮助完成。可稍后脱离提示再做，验证独立能力。" : "我声明本次操作没有借助提示或外部帮助。这个声明仍需口述或复现核验。")); form.append(independentLabel);
    const submit = element("button","button quiet","保存证据记录"); submit.type = "submit"; submit.id = "evidence-submit"; form.append(submit);
    form.addEventListener("submit",event => {
      event.preventDefault();
      action(async () => {
        const values = new FormData(form), payload = {attempt_id:attempt.id,summary:String(values.get("summary")).trim(),test_type:String(values.get("test_type")),scope:String(values.get("scope")).trim(),independent:values.get("independent") === "on"};
        const names = ["tests_run","passed","failures","errors","skipped"], emptyCounts = names.every(name => String(values.get(name) || "").trim() === "");
        if (emptyCounts && !["none","manual"].includes(payload.test_type)) throw new Error("选择实际测试类型时，请填写本次真实测试数量。尚未运行请选“还没有运行测试”。");
        if (!emptyCounts && names.some(name => String(values.get(name) || "").trim() === "")) throw new Error("请完整填写五项测试数量。没有失败、错误或跳过时，明确填写 0。");
        names.forEach(name => { const value = String(values.get(name) || "").trim(); payload[name] = value === "" ? 0 : Number(value); });
        if (payload.tests_run !== payload.passed + payload.failures + payload.errors + payload.skipped) throw new Error("Tests run 应等于 Passed、Failures、Errors、Skipped 之和。");
        if (!payload.summary || !payload.scope) throw new Error("请写清实际操作和证据边界。");
        await api(projectURL("/evidence"),"POST",payload); await refreshProject(); await loadActivity(state.activityId); toast("证据已保存，独立能力仍需复核。");
      },submit,"正在保存…");
    });
    append(details,form); paper.append(details);
  }
  function renderHistory(paper, history) {
    if (!history.length) return;
    const details = element("details","details-section"), list = element("ol","history-list");
    append(details,element("summary","","首答与作答记录 · " + history.length + " 次"));
    history.forEach((attempt,index) => {
      const item = element("li","history-item"), meta = element("div","history-meta");
      append(meta,element("span","record-tag",index === 0 ? "原始首答" : "第 " + (index + 1) + " 次"),element("span","",stamp(attempt.created_at)),element("span","",statuses[attempt.status] || "已记录"),element("span","",(helps[attempt.assistance] || attempt.assistance) + " / 提示 " + (attempt.hint_level || 0) + " 级"));
      if (attempt.stale) meta.append(element("span","record-tag stale","版本已变化"));
      append(item,meta,element("div","history-answer",Array.isArray(attempt.answer) ? "所选项：" + attempt.answer.join("、") : attempt.answer));
      if (attempt.stale && attempt.question_snapshot) {
        const snapshot = element("details","historical-question");
        append(snapshot,element("summary","","查看当时的题目 · 版本 " + attempt.version),formattedPrompt(attempt.question_snapshot.prompt));
        item.append(snapshot);
      }
      list.append(item);
    });
    append(details,list); paper.append(details);
  }
  function renderEvidenceRecords(paper, evidence) {
    if (!evidence.length) return;
    const details = element("details","details-section"); append(details,element("summary","","实际证据记录 · " + evidence.length + " 条"));
    evidence.forEach(record => {
      const node = element("div","evidence-record");
      append(node,element("small","",stamp(record.created_at) + " · " + (record.test_type || "验证类型未记录") + " · " + (record.independent ? "独立完成自述，待核验" : "操作记录，待复核")),paragraph(record.summary),paragraph(record.scope || record.limitations));
      if (record.test_type === "none") node.append(element("small","","没有运行测试。以上内容是操作说明，不能视为测试通过。"));
      else if (record.tests_run != null) node.append(element("small","","Tests run " + record.tests_run + " · Passed " + (record.passed ?? "—") + " · Failures " + (record.failures ?? "—") + " · Errors " + (record.errors ?? "—") + " · Skipped " + (record.skipped ?? "—")));
      if (record.stale) node.append(element("span","record-tag stale","这条证据基于旧快照，需要重新核对。"));
      details.append(node);
    }); paper.append(details);
  }
  function renderMaterials() {
    const list = $("#materials-list"); list.replaceChildren();
    if (!state.materials.length) { list.append(element("p","small-note","当前还没有补充资料。支持 .md 与 .txt；来源将保留在 materials/ 中。")); return; }
    list.append(element("p","small-note","已保存 " + state.materials.length + " 份资料 · 当前版本 " + projectVersion()));
    state.materials.forEach(material => {
      const item = element("div","material-item"); append(item,sources([{path:material.path}]),element("small","muted",material.sha256 ? "文件校验 " + material.sha256.slice(0,12) + "…" : "项目资料快照")); list.append(item);
    });
  }
  async function uploadMaterials(files) {
    if (!files.length) return;
    const payload = [], maxFile = state.limits.max_file_bytes || 262144;
    for (const file of Array.from(files)) {
      if (!/\.(?:md|txt)$/i.test(file.name)) throw new Error("学习资料只接受 .md 或 .txt 文件。");
      if (file.size > maxFile) throw new Error("资料「" + file.name + "」超过单个文本上限。");
      const text = await file.text(); if (text.includes("\0")) throw new Error("资料中含有二进制内容，请选择文本文件。");
      payload.push({path:file.name,text});
    }
    const data = await api(projectURL("/materials"),"POST",{files:payload});
    state.activityId = ""; state.activityDetail = null; state.conceptId = ""; state.drafts.clear(); state.packDraft = null; $("#pack-preview").hidden = true; $("#materials-file").value = "";
    if (data.project) applyProject(data); else await refreshProject();
    toast("资料已更新。历史记录已保留，请按新版本让 Agent 重新生成学习包。");
    statusText("资料已保存 · 项目版本 " + projectVersion() + " · 请重新导出接手文件并生成知识与题目");
  }
  function clearCheck() {
    state.check = null; state.checkQuestions = []; state.checkAnswers.clear(); state.checkIndex = 0; state.checkHistory = []; state.checkResults = null; state.checkFeedback.clear(); state.checkRequestId++;
    $("#check-question").hidden = true; $("#check-result").hidden = true; $("#check-start").textContent = "开始复习";
  }
  function quizAnswerText(activity, ids) {
    if (!ids?.length) return "未作答";
    return ids.map(id => {
      const index = (activity.options || []).findIndex(option => String(option.id) === String(id));
      return index < 0 ? "本题原选项未记录" : String.fromCharCode(65 + index) + " · " + activity.options[index].text;
    }).join("；");
  }
  function renderCheckFilters() {
    const select = $("#check-concept-filter"), previous = select.value; select.replaceChildren(); const all = element("option","","全部知识点"); all.value = ""; select.append(all);
    state.concepts.forEach(concept => { const count = state.activities.filter(activity => activity.concept_id === concept.id && activity.objective).length; const item = element("option","",concept.title + " · " + count + " 题"); item.value = concept.id; select.append(item); });
    select.value = state.concepts.some(concept => concept.id === previous) ? previous : "";
    $("#check-pool-note").textContent = state.activities.length ? "每次随机抽取；可用题少于所选数量时，使用全部可用题。错题范围只看当前版本每题最近一次结果。" : "当前版本没有可检测题目。先让 Agent 整理学习包；应用不会凭空生成题目或能力结论。";
  }
  async function loadCheckHistory() {
    const pid = state.project?.id, version = state.project?.version; if (!pid) return;
    const [data, inventory] = await Promise.all([api(projectURL("/checks")),api(projectURL("/checks/inventory"))]);
    if (pid !== state.project?.id || version !== state.project?.version) return;
    state.checkHistory = data.checks || []; state.quizInventory = inventory; renderCheckHistory();
    $("#check-pool-note").textContent = "当前版本："+inventory.all+" 道客观题 · "+inventory.wrong+" 道错题 · "+inventory.due+" 道到期题。"+inventory.review_rule;
  }
  function renderCheckHistory() {
    const node = $("#check-history"); node.replaceChildren();
    if (!state.checkHistory.length) { node.append(element("p","empty-note","还没有检测记录。检测结果会保留版本与题目数量。")); return; }
    const list = element("ol","history-list");
    [...state.checkHistory].reverse().forEach(check => {
      const item = element("li","history-item"), meta = element("div","history-meta");
      append(meta,element("span","",stamp(check.finished_at || check.created_at)),element("span","","版本 " + (check.version ?? "—")));
      if (check.stale) meta.append(element("span","record-tag stale","旧版记录，需重新检测"));
      const finished = check.status === "finished";
      append(item,meta,paragraph(finished ? "本次 " + (check.score ?? 0) + "% · " + (check.correct_count ?? 0) + "/" + check.question_count + " 题符合 · " + (check.passed ? "检测通过" : "需要回看") : "尚未提交 · " + check.question_count + " 题","check-history-score"),element("small","muted","检测通过不等于独立掌握。"));
      if (!check.stale && !finished) {
        const label = check.mode === "practice" ? "继续这组复习（已保存 "+(check.answered_count || 0)+"/"+check.question_count+" 题）" : "继续这组测验（原有 "+check.question_count+" 题）";
        const resume = button(label,() => action(() => resumeCheck(check.id),resume,"正在恢复…")); resume.dataset.resumeCheck = check.id; item.append(resume);
      }
      if (check.results?.length) {
        const details = element("details","historical-question"); details.append(element("summary","","查看当次答案与讲解"));
        const content = element("div"), pid = state.project.id; details.append(content);
        details.addEventListener("toggle",async () => {
          if (!details.open || details.dataset.loaded) return;
          details.dataset.loaded = "loading"; content.replaceChildren(paragraph("正在读取当次题目…","small-note"));
          try {
            const saved = await api("/api/projects/"+encodeURIComponent(pid)+"/checks/"+encodeURIComponent(check.id));
            if (!details.isConnected || pid !== state.project?.id) return;
            content.replaceChildren();
            check.results.forEach((result,index) => {
              const activity = saved.questions.find(question => question.id === result.activity_id) || {}, entry = element("section","paper-section");
              append(entry,element("h3","","第 " + (index + 1) + " 题 · " + (activity.title || "历史题目") + " · " + (result.correct ? "符合" : "需要重看")),formattedPrompt(activity.prompt),paragraph("你的答案："+quizAnswerText(activity,result.answer)),paragraph("固定答案："+quizAnswerText(activity,result.answer_ids)));
              if (result.explanation) entry.append(formattedPrompt(result.explanation)); content.append(entry);
            }); details.dataset.loaded = "true";
          } catch (error) { delete details.dataset.loaded; content.replaceChildren(paragraph(error.message,"small-note")); }
        }); item.append(details);
      } list.append(item);
    }); node.append(list);
  }
  async function startCheck() {
    const count = Number($("#check-count").value), scope = $("#check-scope").value, concept = $("#check-concept-filter").value, mode = $("#check-mode").value;
    if (!Number.isInteger(count) || count < 1 || count > 30) throw new Error("题数必须是 1 到 30 之间的整数。");
    if (state.check?.status === "active" && state.checkAnswers.size) { const approved = await confirmAction("开始新一组","当前一组尚未完成。已提交的首答仍会保留，未提交答案会离开当前页面。","开始新一组"); if (!approved) return; }
    const pid = state.project.id, requestId = ++state.checkRequestId, body = {count,scope,mode}; if (concept) body.concept_id = concept;
    const data = await api(projectURL("/checks/start"),"POST",body);
    if (pid !== state.project?.id || requestId !== state.checkRequestId) return;
    state.check = data.check; state.checkQuestions = data.questions || []; state.checkIndex = 0; state.checkAnswers.clear(); state.checkFeedback.clear(); state.checkResults = null; $("#check-result").hidden = true;
    $("#check-pool-note").textContent = "本次 " + state.checkQuestions.length + " 题 · "+(mode === "practice" ? "答后立即反馈，首答不能改写。" : "完整作答后核对，通过线 80%。");
    renderCheckQuestion(); await loadCheckHistory();
  }
  async function resumeCheck(id) {
    const pid = state.project.id, version = state.project.version, requestId = ++state.checkRequestId;
    const data = await api(projectURL("/checks/"+encodeURIComponent(id)));
    if (pid !== state.project?.id || version !== state.project?.version || requestId !== state.checkRequestId) return;
    if (data.check.stale || data.check.status !== "active") throw new Error("这组练习不能继续，请按当前资料开始新一组。");
    state.check = data.check; state.checkQuestions = data.questions; state.checkIndex = data.check.current_index || 0; state.checkAnswers.clear(); state.checkFeedback.clear();
    (data.check.results || []).forEach(result => { state.checkAnswers.set(result.activity_id,result.answer); state.checkFeedback.set(result.activity_id,result); });
    state.checkResults = data.check.results; $("#check-result").hidden = true; $("#check-mode").value = data.check.mode || "exam";
    renderCheckQuestion(); $("#check-question").scrollIntoView({block:"start"}); toast(data.check.mode === "practice" ? "已恢复原题目顺序和保存过的首答。" : "已恢复当次原题与选项顺序。测验尚未提交的答案需重新选择。");
  }
  function renderCheckQuestion() {
    const paper = $("#check-question"); paper.replaceChildren(); const activity = state.checkQuestions[state.checkIndex], practice = state.check?.mode === "practice";
    const feedback = state.checkFeedback.get(activity?.id);
    paper.hidden = !activity || (state.check?.status !== "active" && !(practice && feedback)); if (paper.hidden) { delete paper.dataset.activityId; return; } paper.dataset.activityId = activity.id;
    const selected = state.checkAnswers.get(activity.id) || [], multi = activity.option_mode === "multiple";
    const progress = element("div","check-progress"); append(progress,element("span","kind-label","第 " + (state.checkIndex+1) + " / " + state.checkQuestions.length + " 题"),element("span","muted","已作答 " + (practice ? state.check?.answered_count || 0 : state.checkAnswers.size) + " / " + state.checkQuestions.length));
    const concept = state.concepts.find(item => item.id === activity.concept_id);
    // The choice legend already explains single/multiple selection. Keep the
    // stored prompt intact, but avoid repeating these exact generated headers.
    const prompt = activity.prompt.replace(/^(?:单选题：选择最准确的一项。|多选题：选择所有成立的说法。)\r?\n\r?\n/u,"");
    append(paper,progress,paragraph((concept?.title || "项目知识") + " · " + (kinds[activity.kind] || "理解与复习"),"quiz-context"),element("h2","",activity.title),formattedPrompt(prompt));
    const fieldset = element("fieldset","answer-options"); fieldset.disabled = !!feedback;
    append(fieldset,element("legend","",multi ? "多选：选出所有符合项；少选、多选均不通过。" : "单选：选择一项。"));
    (activity.options || []).forEach((option,index) => {
      const label = element("label","answer-option"), input = element("input"); input.type = multi ? "checkbox" : "radio"; input.name = "check-answer"; input.value = String(option.id); input.checked = selected.includes(String(option.id));
      if (feedback) { label.classList.toggle("option-correct",feedback.answer_ids.includes(String(option.id))); label.classList.toggle("option-wrong",input.checked && !feedback.answer_ids.includes(String(option.id))); }
      input.addEventListener("change",() => { const answer = [...fieldset.querySelectorAll('input:checked')].map(n => n.value); if (answer.length) state.checkAnswers.set(activity.id,answer); else state.checkAnswers.delete(activity.id); renderCheckProgress(); });
      append(label,input,element("span","option-id",String.fromCharCode(65 + index)),element("span","option-text",option.text)); fieldset.append(label);
    }); paper.append(fieldset);
    const nav = element("div","button-row check-pagination");
    if (practice) {
      const label = element("label","checkbox-label guess-label"), guessed = element("input"); guessed.type = "checkbox"; guessed.id = "check-guessed"; guessed.disabled = !!feedback; guessed.checked = !!feedback?.guessed; append(label,guessed,element("span","","这题是猜的，稍后再复习")); paper.append(label);
      const submit = button("确认答案",() => action(answerCheck,submit,"正在核对…"),"button primary"); submit.id = "check-answer-submit"; submit.disabled = !!feedback || !selected.length;
      const next = button(state.checkIndex === state.checkQuestions.length-1 ? "查看本次结果" : "下一题 →",() => { if (state.checkIndex < state.checkQuestions.length-1) { state.checkIndex++; renderCheckQuestion(); } else { paper.hidden = true; renderCheckResult(); $("#check-result").scrollIntoView({block:"start"}); } },"button quiet"); next.id = "check-next"; next.disabled = !feedback;
      append(nav,submit,next); paper.append(nav);
      if (feedback) renderSingleFeedback(paper,activity,feedback);
    } else {
      const prev = button("上一题",() => { state.checkIndex--; renderCheckQuestion(); }); prev.id = "check-prev"; prev.disabled = state.checkIndex === 0;
      const next = button("下一题",() => { state.checkIndex++; renderCheckQuestion(); }); next.id = "check-next"; next.disabled = state.checkIndex >= state.checkQuestions.length-1;
      const submit = button("提交整组测验",() => action(submitCheck,submit,"正在核对…"),"button primary"); submit.id = "check-submit"; append(nav,prev,next,submit); paper.append(nav);
    } renderCheckProgress();
  }
  function renderCheckProgress() {
    const practice = state.check?.mode === "practice", note = $(".check-progress .muted"); if (note) note.textContent = "已作答 " + (practice ? state.check?.answered_count || 0 : state.checkAnswers.size) + " / " + state.checkQuestions.length;
    const submit = $("#check-submit"); if (submit) { submit.disabled = state.checkAnswers.size !== state.checkQuestions.length; submit.textContent = submit.disabled ? "答完 "+state.checkQuestions.length+" 题后提交" : "提交整组测验"; }
    const single = $("#check-answer-submit"), activity = state.checkQuestions[state.checkIndex]; if (single) single.disabled = !!state.checkFeedback.get(activity?.id) || !state.checkAnswers.get(activity?.id)?.length;
  }
  async function submitCheck() {
    if (!state.check || state.check.status !== "active") throw new Error("这次检测已经结束，请重新抽题。");
    if (state.checkAnswers.size !== state.checkQuestions.length) throw new Error("请完成全部题目后提交。");
    const pid = state.project.id, checkId = state.check.id;
    const answers = state.checkQuestions.map(activity => ({activity_id:activity.id,answer:state.checkAnswers.get(activity.id)}));
    const data = await api(projectURL("/checks/" + encodeURIComponent(checkId) + "/submit"),"POST",{answers});
    if (pid !== state.project?.id || state.check?.id !== checkId) return;
    state.check = data.check; state.checkResults = data.results || []; renderCheckQuestion(); renderCheckResult();
    await refreshProject(); await loadCheckHistory(); toast(data.check.passed ? "本次检测通过。继续核验解释与独立复现。" : "本次检测已保存，可按错题回看讲解与来源。");
  }
  function renderCheckResult() {
    const node = $("#check-result"); node.replaceChildren(); const check = state.check; node.hidden = check?.status !== "finished"; if (node.hidden) return;
    const summary = element("section","result-box " + (check.passed ? "" : "incorrect"));
    append(summary,element("h2","",check.passed ? "本次检测通过" : "本次检测需要回看"),paragraph("正确率 " + check.score + "% · " + check.correct_count + "/" + check.question_count + " 题符合 · 通过线 " + (check.threshold ?? 80) + "%"),paragraph("达到 80% 只说明这组客观题通过。能否独立解释、排错与复现，仍未由本次检测证明。"));
    if (check.stale) summary.append(element("p","record-tag stale","这次结果来自旧版本，请按当前资料重新检测。"));
    summary.append(button("再做一组随机检测",() => action(startCheck,$("#check-start"),"正在抽题…"),"button primary")); node.append(summary);
    const results = element("div","check-feedback-list");
    (state.checkResults || []).forEach((result,index) => {
      const activity = state.checkQuestions.find(question => question.id === result.activity_id) || {}, details = element("details","check-feedback " + (result.correct ? "" : "incorrect")); details.open = !result.correct;
      append(details,element("summary","",(index + 1) + ". " + (activity.title || "检测题目") + " · " + (result.correct ? "符合" : "需要重看")),formattedPrompt(activity.prompt));
      append(details,paragraph("你的答案：" + quizAnswerText(activity,result.answer || state.checkAnswers.get(result.activity_id))),paragraph("固定答案：" + quizAnswerText(activity,result.answer_ids)));
      if (result.explanation) append(details,element("h3","","答案讲解"),formattedPrompt(result.explanation));
      const concept = state.concepts.find(item => item.id === activity.concept_id);
      const evidence = activity.evidence?.length ? activity.evidence : concept?.evidence;
      if (evidence?.length) append(details,element("h3","","知识与题目来源"),sources(evidence));
      if (concept) details.append(button("回看知识：" + concept.title,() => { state.flowReturn = null; state.knowledgeReturn = "check"; state.conceptId = concept.id; state.group = groupOf(concept); renderGroups(); renderConcepts(); renderConceptDetail(); showView("knowledge"); }));
      results.append(details);
    }); node.append(results);
  }
  function showView(view, selectActivity = true) {
    if (!["overview","flow","reflection","knowledge","check"].includes(view)) view = "overview";
    state.view = view;
    document.querySelectorAll("[data-view]").forEach(node => { if (node.dataset.view === view) node.setAttribute("aria-current","page"); else node.removeAttribute("aria-current"); });
    ["overview","flow","reflection","knowledge","practice","check","handoff"].forEach(name => { $("#"+name+"-view").hidden = name !== view; });
    $("#flow-navigation").hidden = view !== "flow"; $("#group-nav").hidden = view !== "knowledge";
    $("#workspace").classList.toggle("reading-wide",view !== "flow" && view !== "knowledge");
    $("#stage-guide").textContent = {overview:"先说清项目解决的问题，再进入一个功能。",flow:"带着具体输入，逐个方法看它怎么走。",reflection:"回忆、复述、留下疑问。记录按项目版本保存。",check:"一次一题，巩固知识。选择题通过不等于独立掌握。",knowledge:"从方法走进知识，再回到同一位置。"}[view];
    renderFlowContext();
    $("#knowledge-return").hidden = view !== "knowledge" || state.knowledgeReturn !== "check";
    if (!state.project) return;
    if (view === "check" && selectActivity) action(loadCheckHistory);
    if (view === "reflection" && selectActivity) action(loadReview);
  }
  async function openSource(evidence) {
    $("#source-eyebrow").textContent = String(evidence.path).startsWith("materials/") ? "项目学习资料快照" : "项目源码快照"; $("#source-title").textContent = safeRelative(evidence.path); $("#source-note").textContent = "正在读取本机快照…"; $("#source-content").replaceChildren(element("p","empty-note","正在读取…")); openDialog("source-dialog");
    try {
      const source = await api(projectURL("/source?path=" + encodeURIComponent(evidence.path)));
      $("#source-title").textContent = safeRelative(source.path);
      $("#source-note").textContent = "版本 " + (source.version ?? projectVersion()) + (evidence.line ? " · 来源第 " + evidence.line + " 行" : "") + " · 只读快照。" + (source.sha256 ? "文件校验 " + source.sha256.slice(0,12) + "…" : "");
      const content = $("#source-content"); content.replaceChildren();
      String(source.text || "").split("\n").forEach((line,index) => { const row = element("div","source-line"); row.classList.toggle("highlight",index + 1 === Number(evidence.line)); append(row,element("span","source-line-number",index + 1),element("span","source-line-text",line || " ")); content.append(row); });
      setTimeout(() => { const highlight = $(".source-line.highlight",content); if (highlight) content.scrollTop = Math.max(0,highlight.offsetTop - content.offsetTop - 90); },10);
    } catch (error) { $("#source-content").replaceChildren(element("p","empty-note",error.message)); $("#source-note").textContent = "未能读取来源。"; throw error; }
  }
  function openPlain(title, text, note = "") { $("#source-eyebrow").textContent = "本机内容预览"; $("#source-title").textContent = title; $("#source-note").textContent = note; $("#source-content").replaceChildren(element("pre","source-plain",text)); openDialog("source-dialog"); }
  async function importResult(data, report = "") {
    state.activityId = ""; state.activityDetail = null; state.conceptId = ""; state.group = ""; state.dueOnly = false; state.practiceFilterInitialized = false; state.drafts.clear(); state.packDraft = null;
    state.requestId++; applyProject(data); showView("overview",false); $("#project-path").value = ""; $("#snapshot-upload").value = ""; $("#pack-preview").hidden = true;
    const scan = data.project?.scan_report;
    if (scan) report = "快照实际保留 " + scan.included + " 个文件，跳过 " + (scan.skipped || 0) + " 个不符合导入规则的文件。" + (report ? "\n" + report : "");
    if (report) { $("#import-report").textContent = report; $("#import-report").hidden = false; }
    if ($("#update-current")) $("#update-current").checked = false;
    closeDialog("manage-dialog"); toast("学习内容已准备好。先看项目总结，再进入一个功能。");
  }
  async function demo(buttonNode) { await action(async () => { const data = await api("/api/projects/demo","POST",{}); await importResult(data); },buttonNode,"正在准备案例…"); }
  function allowedFile(path) {
    const parts = path.replaceAll("\\","/").split("/");
    if (parts.some(part => /^(?:\.git|\.idea|node_modules|target|build|dist|\.venv|venv|\.gradle|out|coverage|\.next|__pycache__)$/i.test(part))) return false;
    const name = parts[parts.length - 1]; if (/^(?:\.env|application(?:-[^.]+)?\.|secrets?|credentials?|id_rsa|keystore)/i.test(name)) return false;
    return /\.(?:java|py|js|mjs|cjs|ts|tsx|jsx|go|rs|c|h|cc|cpp|cxx|hpp|cs|sql|html|htm|css|scss|md|txt)$/i.test(name) || /^(?:pom\.xml|(?:build|settings)\.gradle(?:\.kts)?)$/i.test(name);
  }
  async function folderUpload(files) {
    if (!files.length) return;
    statusText("正在读取选中的文件夹，只导入源码与说明文件…");
    const source = Array.from(files), rootName = source[0].webkitRelativePath?.split("/")[0] || "我的项目", candidates = source.filter(file => allowedFile(file.webkitRelativePath || file.name));
    const maxFiles = state.limits.max_files || 180, maxFile = state.limits.max_file_bytes || 262144, maxTotal = state.limits.max_total_bytes || 8388608;
    const oversized = candidates.filter(file => file.size > maxFile), selected = candidates.filter(file => file.size <= maxFile);
    if (!selected.length) throw new Error("没有找到可导入的文本源码或说明文件。请检查项目文件夹，或让 Agent 整理带来源文件的完整学习包。");
    if (selected.length > maxFiles || selected.reduce((sum,file) => sum + file.size,0) > maxTotal) throw new Error("允许的文件超过快照上限。请先选择一个更小的模块（最多 " + maxFiles + " 个文件、总计 " + Math.round(maxTotal / 1048576) + " MB）。");
    const payload = []; let binaryCount = 0;
    for (const file of selected) { const text = await file.text(); if (text.includes("\0")) { binaryCount++; continue; } const relative = file.webkitRelativePath || file.name; payload.push({path:relative.includes("/") ? relative.slice(relative.indexOf("/") + 1) : relative,text}); }
    if (!payload.length) throw new Error("没有可读取的文本源码。");
    const upload = {name:rootName,files:payload};
    if ($("#update-current")?.checked && state.project) upload.project_id = state.project.id;
    const data = await api("/api/projects/upload","POST",upload);
    await importResult(data,"已导入 " + payload.length + " 个文件。跳过 " + (source.length - candidates.length) + " 个非源码或受保护文件、" + oversized.length + " 个超限文件、" + binaryCount + " 个二进制文件。");
    if (oversized.length || binaryCount) toast("已导入。另有 " + (oversized.length + binaryCount) + " 个文件未读取，请在项目管理中核对。");
  }
  function updatePrompt() {
    const authorization = $("#allow-research").checked;
    const context = offlineMode ? "我使用的是拾阶独立手机离线版，没有对外 HTTP 服务。请读取开源应用 apps/shijie/prompts/reference.md 的 schema_version=2 协议和我提供的项目、资料或接手 JSON。首次生成完整学习包 JSON；后续生成绑定当前版本与来源哈希的补充任务包 JSON。由我在手机‘使用说明’里预览并确认导入。不要尝试访问手机的 appassets 地址、调用本机 API、扫描手机目录或改写我的原项目；上述提示词中的桌面接口操作在此模式均改为输出 JSON 文件。" : "拾阶本机应用地址：" + location.origin + "。应用只在本机提供资料，不会自动请求外部模型。";
    const research = authorization ? "allow_research=true：我明确授权你在资料不足时联网查找官方文档和一手来源。请列出来源，区分项目事实与补充资料，遵守来源转载边界。" : "allow_research=false：未授权联网。仅使用我提供的项目与资料；缺少依据时说明缺口，先等我补充。";
    const firstRun = (state.coachPrompts.onboarding || "请帮我为自己选择的项目整理拾阶完整学习包。先读取拾阶应用目录的 prompts/first-run.md，或 GET " + location.origin + "/api/coach-prompt?mode=onboarding 获取正式要求。询问我选择的项目与资料，先解释项目功能与具体用户场景，再整理有源码依据的有序调用链和逐步参数示例；为每个步骤关联完整知识讲义、运行机制、设计理由、常见错误、客观题固定答案和开放题验收要求。学习包预览确认后再导入。不得虚构源码、测试与独立掌握，不改写原项目或覆盖学习记录。").replace(/allow_research\s*=\s*(?:true|false)/g,"allow_research=" + authorization);
    $("#onboarding-prompt").textContent = [firstRun,context,research].join("\n\n");
    const learning = (state.coachPrompts.learning || "请读取我导出的拾阶接手 JSON，再读取应用目录的 prompts/project-coach.md，或 GET " + location.origin + "/api/coach-prompt?mode=learning 获取正式要求。依据实际项目、学习资料和历史证据，先整理具体功能场景、有序调用链与逐步参数示例，再为步骤关联不限语言的知识讲义与练习。解释概念、机制、设计理由和常见错误，并用渐进提示推进；固定答案符合不等于独立掌握。输出可预览的结构化任务包，按接手文件的 schema 与当前版本填写真实来源，不修改原项目或覆盖记录。").replace(/allow_research\s*=\s*(?:true|false)/g,"allow_research=" + authorization);
    const project = state.project ? "当前项目：" + state.project.name + "；project_id=" + state.project.id + "；project_version=" + projectVersion() + "。资料改变后需要按这个版本重新核对和生成学习包。" : "项目由我选择，请先确认我的学习目标与可用资料。";
    $("#agent-prompt").textContent = [learning,context,project,research].join("\n\n");
  }
  async function loadCoachPrompts() {
    const results = await Promise.allSettled([api("/api/coach-prompt?mode=onboarding"),api("/api/coach-prompt?mode=learning")]);
    ["onboarding","learning"].forEach((mode,index) => { const result = results[index]; if (result.status === "fulfilled" && typeof result.value.prompt === "string") state.coachPrompts[mode] = result.value.prompt; });
    updatePrompt();
  }
  async function readJSON(file, maxBytes) {
    if (!file) return null;
    if (maxBytes && file.size > maxBytes) throw new Error("文件超过本机服务允许的 " + Math.round(maxBytes / 1048576) + " MB 上限。");
    try { return JSON.parse(await file.text()); } catch { throw new Error("文件不是有效的 JSON，请选择应用备份或 Agent 生成的学习包与任务包。"); }
  }
  function previewWarnings(node, warnings) {
    if (!warnings?.length) return;
    const list = element("ul","warning-list"); warnings.forEach(warning => list.append(element("li","",textOf(warning)))); append(node,element("strong","","需要核对"),list);
  }
  function previewConcepts(node, concepts, previewFiles) {
    if (!concepts?.length) return;
    node.append(element("h3","","知识讲义 · " + concepts.length + " 个"));
    const list = element("ul","preview-list");
    concepts.forEach(concept => {
      const item = element("li"), details = element("details","pack-task-detail");
      append(details,element("summary","",concept.title),element("small","",groupOf(concept) + " · " + concept.id),paragraph(concept.explanation || ""),conceptTeaching(concept));
      if (concept.prerequisites?.length) details.append(paragraph("需要先了解：" + concept.prerequisites.join("、")));
      details.append(evidenceDetails(concept.evidence,previewFiles)); item.append(details); list.append(item);
    }); node.append(list);
  }
  function previewActivities(node, activities, previewFiles) {
    const list = element("ul","preview-list");
    (activities || []).forEach(activity => {
      const item = element("li"), review = element("details","pack-task-detail");
      append(review,element("summary","",activity.title + " · " + (kinds[activity.kind] || activity.kind)),element("small","",activity.concept_id || "知识点待核对"),formattedPrompt(activity.prompt));
      if (activity.options?.length) {
        const options = element("ul","rubric-list"); activity.options.forEach(option => options.append(element("li","",option.id + "：" + option.text)));
        append(review,element("strong","","选项与固定答案"),options,paragraph("固定答案 ID：" + (activity.answer_ids || []).join("、")));
      } else review.append(paragraph("开放题，保存为待复核，不自动精确评分。","small-note"));
      if (activity.explanation) append(review,element("strong","","提交后的标准讲解"),formattedPrompt(activity.explanation));
      if (activity.rubric?.length) { const rubric = element("ul","rubric-list"); activity.rubric.forEach(point => rubric.append(element("li","",textOf(point)))); append(review,element("strong","","验收要求"),rubric); }
      if (activity.hints?.length) { const hints = element("ol","rubric-list"); activity.hints.forEach(hint => hints.append(element("li","",textOf(hint)))); append(review,element("strong","","渐进提示"),hints); }
      if (activity.references?.length) append(review,element("strong","","参考来源"),referenceLinks(activity.references));
      review.append(evidenceDetails(activity.evidence,previewFiles)); item.append(review); list.append(item);
    }); node.append(list);
  }
  function openPreviewSource(evidence, files) {
    const source = files.find(file => file.path === evidence.path); if (!source) throw new Error("预览文件中找不到这份来源，请核对学习包。");
    openPlain(safeRelative(source.path),source.text,"完整学习包预览 · 来源" + (evidence.line ? "第 " + evidence.line + " 行" : "") + " · 尚未写入项目");
  }
  function previewFlows(node, flows, previewFiles, concepts = []) {
    if (!flows?.length) { node.append(paragraph("当前内容缺少项目导读：导入后可以阅读知识与练习，但需要让 Agent 补充有源码依据的功能场景、调用顺序与参数跟踪。","small-note")); return; }
    node.append(element("h3","","项目导读 · " + flows.length + " 个功能场景"));
    flows.forEach(flow => {
      const review = element("details","pack-task-detail flow-review"); append(review,element("summary","",flow.title),paragraph(flow.summary),flowFields([["具体场景",flow.scenario],["输入",flow.input],["期待结果",flow.expected]]));
      const steps = element("ol","preview-flow-steps");
      (flow.steps || []).forEach(step => {
        const item = element("li"); append(item,element("h4","",step.title),flowFields([["谁来处理",step.actor],["收到什么",step.input],["做什么",step.action],["交出什么",step.output],["为什么进入下一步",step.why]]));
        item.append(paragraph("关联知识：" + (step.concept_ids || []).map(id => concepts.find(concept => concept.id === id)?.title || state.concepts.find(concept => concept.id === id)?.title || id).join("、"),"small-note"));
        item.append(evidenceDetails(step.evidence,previewFiles)); steps.append(item);
      }); append(review,element("h4","","调用顺序"),steps);
      (flow.trace_cases || []).forEach(trace => {
        const traceReview = element("details","pack-task-detail preview-trace"); append(traceReview,element("summary","","参数示例：" + trace.title),flowFields([["输入",trace.input],["最终结果",trace.result]]));
        (trace.frames || []).forEach((frame,index) => {
          const step = (flow.steps || []).find(item => item.id === frame.step_id); append(traceReview,element("h4","","参数快照 " + (index + 1) + " · " + (step?.title || frame.step_id)),paragraph(frame.explanation),valuesTable(frame.values));
        }); review.append(traceReview);
      });
      append(review,renderResources(flow.resources),renderBranches(flow.branches,flow.steps),paragraph("这是根据当前源码整理的示例过程，不是实时运行日志。","small-note")); node.append(review);
    });
  }
  async function previewBundle(file) {
    state.bundleDraft = null; state.bundleFiles = []; $("#bundle-preview").hidden = true;
    const bundle = await readJSON(file,state.limits.max_bundle_bytes || state.limits.max_backup_bytes); if (!bundle) return;
    const data = await api("/api/bundles/preview","POST",{bundle}); state.bundleDraft = data;
    state.bundleFiles = bundle.files || bundle.source_files || bundle.project?.files || [];
    const node = $("#bundle-preview"); node.replaceChildren(); node.hidden = false;
    append(node,element("h3","",data.title || "完整学习包预览"),paragraph("项目「" + data.project_name + "」 · " + data.file_count + " 个来源文件 · " + (data.flow_count ?? data.flows?.length ?? 0) + " 个功能场景 · " + data.concept_count + " 个知识点 · " + data.activity_count + " 项练习"));
    previewWarnings(node,data.warnings); previewFlows(node,data.flows,state.bundleFiles,data.concepts); previewConcepts(node,data.concepts,state.bundleFiles);
    node.append(element("h3","","练习与固定答案")); previewActivities(node,data.activities,state.bundleFiles);
    const row = element("div","button-row"), commit = button("确认写入完整学习包",() => action(async () => {
      if (!state.bundleDraft) throw new Error("预览已失效，请重新选择完整学习包。");
      const detail = await api("/api/bundles/" + encodeURIComponent(state.bundleDraft.draft_id) + "/commit","POST",{confirmed:true});
      state.bundleDraft = null; state.bundleFiles = []; $("#bundle-file").value = ""; node.hidden = true; await importResult(detail); toast("完整学习包已导入。先看场景与调用顺序，再进入讲义。");
    },commit,"正在写入…"),"button primary"); commit.id = "bundle-commit";
    append(row,commit,button("取消预览",() => { state.bundleDraft = null; state.bundleFiles = []; node.hidden = true; $("#bundle-file").value = ""; }));
    append(node,element("p","small-note","请展开核对知识讲义、题目、固定答案、提示和来源。结构校验无法替你确认教学正确；点击确认后才会写入应用。"),row);
  }
  async function previewPack(file) {
    state.packDraft = null; $("#pack-preview").hidden = true;
    const pack = await readJSON(file,state.limits.max_pack_bytes); if (!pack) return;
    const data = await api(projectURL("/packs/preview"),"POST",{pack}); state.packDraft = data;
    const node = $("#pack-preview"); node.replaceChildren(); node.hidden = false;
    append(node,element("h3","",data.title || "任务包预览"),paragraph("已通过结构校验 · " + (data.flow_count ?? data.flows?.length ?? 0) + " 个功能场景 · " + (data.concept_count ?? data.concepts?.length ?? 0) + " 个知识点 · " + (data.activity_count ?? data.activities?.length ?? 0) + " 项任务"));
    previewWarnings(node,data.warnings); if (data.flows?.length) previewFlows(node,data.flows,undefined,data.concepts); previewConcepts(node,data.concepts); previewActivities(node,data.activities);
    const row = element("div","button-row");
    const commit = button("确认写入任务包",() => action(async () => {
      if (!state.packDraft) throw new Error("预览已失效，请重新选择任务包。");
      await api(projectURL("/packs/" + encodeURIComponent(state.packDraft.draft_id) + "/commit"),"POST",{confirmed:true});
      state.packDraft = null; $("#pack-file").value = ""; node.hidden = true; await refreshProject(); toast("任务包已写入，原有作答记录已保留。");
    },commit,"正在写入…"),"button primary");
    commit.id = "pack-commit";
    append(row,commit,button("取消预览",() => { state.packDraft = null; node.hidden = true; $("#pack-file").value = ""; }));
    append(node,element("p","small-note","结构校验不等于教学正确。请核对任务与源码关联、固定答案、提示和验收要求。"),row);
  }
  async function previewRestore(file) {
    const data = await readJSON(file,state.limits.max_backup_bytes); if (!data) return;
    state.restore = data; const node = $("#restore-preview"); node.replaceChildren(); node.hidden = false;
    const projects = Array.isArray(data.projects) ? data.projects : data.data?.projects;
    const countText = Array.isArray(projects) ? "文件含 " + projects.length + " 个项目。" : "文件将在恢复时进行完整校验。";
    append(node,element("h3","","恢复会替换当前应用数据"),paragraph(countText + "当前项目快照、作答、提示与证据将被备份内容替换；原项目文件不受影响。请先导出当前备份。"));
    const row = element("div","button-row");
    const restore = button("确认恢复并替换",() => action(async () => {
      const agreed = await confirmAction("恢复整份备份","这会替换拾阶当前保存的全部项目快照与学习记录。\n原项目源码不会被修改。确认已保存需要保留的当前备份后再继续。","恢复并替换");
      if (!agreed) return;
      await api("/api/restore","POST",{confirmed:true,backup:state.restore}); state.restore = null; clearWorkspace(); node.hidden = true; $("#restore-file").value = ""; closeDialog("manage-dialog"); await bootstrap(); toast("备份已恢复。请核对项目与原始首答记录。");
    },restore,"正在恢复…"),"button primary");
    restore.id = "restore-confirm";
    append(row,restore,button("取消",() => { state.restore = null; node.hidden = true; $("#restore-file").value = ""; })); node.append(row);
  }
  function clearWorkspace() {
    state.project = null; state.flows = []; state.flowId = ""; state.flowStepId = ""; state.traceCaseId = ""; state.traceIndex = 0; state.flowReturn = null; state.concepts = []; state.activities = []; state.materials = []; state.activityDetail = null; state.activityId = ""; state.conceptId = ""; state.packDraft = null; state.drafts.clear(); state.requestId++; clearCheck();
    $("#pack-preview").hidden = true;
  }
  async function bootstrap() {
    const data = await api("/api/bootstrap"); state.token = data.token; state.projects = data.projects || []; state.limits = data.limits || {};
    $("#footer-local").textContent = "拾阶 "+data.version+(offlineMode ? " · 手机离线学习" : " · 本机学习");
    $("#import-limits").textContent = "快照最多 " + (state.limits.max_files || 180) + " 个文件，单个文本最多 " + Math.round((state.limits.max_file_bytes || 262144) / 1024) + " KB，总计 " + Math.round((state.limits.max_total_bytes || 8388608) / 1048576) + " MB。个人路径只用于扫描，不在分享文件中展示。";
    renderProjectPicker(); updatePrompt(); await loadCoachPrompts();
    if (state.projects.length) { let last; try { last = localStorage.getItem("shijie-last-project"); } catch {} const id = state.projects.some(p => p.id === last) ? last : state.projects[0].id; await loadProject(id); }
    else { state.project = null; $("#welcome").hidden = false; $("#workspace").hidden = true; statusText(offlineMode ? "离线学习已就绪 · 在使用说明中导入学习包或恢复备份。" : "本机学习服务已就绪 · 可以先体验原创案例，或让 Agent 准备你的项目。"); }
  }

  function renderOverview() {
    const node = $("#project-overview"); node.replaceChildren(); if (!state.project) return;
    const summary = state.projectSummary;
    const purpose = element("section","summary-purpose"); purpose.dataset.summaryField = "purpose";
    append(purpose,element("p","eyebrow","你正在理解的项目"),element("h2","",state.project.name),paragraph(summary.purpose || "当前资料未提供完整项目总结。请让 Agent 根据项目说明补齐，应用不会虚构项目作用。")); node.append(purpose);
    const context = element("div","summary-context");
    [["audience","谁使用它",summary.audience],["scenario","一次典型使用",summary.scenario]].forEach(([key,title,value]) => { const section = element("section","summary-section"); section.dataset.summaryField = key; append(section,element("h3","",title),paragraph(value || "尚未整理；需要项目事实支持。")); context.append(section); }); node.append(context);
    const capabilities = element("section","summary-section"); append(capabilities,element("h3","","它能够完成什么")); const list = element("ul","summary-capabilities");
    (summary.capabilities?.length ? summary.capabilities : state.flows.map(f => f.title + "：" + f.summary)).forEach(text => list.append(element("li","",text))); capabilities.append(list); node.append(capabilities);
    [["learning_value","理解它，能练到什么",summary.learning_value],["limitations","学习意义与现有边界",summary.limitations]].forEach(([key,title,value]) => { const section = element("section","summary-section"); section.dataset.summaryField = key; append(section,element("h3","",title),paragraph(value || "这份总结尚未补齐。已有代码不能直接证明使用者掌握，也不能替代真实运行验收。")); node.append(section); });
    if (summary.origin) node.append(paragraph(summary.origin === "readme_fallback" ? "这份摘要来自现有说明的保守摘取，尚待 Agent 完整整理。" : "内容由学习包整理，依据当前导入资料。","small-note"));
    if (summary.evidence?.length) node.append(evidenceDetails(summary.evidence));
    const next = element("section","summary-next"); append(next,element("h3","","下一步，选一个功能走进去"));
    state.flows.forEach((flow,index) => { const item = button("",() => { selectFlowState(flow.id); renderFlows(); showView("flow",false); $("#flow-view").scrollIntoView({block:"start"}); },"feature-entry"); item.dataset.flowId = flow.id; append(item,element("span","feature-number",String(index+1).padStart(2,"0")),element("span","",flow.title),element("span","","→")); next.append(item); });
    if (!state.flows.length) next.append(paragraph("功能链还未整理，请通过使用说明让 Agent 补全。")); node.append(next);
  }
  function explicitSkippedSteps() {
    return new Set(currentTraceCase()?.skipped_step_ids || []);
  }
  function reviewKey() { return state.project ? state.project.id+":"+state.project.version : ""; }
  function keepReviewDraft() {
    if (!state.project) return;
    const answers = Object.fromEntries([...$("#review-form").querySelectorAll("textarea[name]")].map(input => [input.name,input.value])); state.reviewDrafts.set(reviewKey(),{answers,assistance:$("#review-assistance").value});
  }
  function renderReview() {
    const review = state.reviewState, form = $("#review-form"); if (!state.project) return;
    const draft = state.reviewDrafts.get(reviewKey()); form.querySelectorAll("textarea[name]").forEach(input => { input.value = draft?.answers[input.name] || ""; }); $("#review-assistance").value = draft?.assistance || "none";
    $("#review-interval").value = state.reviewIntervalDrafts?.get(reviewKey()) ?? review?.interval_days ?? 3;
    $("#review-status").textContent = review ? (review.due ? "现在可以做一次回顾。" : "下次回顾："+stamp(review.due_at)+"。")+" 当前间隔 "+review.interval_days+" 天。" : "回顾记录保存在本机，默认每隔 3 天。";
    const history = $("#review-history"); history.replaceChildren();
    if (!review?.history?.length) { history.append(paragraph("还没有回顾。先写下这一次理解，下次再比较变化。","empty-note")); return; }
    [...review.history].reverse().forEach(record => { const details = element("details","review-record"); details.dataset.reviewId = record.id; append(details,element("summary","",stamp(record.created_at)+" · "+(helps[record.assistance] || record.assistance)+" · 版本 "+record.version+(record.stale ? "（历史版本）" : "")));
      [["purpose","项目作用"],["chain","功能调用链"],["boundary","故障与边界"],["next_step","疑问与下一次检验"]].forEach(([key,title]) => append(details,element("h3","",title),paragraph(record.answers[key],"saved-reflection"))); history.append(details); });
  }
  async function loadReview() {
    const pid = state.project?.id, version = state.project?.version, requestId = ++state.reviewRequestId; if (!pid) return;
    const data = await api(projectURL("/reviews")); if (pid !== state.project?.id || version !== state.project?.version || requestId !== state.reviewRequestId) return;
    state.reviewState = data; renderReview();
  }
  async function saveReview() {
    keepReviewDraft(); const key = reviewKey(), draft = state.reviewDrafts.get(key), pid = state.project.id;
    const data = await api(projectURL("/reviews"),"POST",draft); if (pid !== state.project?.id || key !== reviewKey()) return;
    state.reviewDrafts.delete(key); state.reviewState = data.review_state || data; renderReview(); toast("这次理解已保存。可在历史中比较自己前后的解释。");
  }
  async function saveReviewSettings() {
    const interval = Number($("#review-interval").value); if (!Number.isInteger(interval) || interval < 1 || interval > 30) throw new Error("回顾间隔应为 1 到 30 天的整数。");
    const pid = state.project.id, key = reviewKey(); await api(projectURL("/reviews/settings"),"POST",{interval_days:interval}); if (pid !== state.project?.id || key !== reviewKey()) return;
    if (Number(state.reviewIntervalDrafts?.get(key)) === interval) state.reviewIntervalDrafts.delete(key);
    await loadReview(); toast("回顾间隔已保存。");
  }
  async function answerCheck() {
    const activity = state.checkQuestions[state.checkIndex], checkId = state.check?.id, pid = state.project.id; if (!activity || !checkId) return;
    const data = await api(projectURL("/checks/"+encodeURIComponent(checkId)+"/answer"),"POST",{activity_id:activity.id,answer:state.checkAnswers.get(activity.id) || [],guessed:$("#check-guessed").checked});
    if (pid !== state.project?.id || checkId !== state.check?.id) return;
    state.check = data.check; state.checkFeedback.set(activity.id,data.result); state.checkResults = data.check.results || []; renderCheckQuestion(); await loadCheckHistory();
  }
  function renderSingleFeedback(parent,activity,result) {
    const section = element("section","single-feedback "+(result.correct ? "correct" : "incorrect")); section.id = "check-feedback"; section.setAttribute("aria-live","polite");
    append(section,element("h3","",result.correct ? "这次回答符合" : "这题需要回看"),paragraph("固定答案："+quizAnswerText(activity,result.answer_ids)),formattedPrompt(result.explanation));
    if (result.guessed) section.append(paragraph("已标记为猜测。即使选对，也会进入到期复习。","small-note"));
    const concept = state.concepts.find(c => c.id === activity.concept_id); if (concept) section.append(button("回看知识："+concept.title,() => { state.flowReturn = null; state.knowledgeReturn = "check"; state.conceptId = concept.id; state.group = groupOf(concept); renderConcepts(); renderConceptDetail(); showView("knowledge",false); },"button quiet")); parent.append(section);
  }

  function bind() {
    state.reviewIntervalDrafts = new Map();
    $("#review-interval").addEventListener("input",() => state.reviewIntervalDrafts.set(reviewKey(),$("#review-interval").value));
    $("#knowledge-return").addEventListener("click",() => showView("check",false));
    const handoffActions = element("div","button-row handoff-shortcuts");
    const firstRun = button("首次上手提示词",() => openManagement("onboarding-section")); firstRun.id = "handoff-onboarding";
    const bundleEntry = button("导入完整学习包",() => openManagement("bundle-section")); bundleEntry.id = "handoff-bundle";
    const materialsEntry = button("项目学习资料",() => openManagement("materials-section")); materialsEntry.id = "handoff-materials";
    append(handoffActions,firstRun,bundleEntry,materialsEntry); $("#handoff-view .view-intro").after(handoffActions);
    const learningDownload = button("下载出题提示词",() => downloadText("shijie-project-coach.txt",$("#agent-prompt").textContent)); learningDownload.id = "prompt-download"; $("#prompt-copy").after(learningDownload);
    const updateRow = element("label","checkbox-label"); updateRow.id = "update-current-row"; updateRow.hidden = true;
    const updateCheck = element("input"); updateCheck.type = "checkbox"; updateCheck.id = "update-current";
    append(updateRow,updateCheck,element("span","","更新当前项目快照：保留作答记录，源码改变后将旧证据标为需要重新核对。"));
    $("#scan-form").append(updateRow);
    document.querySelectorAll("[data-view]").forEach(node => node.addEventListener("click",() => showView(node.dataset.view)));
    $("#flow-back").addEventListener("click",returnToFlow);
    document.querySelectorAll("[data-close]").forEach(node => node.addEventListener("click",() => closeDialog(node.dataset.close)));
    $("#manage-open").addEventListener("click",() => openManagement()); $("#welcome-import").addEventListener("click",() => openManagement("scan-section"));
    $("#welcome-agent").addEventListener("click",() => openManagement("onboarding-section")); $("#welcome-bundle").addEventListener("click",() => openManagement("bundle-section"));
    $("#allow-research").addEventListener("change",updatePrompt);
    $("#onboarding-copy").addEventListener("click",() => action(() => copyText($("#onboarding-prompt").textContent)));
    $("#onboarding-download").addEventListener("click",() => downloadText("shijie-first-run.txt",$("#onboarding-prompt").textContent));
    $("#bundle-file").addEventListener("change",event => action(() => previewBundle(event.target.files[0])));
    $("#materials-file").addEventListener("change",event => action(() => uploadMaterials(event.target.files)));
    $("#review-form").addEventListener("input",keepReviewDraft);
    $("#review-assistance").addEventListener("change",keepReviewDraft);
    $("#review-form").addEventListener("submit",event => { event.preventDefault(); action(saveReview,$("#review-save"),"正在保存…"); });
    $("#review-settings").addEventListener("submit",event => { event.preventDefault(); action(saveReviewSettings,event.submitter,"正在保存…"); });
    $("#check-settings").addEventListener("submit",event => { event.preventDefault(); action(startCheck,$("#check-start"),"正在抽题…"); });
    $("#demo-start").addEventListener("click",event => offlineMode ? openManagement("bundle-section") : demo(event.currentTarget)); $("#manage-demo").addEventListener("click",event => demo(event.currentTarget));
    $("#project-select").addEventListener("change",event => { const id = event.target.value; if (id) action(() => loadProject(id)); });
    $("#scan-form").addEventListener("submit",event => { event.preventDefault(); action(async () => { const path = $("#project-path").value.trim(); if (!path) throw new Error("请粘贴一个项目文件夹路径。"); const scan = {path}; if ($("#update-current").checked && state.project) scan.project_id = state.project.id; const data = await api("/api/projects/scan","POST",scan); await importResult(data); },$("#scan-submit"),"正在扫描…"); });
    $("#snapshot-upload").addEventListener("change",event => action(() => folderUpload(event.target.files)));
    ["#practice-concept-filter","#practice-kind-filter"].forEach(selector => $(selector).addEventListener("change",() => { renderActivityList(); action(selectFirstActivity); }));
    $("#practice-due").addEventListener("click",() => { state.dueOnly = !state.dueOnly; renderActivityList(); action(selectFirstActivity); });
    $("#handoff-download").addEventListener("click",event => action(async () => {
      const data = await api(projectURL("/handoff"));
      const approved = await confirmAction("导出 Agent 接手文件","文件包含已导入源码与作答记录。请确认这些资料可以提供给你选择的 Agent；应用不会代你上传。","导出到本机");
      if (!approved) return; download("shijie-handoff.json",data); if (!window.ShijieNative) toast("接手文件已导出，请自行选择可信的 Agent 使用。");
    },event.currentTarget,"正在准备…"));
    $("#handoff-preview").addEventListener("click",event => action(async () => { const data = await api(projectURL("/handoff")); openPlain("Agent 接手内容",JSON.stringify(data,null,2),"只在本机预览。这些内容将在你导出并交给 Agent 后被对方读取。"); },event.currentTarget));
    $("#prompt-copy").addEventListener("click",() => action(() => copyText($("#agent-prompt").textContent)));
    $("#pack-file").addEventListener("change",event => action(() => previewPack(event.target.files[0])));
    $("#backup-download").addEventListener("click",event => action(async () => { const data = await api("/api/backup"); download("shijie-backup-" + new Date().toISOString().slice(0,10) + ".json",data); if (!window.ShijieNative) toast("完整备份已导出，请保存在自己的安全位置。"); },event.currentTarget,"正在准备…"));
    $("#restore-file").addEventListener("change",event => action(() => previewRestore(event.target.files[0])));
    $("#delete-project").addEventListener("click",event => action(async () => {
      const approved = await confirmAction("移除项目快照","将移除「" + state.project.name + "」在拾阶中的快照与相关练习记录。\n原项目源码不会被删除。此操作只能通过你保存的备份恢复。","移除快照");
      if (!approved) return; await api(projectURL("?confirmed=true"),"DELETE"); clearWorkspace(); closeDialog("manage-dialog"); await bootstrap(); toast("快照已移除，原项目未被修改。");
    },event.currentTarget,"正在移除…"));
    $("#confirm-ok").addEventListener("click",() => settleConfirm(true)); $("#confirm-cancel").addEventListener("click",() => settleConfirm(false));
    $("#confirm-dialog").addEventListener("close",() => { if (confirmResolve) { const resolve = confirmResolve; confirmResolve = null; resolve(false); } });
  }
  $("#service-reconnect").addEventListener("click",event => action(async () => { if (offlineMode) offlineReady = null; await bootstrap(); $("#service-reconnect").hidden = true; toast("已重新读取。已保存的作答和理解记录仍在。"); },event.currentTarget,"正在读取…"));
  function prepareOfflineInterface() {
    document.body.classList.add("offline-app");
    $("#status-text").textContent = "正在读取手机内的学习资料…";
    $("#service-reconnect").textContent = "重新读取";
    const setup = $("#onboarding-section");
    setup.querySelector("h3").textContent = "离线使用，让 Agent 帮你准备资料";
    setup.querySelector("p").textContent = "安装后可以直接学习，无需电脑或登录。记录只保存在这台手机；卸载前请导出完整备份。要学习自己的项目，可在电脑上让 Agent 整理学习包 JSON，然后传到手机预览导入。";
    setup.querySelector("p.muted").textContent = "复制以下提示词，交给你的 Agent；再提供你选择的项目和学习资料。资料不足时，可在下方明确授权它查找官方文档。应用本身不连接模型。";
    const legal = element("details","dialog-section");
    legal.append(element("summary","","版本与许可"),paragraph("拾阶 0.4.1 · Android 离线版。原创代码使用 Apache-2.0；你导入的项目、资料和学习记录的权利仍归相应权利人。","muted"));
    legal.append(button("查看许可与版权说明",() => action(async () => {
      const texts = await Promise.all(["LICENSE","NOTICE"].map(async name => {
        const response = await fetch("/legal/"+name); if (!response.ok) throw new Error("许可说明未能读取，请检查安装包。");
        return await response.text();
      }));
      openPlain("拾阶许可与版权",texts.join("\n\n"),"只读授权说明；不包含个人资料。");
    })));
    $("#manage-dialog .dialog-scroll").append(legal);
    $("#bundle-section").removeAttribute("data-agent-only");
    const section = element("details","dialog-section");
    section.append(element("summary","","补充题目与交给 Agent"));
    $("#handoff-view .handoff-grid").querySelectorAll(".workflow-section").forEach(node => section.append(node));
    $("#bundle-section").after(section);
    $("#demo-start").textContent = "导入我的学习包";
    window.ShijieOffline.handleBack = () => {
      if (state.view === "knowledge" && state.flowReturn) { returnToFlow(); return true; }
      if (state.view !== "overview") { showView("overview",false); return true; }
      return false;
    };
  }
  bind(); if (offlineMode) prepareOfflineInterface(); action(bootstrap);
})();
