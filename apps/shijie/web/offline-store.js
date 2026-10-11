/* 拾阶离线协议：声明式 JSON、版本绑定、先持久化后公布结果。 */
(function (root, factory) {
  "use strict";
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  else root.ShijieOffline = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";
  const TABLES = ["projects", "files", "concepts", "activities", "attempts", "hints", "evidence", "checks", "flows", "project_summaries", "reviews", "review_settings"];
  const KINDS = ["predict", "trace", "debug", "reproduce", "transfer"];
  const ASSISTANCE = ["none", "reference", "agent"];
  const LIMITS = {max_files:640,max_file_bytes:262144,max_total_bytes:8388608,max_backup_bytes:33554432,max_pack_bytes:2097152,max_bundle_bytes:33554432};
  const SUMMARY_FIELDS = ["purpose","audience","scenario","capabilities","learning_value","limitations","evidence"];
  const CONCEPT_FIELDS = ["id","title","group","explanation","why","prerequisites","evidence","detection","limitations","mechanism","failures","lesson","references"];
  const FLOW_CONTRACT = {
    fields:["id","title","summary","scenario","input","expected","steps","branches","trace_cases","resources"],
    step_fields:["id","title","actor","action","input","output","why","concept_ids","evidence"],optional_step_fields:["methods"],
    method_fields:["id","signature","input","output","relation","why","evidence"],method_relations:["call","return","stop"],optional_frame_fields:["method_id"],
    branch_fields:["from_step_id","condition","outcome","skipped_step_ids"],trace_case_fields:["id","title","input","result","frames"],optional_trace_case_fields:["skipped_step_ids"],
    frame_fields:["step_id","explanation","values"],value_fields:["name","type","value","source"],resource_fields:["name","ownership","lifecycle","failure"],
    limits:{flows:12,steps_min:2,steps_max:20,step_concepts:80,branches:20,skipped_steps:20,trace_cases:12,frames:120,values_per_frame:24,resources:20},
    rules:["必填字段完整，只允许协议声明的可选字段；所有叙述字段和变量 value 均为字符串。","每步说明 actor/action/input/output/why，并引用当前知识点与文件证据。","场景和参数追踪是静态教学推演，不证明实际执行、生产能力或独立掌握。","方法签名与调用、返回、停止关系需要真实源码依据；禁止执行导入表达式。"]
  };
  const REVIEW_PROMPTS = [
    {id:"purpose",title:"这个项目解决什么问题？",prompt:"用自己的话说出使用者、具体场景、输入和最终结果。"},
    {id:"chain",title:"选一个功能，讲完调用顺序",prompt:"从入口开始，按实际方法说明谁调用谁、每步接收什么、返回什么。"},
    {id:"boundary",title:"追踪一个正常或非法输入",prompt:"写出参数的类型和值如何变化、在哪一步拒绝，以及哪些后续调用不会发生。"},
    {id:"next_step",title:"哪些地方还不能独立解释？",prompt:"写出不确定的知识点、需要核对的证据和下一次准备复现的一个小行为。"}
  ];
  const REVIEW_CLAIM = "本人回顾原文；软件未验证解释正确性、实际运行或独立掌握。";
  const clone = value => JSON.parse(JSON.stringify(value));
  class ProtocolError extends Error { constructor(message, status=400) { super(message); this.name="ProtocolError"; this.status=status; } }
  function fail(message, status=400) { throw new ProtocolError(message,status); }
  function assert(condition,message) { if (!condition) fail(message); }
  function object(value) { return value !== null && typeof value === "object" && !Array.isArray(value); }
  function fields(value, allowed, required=allowed) { assert(object(value) && Object.keys(value).every(k=>allowed.includes(k)) && required.every(k=>Object.hasOwn(value,k)),"字段不符合离线数据协议。"); }
  function identity(value) { assert(typeof value==="string" && /^[a-zA-Z0-9_-]{1,100}$/.test(value),"标识符格式不正确。"); return value; }
  function text(value,limit=12000,empty=false) { assert(typeof value==="string" && value.length<=limit && !value.includes("\0") && (empty || value.trim()),"文本内容为空或超过允许长度。"); return value.trim(); }
  function timestamp(value) { assert(typeof value==="string" && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value)),"记录时间格式不正确。"); return value; }
  function digest(value) { assert(typeof value==="string" && /^[0-9a-f]{64}$/.test(value),"源码哈希无效。"); return value; }
  function integer(value,min,max,message="数量或范围不正确。") { assert(Number.isInteger(value) && value>=min && value<=max,message); return value; }
  function list(value,min,max,label="列表") { assert(Array.isArray(value) && value.length>=min && value.length<=max,label+"数量不正确。"); return value; }
  function same(a,b) { return canonical(a)===canonical(b); }
  function canonical(value) { if(Array.isArray(value)) return "["+value.map(canonical).join(",")+"]"; if(object(value)) return "{"+Object.keys(value).sort().map(k=>JSON.stringify(k)+":"+canonical(value[k])).join(",")+"}"; return JSON.stringify(value); }
  function setEqual(a,b) { return a.length===b.length && new Set(a).size===a.length && a.every(x=>b.includes(x)); }
  function relativePath(value) { assert(typeof value==="string" && value.length>0 && value.length<=300 && !/[\\:\0]/.test(value) && !value.startsWith("/") && value.split("/").every(p=>p && p!=="." && p!==".."),"文件路径必须是安全的相对路径。"); return value; }
  function safeJSON(value,limit) {
    let count=0;
    function visit(v,depth) {
      assert(depth<=80 && ++count<=1000000,"数据结构过深或过大。");
      if(typeof v==="number") assert(Number.isFinite(v),"JSON 数值无效。");
      if(typeof v==="string") assert(!/[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(v),"文本包含无效 Unicode。");
      if(v && typeof v==="object") for(const k of Object.keys(v)) { assert(!["__proto__","prototype","constructor"].includes(k),"数据包含不安全字段。"); visit(v[k],depth+1); }
      else assert(v===null || ["string","number","boolean"].includes(typeof v),"只支持声明式 JSON 数据。");
    }
    visit(value,0);
    assert(new TextEncoder().encode(JSON.stringify(value)).length<=limit,"数据超过安全容量上限。");
  }
  function cryptoAPI() { if (globalThis.crypto?.subtle) return globalThis.crypto; if(typeof require==="function") return require("node:crypto").webcrypto; fail("当前设备不支持安全哈希，请更新 Android System WebView。",503); }
  async function sha256(value) { const data=await cryptoAPI().subtle.digest("SHA-256",new TextEncoder().encode(value)); return Array.from(new Uint8Array(data),n=>n.toString(16).padStart(2,"0")).join(""); }
  async function sourceHash(files) { return sha256(files.map(f=>f.path+":"+f.sha256).join("\n")); }
  function uid() { const bytes=new Uint8Array(16); cryptoAPI().getRandomValues(bytes); return Array.from(bytes,n=>n.toString(16).padStart(2,"0")).join(""); }
  function eligible(path,content) {
    const parts=path.split("/"),name=parts.at(-1),skip=[".git",".idea",".gradle",".mvn","target","build","dist","node_modules","data","__pycache__",".venv","venv","vendor","bin","obj","coverage","secrets","private","credentials","keys"];
    if(parts.slice(0,-1).some(p=>p.startsWith(".") || skip.includes(p.toLowerCase()))) return false;
    if(/(?:^|[._-])(?:secrets?|credentials?|private|token|key)(?:[._-]|$)|^\.env|^application.*\.(?:yml|yaml|properties)$/i.test(name)) return false;
    if(!/\.(?:java|py|jsx?|mjs|cjs|tsx?|go|rs|c|h|cc|cpp|cxx|hpp|cs|sql|html?|css|scss|md|txt)$/i.test(name) && !["pom.xml","build.gradle","build.gradle.kts","settings.gradle","settings.gradle.kts"].includes(name)) return false;
    if(/-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|\bsk-[A-Za-z0-9_-]{16,}|\bgh[pousr]_[A-Za-z0-9]{20,}/.test(content)) return false;
    return !/(?:password|secret|api[_-]?key|access[_-]?token)["']?\s*[:=]\s*["']?(?!\$\{|example\b|dummy\b|changeme\b|test\b|fake\b)[A-Za-z0-9_./+-]{6,}/i.test(content);
  }
  async function collectFiles(items,strict=false) {
    list(items,1,2560,"源码快照"); const seen=new Set(),result=[]; let total=0,skipped=0;
    for(const raw of items) {
      fields(raw,strict?["path","text","sha256"]:["path","text"],strict?["path","text","sha256"]:["path","text"]);
      const path=relativePath(raw.path); assert(!seen.has(path),"文件相对路径不能重复。"); seen.add(path);
      assert(typeof raw.text==="string" && !raw.text.includes("\0"),"只支持 UTF-8 文本源码。");
      const size=new TextEncoder().encode(raw.text).length;
      if(!eligible(path,raw.text) || size>LIMITS.max_file_bytes) { assert(!strict,"备份包含敏感配置、密钥或不支持的文件。"); skipped++; continue; }
      assert(result.length<LIMITS.max_files && total+size<=LIMITS.max_total_bytes,"项目超过快照容量上限。"); total+=size;
      const hash=await sha256(raw.text); if(strict) assert(hash===raw.sha256,"备份源码哈希不匹配。");
      result.push({path,text:raw.text,sha256:hash});
    }
    assert(result.length,"没有可导入的源码；配置、密钥和构建产物已排除。");
    result.sort((a,b)=>a.path<b.path?-1:a.path>b.path?1:0);
    return {files:result,report:{included:result.length,skipped,bytes:total}};
  }
  function anchors(value,files,current=true) {
    return list(value,1,8,"源码证据").map(a=>{
      fields(a,["path","line","quote","sha256"]); relativePath(a.path); integer(a.line,1,1000000); text(a.quote,800); digest(a.sha256);
      if(current) { const f=files.find(f=>f.path===a.path),line=f?.text.split(/\r\n|\n|\r/)[a.line-1]; assert(f && line!==undefined && line.includes(a.quote) && a.sha256===f.sha256,"证据或文件哈希不匹配。"); }
      return clone(a);
    });
  }
  function references(refs) {
    return list(refs,0,12,"参考出处").map(ref=>{
      fields(ref,["title","url","note"]); text(ref.title,200); text(ref.note,3000,true); text(ref.url,2000);
      let parsed; try {parsed=new URL(ref.url);} catch { fail("参考出处只支持安全的 http 或 https 绝对地址。");}
      assert(["http:","https:"].includes(parsed.protocol) && parsed.hostname && !parsed.username && !parsed.password && !/[\\\s\x00-\x1f]/.test(ref.url),"参考出处只支持安全的 http 或 https 绝对地址。"); return clone(ref);
    });
  }
  function concepts(raw,files,existing=[],legacy=false) {
    list(raw,0,80,"知识点"); assert(raw.length+existing.length<=80,"一个项目最多保存 80 个知识点。");
    const ids=new Set(existing.map(c=>c.id)),result=[];
    for(const c of raw) {
      fields(c,CONCEPT_FIELDS,["id","title","group","explanation","why","prerequisites","evidence"]); identity(c.id); assert(!ids.has(c.id),"知识点标识已存在或重复。"); ids.add(c.id);
      text(c.title,200); text(c.group,100); text(c.explanation); text(c.why); if(c.detection!==undefined) text(c.detection,100); if(c.limitations!==undefined) text(c.limitations,5000);
      list(c.prerequisites,0,80).forEach(identity); assert(new Set(c.prerequisites).size===c.prerequisites.length,"前置知识点重复。"); anchors(c.evidence,files);
      if(c.mechanism!==undefined) text(c.mechanism,16000,true); if(c.lesson!==undefined) text(c.lesson,48000,true);
      if(c.failures!==undefined) list(c.failures,0,20).forEach(v=>text(v,5000)); if(c.references!==undefined) references(c.references);
      result.push(clone(c));
    }
    const all=existing.concat(result),graph=new Map(all.map(c=>[c.id,c.prerequisites||[]])); assert(new Set(all.map(c=>c.group)).size<=8,"一个项目最多包含 8 个知识分类。");
    const active=new Set(),done=new Set();
    function visit(id) { assert(!active.has(id),"前置知识点形成循环。"); if(done.has(id))return; active.add(id); for(const p of graph.get(id)||[]) { if(!ids.has(p)) { assert(legacy && all.find(c=>c.id===id)?.detection==="static_candidate","前置知识点不存在。"); continue; } visit(p); } active.delete(id); done.add(id); }
    all.forEach(c=>visit(c.id)); return result;
  }
  function options(raw) {
    list(raw,0,8,"选项"); const seen=new Set(); return raw.map(o=>{fields(o,["id","text"]); identity(o.id); text(o.text,3000); assert(!seen.has(o.id),"选项标识不能重复。"); seen.add(o.id); return clone(o);});
  }
  function activities(raw,cs,files,max=80) {
    list(raw,0,max,"活动"); const ids=new Set(),cids=new Set(cs.map(c=>c.id));
    return raw.map(a=>{
      fields(a,["id","concept_id","kind","title","prompt","options","answer_ids","rubric","explanation","hints","evidence","due_after_hours","option_mode","multiple","origin"],["id","concept_id","kind","title","prompt","rubric","hints","evidence"]);
      identity(a.id); assert(!ids.has(a.id) && cids.has(a.concept_id) && KINDS.includes(a.kind),"任务标识、知识点或活动类型无效。"); ids.add(a.id);
      const opts=options(a.options||[]),ans=list(a.answer_ids||[],0,8); assert(new Set(ans).size===ans.length && ans.every(v=>opts.some(o=>o.id===v)),"标准答案必须引用有效选项。"); assert(Boolean(opts.length)===Boolean(ans.length) && (!opts.length || opts.length>=2),"选择题需要答案与至少两个选项。");
      text(a.title,200); text(a.prompt,16000); list(a.rubric,1,12).forEach(v=>text(v,3000)); list(a.hints,1,3).forEach(v=>text(v,5000)); text(a.explanation||"",12000,true); anchors(a.evidence,files);
      const delay=a.due_after_hours??(a.kind==="transfer"?48:0); assert(typeof delay==="number" && Number.isFinite(delay) && delay>=0 && delay<=168,"延迟复习时间无效。");
      return {...clone(a),options:opts,answer_ids:clone(ans),explanation:a.explanation||"",due_after_hours:delay,option_mode:ans.length>1?"multiple":opts.length?"single":"text"};
    });
  }
  function summary(raw,files,current=true) {
    if(raw===undefined || raw===null)return null;
    fields(raw,SUMMARY_FIELDS); SUMMARY_FIELDS.filter(k=>!["evidence","capabilities"].includes(k)).forEach(k=>text(raw[k],8000)); list(raw.capabilities,1,20).forEach(v=>text(v,1000)); anchors(raw.evidence,files,current); return clone(raw);
  }
  function refs(raw,valid,min,max,label) { list(raw,min,max,label).forEach(identity); assert(new Set(raw).size===raw.length && raw.every(v=>valid.has(v)),label+"引用无效或重复标识。"); return raw; }
  function flows(raw,cs,files,existing=[]) {
    list(raw,0,12,"调用链"); assert(raw.length+existing.length<=12,"一个项目最多保存 12 条调用链。"); const fids=new Set(existing.map(f=>f.id)),cids=new Set(cs.map(c=>c.id));
    for(const f of raw) {
      fields(f,["id","title","summary","scenario","input","expected","steps","branches","trace_cases","resources"]); identity(f.id); assert(!fids.has(f.id),"调用链标识已存在或重复。"); fids.add(f.id); text(f.title,200); text(f.summary,4000); ["scenario","input","expected"].forEach(k=>text(f[k],8000));
      const sids=new Set();
      list(f.steps,2,20,"调用链步骤").forEach(s=>{
        const sf=["id","title","actor","action","input","output","why","concept_ids","evidence"]; fields(s,[...sf,"methods"],sf); identity(s.id); assert(!sids.has(s.id),"步骤标识重复。"); sids.add(s.id); text(s.title,200); text(s.actor,200); ["action","input","output","why"].forEach(k=>text(s[k],5000)); refs(s.concept_ids,cids,1,80,"步骤知识点"); anchors(s.evidence,files);
        if(s.methods!==undefined) {const mids=new Set(); list(s.methods,1,30,"方法").forEach(m=>{fields(m,["id","signature","input","output","relation","why","evidence"]); identity(m.id); assert(!mids.has(m.id) && ["call","return","stop"].includes(m.relation),"方法标识或关系无效。"); mids.add(m.id); text(m.signature,1000); ["input","output","why"].forEach(k=>text(m[k],5000)); anchors(m.evidence,files);});}
      });
      list(f.branches,0,20,"分支").forEach(b=>{fields(b,["from_step_id","condition","outcome","skipped_step_ids"]); assert(sids.has(b.from_step_id),"分支起点无效。"); text(b.condition,5000); text(b.outcome,5000); refs(b.skipped_step_ids,sids,0,20,"跳过步骤");});
      const caseIds=new Set(); list(f.trace_cases,0,12,"追踪案例").forEach(c=>{
        const cf=["id","title","input","result","frames"]; fields(c,[...cf,"skipped_step_ids"],cf); identity(c.id); assert(!caseIds.has(c.id),"追踪案例重复。"); caseIds.add(c.id); text(c.title,200); text(c.input,8000); text(c.result,8000);
        list(c.frames,1,120,"追踪帧").forEach(frame=>{fields(frame,["step_id","explanation","values","method_id"],["step_id","explanation","values"]); assert(sids.has(frame.step_id),"追踪帧步骤不存在。"); text(frame.explanation,5000); if(frame.method_id!==undefined) assert(f.steps.find(s=>s.id===frame.step_id).methods?.some(m=>m.id===frame.method_id),"追踪帧方法不存在。"); list(frame.values,1,24,"追踪变量").forEach(v=>{fields(v,["name","type","value","source"]); text(v.name,200); text(v.type,200); text(v.value,4000,true); text(v.source,1000);});});
        if(c.skipped_step_ids!==undefined) {
          refs(c.skipped_step_ids,sids,0,20,"未调用步骤"); const traced=new Set(c.frames.map(fr=>fr.step_id)); assert(!c.skipped_step_ids.some(id=>traced.has(id)),"未调用步骤不能同时出现在追踪帧。");
          const allowed=new Set(f.branches.filter(b=>traced.has(b.from_step_id)).flatMap(b=>b.skipped_step_ids)); c.frames.forEach(fr=>{const index=f.steps.findIndex(s=>s.id===fr.step_id),method=f.steps[index].methods?.find(m=>m.id===fr.method_id); if(method?.relation==="stop") f.steps.slice(index+1).forEach(s=>allowed.add(s.id));}); assert(c.skipped_step_ids.every(id=>allowed.has(id)),"未调用步骤需要已追踪分支或明确停止方法。");
        }
      });
      list(f.resources,0,20,"资源").forEach(r=>{fields(r,["name","ownership","lifecycle","failure"]); text(r.name,200); ["ownership","lifecycle","failure"].forEach(k=>text(r[k],3000));});
    }
    return clone(raw);
  }
  function checkQuestion(q,active) {
    fields(q,["id","concept_id","title","prompt","kind","option_mode","options"]); identity(q.id); identity(q.concept_id); text(q.title,200); text(q.prompt,16000); assert(KINDS.includes(q.kind) && ["single","multiple"].includes(q.option_mode),"检测题目类型不正确。"); assert(options(q.options).length>=2,"检测选项不足。");
    if(active) assert(["concept_id","title","prompt","kind","option_mode"].every(k=>same(q[k],active[k])) && same(Object.fromEntries(q.options.map(o=>[o.id,o.text])),Object.fromEntries(active.options.map(o=>[o.id,o.text]))),"检测题目与当前活动不匹配。");
  }
  function validateHistorical(record,project) { integer(record.version,1,project.version); digest(record.source_hash); if(record.version===project.version) assert(record.source_hash===project.source_hash,"当前记录源码哈希不匹配。"); timestamp(record.created_at); }
  function snapshot(snap) {
    fields(snap,["id","concept_id","kind","title","prompt","options","evidence"]); identity(snap.id); identity(snap.concept_id); assert(KINDS.includes(snap.kind),"历史活动类型无效。"); text(snap.title,200); text(snap.prompt,16000); options(snap.options); anchors(snap.evidence,[],false);
  }
  async function validateBackup(raw) {
    safeJSON(raw,LIMITS.max_backup_bytes); assert(object(raw) && [1,2].includes(raw.schema_version) && raw.app==="project-study","备份格式不正确。");
    const d=raw.data; assert(object(d),"备份表结构不完整。"); const required=TABLES.slice(0,7).concat(raw.schema_version===2?["checks"]:[]); assert(required.every(t=>Object.hasOwn(d,t)) && Object.keys(d).every(t=>TABLES.includes(t)),"备份表结构不完整。");
    const data=Object.fromEntries(TABLES.map(t=>[t,clone(d[t]||[])])); TABLES.forEach(t=>list(data[t],0,t==="projects"?12:t==="attempts"?10000:30000,"备份记录"));
    const ps=new Map(); data.projects.forEach(p=>{fields(p,["id","name","version","source_hash","file_count","created_at","updated_at","scan_report"]); identity(p.id); text(p.name,80); assert(!/[\\/:]/.test(p.name),"项目名称不能包含本机路径。"); integer(p.version,1,2147483647); digest(p.source_hash); integer(p.file_count,1,640); timestamp(p.created_at); timestamp(p.updated_at); assert(object(p.scan_report) && !ps.has(p.id),"项目标识或扫描报告无效。"); ps.set(p.id,p);});
    for(const t of TABLES.slice(1)) { const keys=new Set(); for(const row of data[t]) {fields(row,["project_id","data"]); assert(ps.has(row.project_id) && object(row.data),"备份包含无效项目关联。"); const key=["files","concepts","activities","flows"].includes(t)?row.project_id+":"+(t==="files"?relativePath(row.data.path):identity(row.data.id)):identity(row.data.id); assert(!keys.has(key),"备份记录标识重复。"); keys.add(key);} }
    const rows=(t,pid)=>data[t].filter(r=>r.project_id===pid).map(r=>r.data),activityMap=new Map(),attemptMap=new Map();
    for(const p of data.projects) {
      const original=rows("files",p.id),collected=await collectFiles(original,true),fs=collected.files;
      assert(fs.length===p.file_count && await sourceHash(fs)===p.source_hash,"备份文件数量或源码哈希不匹配。");
      const cs=concepts(rows("concepts",p.id),fs,[],true); const originals=rows("activities",p.id),as=activities(originals,cs,fs,150); flows(rows("flows",p.id),cs,fs);
      originals.forEach((a,i)=>assert(Array.isArray(a.options) && Array.isArray(a.answer_ids) && Array.isArray(a.hints) && Array.isArray(a.rubric) && a.option_mode===as[i].option_mode && typeof a.explanation==="string" && typeof a.due_after_hours==="number","备份活动需包含完整选项、评分与提示字段。"));
      as.forEach(a=>activityMap.set(p.id+":"+a.id,a));
      rows("project_summaries",p.id).forEach(r=>{fields(r,["id","version","source_hash","created_at","content"]); validateHistorical(r,p); summary(r.content,fs,r.version===p.version);});
      rows("review_settings",p.id).forEach(r=>{fields(r,["id","interval_days","updated_at"]); assert(r.id===p.id,"回顾设置项目不匹配。"); integer(r.interval_days,1,30); timestamp(r.updated_at);}); assert(rows("review_settings",p.id).length<=1,"回顾设置重复。");
      rows("reviews",p.id).forEach(r=>{fields(r,["id","version","source_hash","created_at","answers","assistance","status","mastery_claim"]); validateHistorical(r,p); fields(r.answers,REVIEW_PROMPTS.map(p=>p.id)); Object.values(r.answers).forEach(v=>text(v)); assert(ASSISTANCE.includes(r.assistance) && r.status==="self_reported" && r.mastery_claim===REVIEW_CLAIM,"回顾不能冒充自动验收结论。");});
      for(const a of rows("attempts",p.id)) {
        validateHistorical(a,p); identity(a.activity_id); identity(a.concept_id); assert(KINDS.includes(a.kind) && ASSISTANCE.includes(a.assistance),"作答关联或辅助方式不正确。"); integer(a.hint_level,0,3); assert(a.guessed===undefined || typeof a.guessed==="boolean","猜测标记无效。"); snapshot(a.question_snapshot); assert(a.question_snapshot.id===a.activity_id && a.question_snapshot.concept_id===a.concept_id && a.question_snapshot.kind===a.kind,"历史题目与作答关联不正确。");
        fields(a.assessment,["option_ids","answer_ids"]); const opts=a.question_snapshot.options; assert(same(opts.map(o=>o.id),a.assessment.option_ids) && new Set(a.assessment.answer_ids).size===a.assessment.answer_ids.length && a.assessment.answer_ids.every(id=>a.assessment.option_ids.includes(id)),"历史评分依据不正确。");
        if(opts.length) { list(a.answer,1,8); assert(new Set(a.answer).size===a.answer.length && a.answer.every(id=>a.assessment.option_ids.includes(id)) && a.assessment.answer_ids.length,"历史作答选项不正确。"); const score=setEqual(a.answer,a.assessment.answer_ids)?100:0; assert(a.score===score && a.status===(score?"correct":"incorrect"),"作答分数不正确。"); }
        else {text(a.answer); assert(a.score===null && a.status==="review_required" && !a.assessment.answer_ids.length,"主观回答不能自动通过。");}
        const active=activityMap.get(p.id+":"+a.activity_id); if(a.version===p.version) assert(active && same(active.answer_ids,a.assessment.answer_ids) && same(active.options,opts) && same(active.evidence,a.question_snapshot.evidence),"当前作答引用的题目或证据不匹配。"); attemptMap.set(a.id,{pid:p.id,record:a});
      }
      for(const h of rows("hints",p.id)) {validateHistorical(h,p); integer(h.level,1,3); text(h.text,5000); snapshot(h.question_snapshot); assert(h.activity_id===h.question_snapshot.id,"提示活动关联无效。"); const active=activityMap.get(p.id+":"+h.activity_id); if(h.version===p.version) assert(active && active.hints[h.level-1]===h.text && same(active.evidence,h.question_snapshot.evidence),"提示与当前活动不匹配。");}
      for(const e of rows("evidence",p.id)) {const parent=attemptMap.get(e.attempt_id); assert(parent && parent.pid===p.id && e.activity_id===parent.record.activity_id && e.version===parent.record.version && e.source_hash===parent.record.source_hash && e.status==="review_required" && typeof e.independent==="boolean","验收证据关联不正确。"); timestamp(e.created_at); text(e.summary); const nums=["tests_run","passed","failures","errors","skipped"].map(k=>integer(e[k],0,100000)); assert(nums[0]===nums.slice(1).reduce((a,b)=>a+b,0),"验收数量不匹配。"); if(e.independent) assert(parent.record.assistance==="none" && !parent.record.hint_level && !rows("hints",p.id).some(h=>h.version===e.version && h.activity_id===e.activity_id),"独立完成证据与辅助记录冲突。");}
    }
    for(const row of data.checks) {
      const r=row.data,p=ps.get(row.project_id),rf=["id","status","threshold","question_count","version","source_hash","created_at","scope","concept_id","activity_ids","questions","initial_exposure"];
      if(r.mode==="practice")rf.push("mode","answered_count","current_index","results","attempt_ids");
      if(r.status==="finished")rf.push("score","correct_count","passed","independent","finished_at","attempt_ids","results","mastery_claim");
      fields(r,rf); if(r.concept_id!==null)identity(r.concept_id);
      validateHistorical(r,p); assert(["active","finished"].includes(r.status) && ["all","wrong","due"].includes(r.scope) && r.threshold===80 && typeof r.initial_exposure==="boolean","检测记录结构不正确。"); integer(r.question_count,1,30); refs(r.activity_ids,new Set(r.activity_ids),r.question_count,r.question_count,"检测活动"); list(r.questions,r.question_count,r.question_count); assert(same(r.questions.map(q=>q.id),r.activity_ids),"检测题目关联不正确。"); r.questions.forEach(q=>{const a=activityMap.get(row.project_id+":"+q.id); if(r.version===p.version) assert(a,"当前检测活动不存在。"); checkQuestion(q,r.version===p.version?a:null);});
      assert(r.mode===undefined || r.mode==="practice","检测模式无效。"); const count=r.mode==="practice"?integer(r.answered_count,0,r.question_count):r.status==="finished"?r.question_count:0;
      if(r.mode==="practice") assert(r.current_index===count && (r.status==="finished")===(count===r.question_count),"练习进度不匹配。");
      if(r.mode==="practice" || r.status==="finished") {
        list(r.results,count,count); list(r.attempt_ids,count,count); assert(new Set(r.attempt_ids).size===count,"检测作答重复。");
        r.results.forEach((result,i)=>{fields(result,["activity_id","correct","score","answer_ids","explanation","answer",...(r.mode==="practice"?["guessed","needs_review"]:[])]); const q=r.questions[i],a=attemptMap.get(r.attempt_ids[i]); assert(a && a.pid===row.project_id && a.record.activity_id===q.id && a.record.version===r.version && a.record.source_hash===r.source_hash && result.activity_id===q.id,"检测作答关联不完整。"); assert(result.correct===(a.record.status==="correct") && result.score===a.record.score && same(result.answer,a.record.answer) && same(result.answer_ids,a.record.assessment.answer_ids) && result.explanation===a.record.explanation,"检测结果与首答记录不一致。"); if(r.mode==="practice") assert(typeof result.guessed==="boolean" && result.guessed===Boolean(a.record.guessed) && result.needs_review===(!result.correct || result.guessed),"练习猜测或复习标记不一致。");});
      }
      if(r.status==="finished") {
        timestamp(r.finished_at); const correct=r.results.filter(x=>x.correct).length,score=Math.round(correct*10000/r.question_count)/100;
        assert(r.correct_count===correct && r.score===score && r.passed===(score>=80) && typeof r.independent==="boolean" && r.mastery_claim===masteryClaim(score),"检测分数或验收结论不正确。");
        if(r.independent) {
          const before=[]; for(const ar of data.attempts) {if(r.attempt_ids.includes(ar.data.id))break; before.push(ar);}
          assert(!r.initial_exposure && !r.results.some(x=>x.guessed) && !r.attempt_ids.some(id=>{const a=attemptMap.get(id).record; return a.hint_level || a.assistance!=="none" || a.guessed;}) && !before.some(ar=>ar.project_id===row.project_id && ar.data.version===r.version && r.activity_ids.includes(ar.data.activity_id)),"独立标记与历史提示、辅助或首答冲突。");
        }
      }
    }
    return {schema_version:2,app:"project-study",exported_at:raw.exported_at||new Date().toISOString(),data,privacy:"不包含本机原项目绝对路径；包含源码快照与个人答案，请自行保管。"};
  }
  function masteryClaim(score) {return score>=80?"本次客观检测通过；选择题检测不证明独立掌握。":"本次客观检测未达 80 分；请复习后再检测。";}
  function emptyBackup() {return {schema_version:2,app:"project-study",exported_at:new Date().toISOString(),data:Object.fromEntries(TABLES.map(t=>[t,[]])),privacy:"包含源码快照与个人答案，请自行保管。"};}
  function indexedDBStorage(name="shijie-offline-v1") {
    let connection;
    async function open() {if(connection)return connection; assert(globalThis.indexedDB,"离线存储不可用，请更新 Android System WebView。"); connection=await new Promise((resolve,reject)=>{const req=indexedDB.open(name,1); req.onupgradeneeded=()=>req.result.createObjectStore("state"); req.onsuccess=()=>{const db=req.result; db.onversionchange=()=>{db.close(); connection=null;}; resolve(db);}; req.onerror=()=>reject(new ProtocolError("本机数据库无法打开；原数据未被覆盖。",503)); req.onblocked=()=>reject(new ProtocolError("数据库正在更新，请关闭其他拾阶页面后重试。",503));}); return connection;}
    return {
      async load() {const db=await open(); return new Promise((resolve,reject)=>{const tx=db.transaction("state","readonly"),req=tx.objectStore("state").get("main"); let value; req.onsuccess=()=>value=req.result; tx.oncomplete=()=>resolve(value??null); tx.onabort=tx.onerror=()=>reject(new ProtocolError("本机数据读取失败；请勿清除应用数据。",503));});},
      async save(value) {const db=await open(); return new Promise((resolve,reject)=>{let tx; try {tx=db.transaction("state","readwrite",{durability:"strict"});} catch {tx=db.transaction("state","readwrite");} tx.objectStore("state").put(value,"main"); tx.oncomplete=()=>resolve(); tx.onabort=tx.onerror=()=>reject(new ProtocolError("本机保存失败（空间或权限不足），本次操作未写入。请导出备份后检查设备。",503));});},
      close() {connection?.close(); connection=null;}
    };
  }
  async function createStore(config={}) {
    const storage=config.storage||indexedDBStorage(config.databaseName),clock=config.clock||(()=>new Date()),random=config.random||(()=>{const a=new Uint32Array(1); cryptoAPI().getRandomValues(a); return a[0]/4294967296;});
    const now=()=>{const value=clock(); return (value instanceof Date?value:new Date(value)).toISOString().replace(".000Z","+00:00");};
    let saved=await storage.load(),state;
    if(saved!==null && saved!==undefined) {fields(saved,["format","backup","drafts"]); assert(saved.format===1 && Array.isArray(saved.drafts),"本机数据库格式无效；未自动清空数据。"); state={format:1,backup:await validateBackup(saved.backup),drafts:[]}; /* 进程重启后必须重新预览草稿。 */}
    else {state={format:1,backup:await validateBackup(config.seed||emptyBackup()),drafts:[]}; await storage.save(clone(state));}
    let pending=Promise.resolve();
    function serial(fn) {const current=pending.then(fn); pending=current.catch(()=>{}); return current;}
    function rows(st,table,pid) {const all=st.backup.data[table]; return table==="projects"?all:all.filter(r=>pid===undefined || r.project_id===pid).map(r=>r.data);}
    function project(st,pid) {identity(pid); const p=rows(st,"projects").find(p=>p.id===pid); assert(p,"项目不存在。"); return p;}
    function activity(st,pid,aid) {identity(aid); const a=rows(st,"activities",pid).find(a=>a.id===aid); assert(a,"活动不存在或已不属于当前版本。"); return a;}
    function insert(st,table,pid,record) {if(table==="projects") st.backup.data.projects.push(record); else st.backup.data[table].push({project_id:pid,data:record});}
    function replace(st,table,pid,record) {const row=st.backup.data[table].find(r=>r.project_id===pid && r.data.id===record.id); assert(row,"记录不存在。"); row.data=record;}
    function history(st,pid,aid) {const p=project(st,pid); return rows(st,"attempts",pid).filter(r=>!aid || r.activity_id===aid).map(r=>({...r,stale:r.version!==p.version || r.source_hash!==p.source_hash}));}
    function summaryActivity(st,pid,a) {const p=project(st,pid),his=history(st,pid,a.id),current=his.filter(h=>!h.stale); let due_at=null; if(a.due_after_hours) {const first=history(st,pid).find(r=>!r.stale && r.concept_id===a.concept_id && r.kind!=="transfer"); if(first) due_at=new Date(Date.parse(first.created_at)+a.due_after_hours*3600000).toISOString();} return {id:a.id,concept_id:a.concept_id,kind:a.kind,title:a.title,status:current.at(-1)?.status||(his.length?"stale":"not_started"),due_at,option_mode:a.option_mode,objective:Boolean(a.options.length&&a.answer_ids.length),delay_pending:Boolean(a.due_after_hours && (!due_at || Date.parse(due_at)>Date.parse(now()))),attempt_count:his.length,first_attempt:his[0]||null,last_attempt:his.at(-1)||null,evidence_count:rows(st,"evidence",pid).filter(e=>e.activity_id===a.id && e.version===p.version).length};}
    function reviewState(st,pid) {const p=project(st,pid),settings=rows(st,"review_settings",pid),interval=settings.at(-1)?.interval_days||3,history=rows(st,"reviews",pid).map(r=>({...r,stale:r.version!==p.version || r.source_hash!==p.source_hash})),current=history.filter(r=>!r.stale),due_at=current.length?new Date(Date.parse(current.at(-1).created_at)+interval*86400000).toISOString():p.updated_at; return {interval_days:interval,due:!current.length || Date.parse(due_at)<=Date.parse(now()),due_at,current_version:p.version,prompts:REVIEW_PROMPTS,history,mastery_claim:"回顾保存本人解释与辅助程度，不自动评分，也不证明独立掌握。"};}
    function projectSummary(st,pid) {const p=project(st,pid),s=rows(st,"project_summaries",pid).filter(r=>r.version===p.version && r.source_hash===p.source_hash).at(-1); if(s)return {...s.content,origin:"agent_proposed",version:p.version,source_hash:p.source_hash}; const fs=rows(st,"files",pid),readme=fs.filter(f=>/^(?:readme\.md|readme\.txt)$/i.test(f.path.split("/").at(-1))).sort((a,b)=>a.path.split("/").length-b.path.split("/").length)[0],fl=rows(st,"flows",pid); return {purpose:readme?.text.slice(0,6000)||"尚无经过整理的项目作用说明。请让 Agent 依据真实项目补齐。",audience:"尚未整理；不能仅凭代码推断实际用户。",scenario:fl[0]?.scenario||"尚未整理使用场景。",capabilities:fl.map(f=>f.title).length?fl.map(f=>f.title):["尚未整理功能调用链"],learning_value:"可从已有功能学习输入、执行顺序、返回值及失败边界。",limitations:"静态源码不能证明真实运行、生产能力或本人掌握。",evidence:readme?.text.split(/\r?\n/)[0]?[{path:readme.path,line:1,quote:readme.text.split(/\r?\n/)[0].slice(0,800),sha256:readme.sha256}]:[],origin:readme?"readme_fallback":"missing",version:p.version,source_hash:p.source_hash};}
    function detail(st,pid) {const p=project(st,pid),cs=rows(st,"concepts",pid),fs=rows(st,"flows",pid),as=rows(st,"activities",pid).map(a=>summaryActivity(st,pid,a)),his=history(st,pid); return {project:p,project_summary:projectSummary(st,pid),review_state:reviewState(st,pid),concepts:cs,flows:fs,activities:as,materials:rows(st,"files",pid).filter(f=>/\.(?:md|txt)$/i.test(f.path)).map(f=>({path:f.path,sha256:f.sha256})),summary:{concept_count:cs.length,flow_count:fs.length,activity_count:as.length,attempt_count:his.length,review_required:his.filter(h=>h.status==="review_required"&&!h.stale).length,stale_attempt_count:his.filter(h=>h.stale).length,source_is_static:true,mastery_claim:"尚未验证独立掌握"}};}
    function questionSnapshot(a) {return Object.fromEntries(["id","concept_id","kind","title","prompt","options","evidence"].map(k=>[k,clone(a[k])]));}
    function submit(st,pid,aid,answer,assistance="none") {
      assert(ASSISTANCE.includes(assistance),"辅助方式不正确。"); const p=project(st,pid),a=activity(st,pid,aid); assert(rows(st,"attempts").length<10000,"作答记录已达到上限，请备份并整理项目。"); let score,status;
      if(a.options.length) {validateAnswer(a,answer); score=setEqual(answer,a.answer_ids)?100:0; status=score?"correct":"incorrect";} else {answer=text(answer); score=null; status="review_required";}
      const hints=rows(st,"hints",pid).filter(h=>h.activity_id===aid && h.version===p.version),r={id:uid(),activity_id:aid,concept_id:a.concept_id,kind:a.kind,answer:clone(answer),assistance,hint_level:Math.max(0,...hints.map(h=>h.level)),version:p.version,created_at:now(),status,score,explanation:a.explanation,rubric:clone(a.rubric),source_hash:p.source_hash,assessment:{option_ids:a.options.map(o=>o.id),answer_ids:clone(a.answer_ids)},question_snapshot:questionSnapshot(a),delayed_retest:a.kind==="transfer"&&!summaryActivity(st,pid,a).delay_pending,stale:false,mastery_claim:score!==null?"选择题分数不代表独立掌握":"主观回答待人工复核"}; insert(st,"attempts",pid,r); return {attempt:r,activity:summaryActivity(st,pid,a)};
    }
    function validateAnswer(a,answer) {list(answer,1,8,"答案"); assert(new Set(answer).size===answer.length && answer.every(id=>typeof id==="string" && a.options.some(o=>o.id===id)) && (a.option_mode!=="single" || answer.length===1),"答案必须使用有效且不重复的选项标识。");}
    function latest(st,pid) {const p=project(st,pid); return new Map(rows(st,"attempts",pid).filter(r=>r.version===p.version && r.source_hash===p.source_hash).map(r=>[r.activity_id,r]));}
    function due(r) {return Boolean(r && Date.parse(r.created_at)+(r.status==="incorrect"||r.guessed?600000:86400000)<=Date.parse(now()));}
    function publicCheck(st,pid,c) {const p=project(st,pid); return {...Object.fromEntries(Object.entries(c).filter(([k])=>!["questions","activity_ids","attempt_ids","initial_exposure"].includes(k))),stale:c.version!==p.version || c.source_hash!==p.source_hash};}
    function getCheck(st,pid,id) {identity(id); const c=rows(st,"checks",pid).find(c=>c.id===id); assert(c,"检测记录不存在。"); return c;}
    function shuffle(raw) {const result=raw.map(clone); for(let i=result.length-1;i>0;i--) {const value=random(); assert(typeof value==="number" && value>=0 && value<1,"随机数来源无效。"); const j=Math.floor(value*(i+1)); [result[i],result[j]]=[result[j],result[i]];} return result;}
    function balanced(raw,count,last,rotation) {let remaining=shuffle(raw),selected=[],prev=null; const coverage=new Map(),kinds=new Map(); const category=a=>!last.has(a.id)?"unseen":last.get(a.id).status==="incorrect"||last.get(a.id).guessed||due(last.get(a.id))?"review":"recent"; while(remaining.length && selected.length<count) {const desired=(rotation+selected.length)%2===0?"review":"unseen"; const key=a=>[coverage.get(a.concept_id)||0,a.concept_id===prev?1:0,category(a)===desired?0:category(a)!=="recent"?1:2,kinds.get(a.kind)||0,due(last.get(a.id))?0:1,last.get(a.id)?.created_at||""]; remaining.sort((a,b)=>{const ka=key(a),kb=key(b); for(let i=0;i<ka.length;i++)if(ka[i]!==kb[i])return ka[i]<kb[i]?-1:1;return 0;}); const a=remaining.shift(); selected.push(a); prev=a.concept_id; coverage.set(prev,(coverage.get(prev)||0)+1); kinds.set(a.kind,(kinds.get(a.kind)||0)+1);} return selected;}
    function exposed(st,pid,c) {return c.initial_exposure || ["attempts","hints"].some(t=>rows(st,t,pid).some(r=>r.version===c.version && r.source_hash===c.source_hash && c.activity_ids.includes(r.activity_id) && !(c.attempt_ids||[]).includes(r.id)));}
    function resultFor(a,attempt,guessed) {const r={activity_id:a.id,correct:attempt.status==="correct",score:attempt.score,answer_ids:clone(a.answer_ids),explanation:a.explanation,answer:clone(attempt.answer)}; if(guessed!==undefined) Object.assign(r,{guessed,needs_review:guessed||!r.correct}); return r;}
    function finish(st,pid,c) {const correct=c.results.filter(r=>r.correct).length,score=Math.round(correct*10000/c.question_count)/100; Object.assign(c,{status:"finished",score,correct_count:correct,passed:score>=80,independent:!exposed(st,pid,c)&&!c.results.some(r=>r.guessed),finished_at:now(),mastery_claim:masteryClaim(score)});}
    function qualityWarnings(as) {const warnings=[]; const blank=as.filter(a=>a.options.length&&!a.explanation.trim()).length,all=as.filter(a=>a.options.length===a.answer_ids.length&&a.options.length).length,dup=as.filter(a=>new Set(a.options.map(o=>o.text.trim().replace(/\s+/g," ").toLowerCase())).size!==a.options.length).length; if(blank)warnings.push(`题目质量提示：${blank} 道选择题缺少解析。`); if(all)warnings.push(`题目质量提示：${all} 道选择题所有选项均正确。`); if(dup)warnings.push(`题目质量提示：${dup} 道题存在重复选项。`); return warnings;}
    function conceptUpdates(raw,fs,existing) {list(raw,0,80,"知识讲解补充"); const ids=new Set(),mutable=["lesson","mechanism","failures","references"]; return raw.map(c=>{const old=existing.find(o=>o.id===c.id); assert(old && !ids.has(c.id),"补充知识点不存在或重复。"); ids.add(c.id); mutable.forEach(k=>assert(Object.hasOwn(c,k),"知识补充需要提供四个可追加字段。")); assert(same(Object.fromEntries(Object.entries(old).filter(([k])=>!mutable.includes(k))),Object.fromEntries(Object.entries(c).filter(([k])=>!mutable.includes(k)))),"知识补充不能改写标识、原证据或其他字段。"); ["lesson","mechanism"].forEach(k=>assert(typeof c[k]==="string" && c[k].startsWith(old[k]||""),"知识补充必须保留原讲解后追加。")); ["failures","references"].forEach(k=>assert(Array.isArray(c[k]) && same(c[k].slice(0,(old[k]||[]).length),old[k]||[]),"知识补充必须保留原条目顺序后追加。")); assert(!same(c,old),"知识讲解补充没有新增内容。"); concepts([c],fs,existing.filter(o=>o.id!==c.id),true); return clone(c);});}
    function flowUpdates(raw,cs,fs,existing) {list(raw,0,12,"调用链补充"); const checked=flows(raw,cs,fs); checked.forEach(f=>{const old=existing.find(o=>o.id===f.id); assert(old && same(old.steps.map(s=>s.id),f.steps.map(s=>s.id)),"补充调用链必须保留原标识与步骤顺序。");}); return checked;}
    function additions(st,pid,cs,as,fl) {assert(!cs.some(c=>rows(st,"concepts",pid).some(o=>o.id===c.id)) && !as.some(a=>rows(st,"activities",pid).some(o=>o.id===a.id)) && !fl.some(f=>rows(st,"flows",pid).some(o=>o.id===f.id)),"新增标识已存在，不得覆盖旧题或记录。"); assert(rows(st,"activities",pid).length+as.length<=150 && rows(st,"flows",pid).length+fl.length<=12,"项目活动或调用链达到上限。");}
    function preparePack(st,pid,pack) {
      safeJSON(pack,LIMITS.max_pack_bytes); assert(object(pack) && [1,2].includes(pack.schema_version),"任务包 schema_version 应为 1 或 2。"); fields(pack,pack.schema_version===1?["schema_version","project_version","activities","title"]:["schema_version","project_version","source_hash","title","activities","concepts","flows","project_summary","flow_updates","concept_updates"],["schema_version","project_version"]);
      const p=project(st,pid); assert(pack.project_version===p.version && (pack.schema_version===1 || pack.source_hash===p.source_hash),"任务包源码版本或哈希已过期。"); const fs=rows(st,"files",pid),old=rows(st,"concepts",pid),cs=concepts(pack.concepts||[],fs,old),cu=conceptUpdates(pack.concept_updates||[],fs,old),as=activities(pack.activities||[],old.concat(cs),fs,80),fl=flows(pack.flows||[],old.concat(cs),fs,rows(st,"flows",pid)),fu=flowUpdates(pack.flow_updates||[],old.concat(cs),fs,rows(st,"flows",pid)),su=summary(pack.project_summary,fs);
      assert(cs.length||cu.length||as.length||fl.length||fu.length||su,"学习包不能全空。"); assert(!fl.some(f=>fu.some(u=>u.id===f.id)),"新增和补充调用链不能冲突。"); additions(st,pid,cs,as,fl);
      return {id:uid(),pid,kind:"pack",project_version:p.version,source_hash:p.source_hash,title:text(pack.title||"Agent 学习任务包",120),concepts:cs,concept_updates:cu,activities:as,flows:fl,flow_updates:fu,project_summary:su,concept_update_bases:old.filter(c=>cu.some(u=>u.id===c.id)),flow_update_bases:rows(st,"flows",pid).filter(f=>fu.some(u=>u.id===f.id)),created_at:now(),schema_version:pack.schema_version};
    }
    async function prepareBundle(bundle) {
      safeJSON(bundle,LIMITS.max_bundle_bytes); fields(bundle,["schema_version","title","project","files","concepts","activities","flows","project_summary"],["schema_version","project","files","concepts","activities"]); assert(bundle.schema_version===2,"完整学习包应为 schema_version=2。"); fields(bundle.project,["name"]); const name=text(bundle.project.name,80); assert(!/[\\/:]/.test(name),"项目名称不能包含本机路径。"); const {files,report}=await collectFiles(bundle.files),cs=concepts(bundle.concepts,files),as=activities(bundle.activities,cs,files,150),fl=flows(bundle.flows||[],cs,files),su=summary(bundle.project_summary,files); assert(cs.length||as.length||fl.length||su,"完整学习包不能全空。"); return {id:uid(),pid:"__intake__",kind:"intake",schema_version:2,title:text(bundle.title||"完整学习包",120),project_name:name,files,concepts:cs,activities:as,flows:fl,project_summary:su,source_hash:await sourceHash(files),scan_report:report,created_at:now()};
    }
    function saveSummary(st,pid,su) {if(su){const p=project(st,pid); assert(rows(st,"project_summaries").length<10000,"总结记录达到上限。"); insert(st,"project_summaries",pid,{id:uid(),version:p.version,source_hash:p.source_hash,created_at:now(),content:clone(su)});}}
    async function route(st,path,method,body) {
      const url=new URL(path,"https://appassets.androidplatform.net"); assert(url.origin==="https://appassets.androidplatform.net" && url.pathname.startsWith("/api/"),"离线接口地址无效。"); const p=url.pathname.split("/").filter(Boolean).map(decodeURIComponent),pid=p[2];
      if(method==="GET" && url.pathname==="/api/health")return {ok:true,app:"project-study",version:"0.4.1",offline:true};
      if(method==="GET" && url.pathname==="/api/bootstrap")return {token:"offline-local",projects:rows(st,"projects"),limits:LIMITS,demo_available:Boolean(config.demoBundle),version:"0.4.1",offline:true};
      if(method==="GET" && url.pathname==="/api/backup")return {...clone(st.backup),exported_at:now()};
      if(method==="GET" && url.pathname==="/api/coach-prompt") {const mode=url.searchParams.get("mode")||"learning"; assert(["onboarding","learning","reference"].includes(mode),"提示词模式无效。"); return {prompt:config.prompts?.[mode]||"手机离线版不扫描电脑文件。请在电脑端让 Agent 按拾阶 schema_version=2 协议整理完整学习包，经过预览后导入；也可导入电脑站导出的完整备份。静态调用链不证明实际运行，选择题检测不证明独立掌握。"};}
      if(method==="POST" && url.pathname==="/api/restore") {assert(body.confirmed===true,"恢复会替换应用内数据，请明确确认。"); st.backup=await validateBackup(body.backup); st.drafts=[]; return {restored:true,mode:"replace",project_count:st.backup.data.projects.length,attempt_count:st.backup.data.attempts.length};}
      if(method==="POST" && ["/api/projects/scan","/api/projects/upload"].includes(url.pathname)) fail("手机离线版不扫描电脑项目，请让 Agent 整理完整学习包，或导入电脑端完整备份。",422);
      if(method==="POST" && url.pathname==="/api/projects/demo") {assert(config.demoBundle,"当前离线版没有内置示例。"); const draft=await prepareBundle(config.demoBundle); return commitIntake(st,draft);}
      if(method==="POST" && url.pathname==="/api/bundles/preview") {assert(rows(st,"projects").length<12,"最多保存 12 个项目。"); const draft=await prepareBundle(body.bundle); st.drafts=st.drafts.filter(d=>d.pid!=="__intake__"); st.drafts.push(draft); return {draft_id:draft.id,title:draft.title,project_name:draft.project_name,file_count:draft.files.length,concept_count:draft.concepts.length,activity_count:draft.activities.length,flow_count:draft.flows.length,concepts:draft.concepts,activities:draft.activities,flows:draft.flows,project_summary:draft.project_summary,warnings:["源码与资料锚点已校验；教学内容与标准答案仍需审阅。","仅保存声明式数据，不执行代码或请求外部模型。",...qualityWarnings(draft.activities)]};}
      if(method==="POST" && p[1]==="bundles" && p.length===4 && p[3]==="commit") {assert(body.confirmed===true,"请先预览并明确确认写入。"); const draft=st.drafts.find(d=>d.id===pid && d.kind==="intake"); assert(draft,"预览草稿不存在或已失效。"); return commitIntake(st,draft);}
      if(p[1]!=="projects" || !pid) fail("接口不存在。",404); project(st,pid);
      if(p.length===3) {if(method==="GET")return detail(st,pid); if(method==="DELETE") {assert(url.searchParams.get("confirmed")==="true","删除项目需要明确确认。"); st.backup.data.projects=rows(st,"projects").filter(p=>p.id!==pid); TABLES.slice(1).forEach(t=>st.backup.data[t]=st.backup.data[t].filter(r=>r.project_id!==pid)); st.drafts=st.drafts.filter(d=>d.pid!==pid); return {deleted:true,project_id:pid};}}
      const feature=p[3],id=p[4],operation=p[5];
      if(method==="GET" && feature==="source") {const wanted=relativePath(url.searchParams.get("path")),f=rows(st,"files",pid).find(f=>f.path===wanted); assert(f,"源码快照不存在。"); return {...f,version:project(st,pid).version};}
      if(method==="GET" && feature==="handoff") {const d=detail(st,pid); return {schema_version:2,...d,project_version:d.project.version,source_hash:d.project.source_hash,activities:rows(st,"activities",pid).map(a=>Object.fromEntries(Object.entries(a).filter(([k])=>!["answer_ids","explanation","hints"].includes(k)))),attempts:history(st,pid),hints:rows(st,"hints",pid),evidence:rows(st,"evidence",pid),source_files:rows(st,"files",pid),materials:rows(st,"files",pid).filter(f=>/\.(?:md|txt)$/i.test(f.path)),concept_contract:{fields:CONCEPT_FIELDS,evidence:{path:"当前相对路径",line:"1起算行号",quote:"该行片段",sha256:"完整文件UTF-8 SHA256"},references:[{title:"出处",url:"https://出处",note:"用途和版本"}],max_concepts:80,max_groups:8,lesson_format:"纯文本或 Markdown，不执行 HTML"},flow_contract:FLOW_CONTRACT,project_summary_contract:{fields:SUMMARY_FIELDS,optional:true},concept_update_contract:{field:"concept_updates",schema_version:2,mutable_fields:["lesson","mechanism","failures","references"],rule:"完整对象，仅保留原文后追加四个内容字段。"},instructions:config.prompts?.reference||"先解释项目用途与场景，再按功能整理完整方法调用链与参数追踪。只接受声明式学习包；不执行代码，不自动写本人理解。新增题使用稳定 id、固定答案、逐项解析和源码依据。"};}
      if(method==="GET" && feature==="activities" && p.length===5) {const a=activity(st,pid,id),pub=Object.fromEntries(Object.entries(a).filter(([k])=>!["answer_ids","hints","explanation"].includes(k))),version=project(st,pid).version; return {activity:{...pub,...summaryActivity(st,pid,a),hint_count:a.hints.length},history:history(st,pid,id),hints_requested:rows(st,"hints",pid).filter(h=>h.activity_id===id && h.version===version),evidence:rows(st,"evidence",pid).filter(e=>e.activity_id===id).map(e=>({...e,stale:e.version!==version}))};}
      if(method==="POST" && feature==="attempts")return submit(st,pid,body.activity_id,body.answer,body.assistance||"none");
      if(method==="POST" && feature==="hints") {const a=activity(st,pid,body.activity_id),pr=project(st,pid),level=integer(body.level,1,3,"提示级别应为 1 至 3。"); assert(level<=a.hints.length,"该活动没有这一层提示。"); const history=rows(st,"hints",pid).filter(h=>h.activity_id===a.id && h.version===pr.version),old=history.find(h=>h.level===level); if(old)return {...old,activity:summaryActivity(st,pid,a)}; assert(level<=Math.max(0,...history.map(h=>h.level))+1,"请按顺序逐级请求提示。"); const h={id:uid(),activity_id:a.id,version:pr.version,level,text:a.hints[level-1],created_at:now(),source_hash:pr.source_hash,question_snapshot:questionSnapshot(a)}; insert(st,"hints",pid,h); return {...h,activity:summaryActivity(st,pid,a)};}
      if(method==="POST" && feature==="materials") fail("手机端补充资料请让 Agent 写入完整学习包，或从电脑重新导出备份。",422);
      if(method==="POST" && feature==="evidence") fail("手机离线版不执行项目测试；请在电脑记录并验证手操证据后导入备份。",422);
      if(feature==="reviews") {
        if(method==="GET")return reviewState(st,pid);
        if(method==="POST" && id==="settings") {integer(body.interval_days,1,30,"回顾间隔应为 1 至 30 天。"); st.backup.data.review_settings=st.backup.data.review_settings.filter(r=>r.project_id!==pid); insert(st,"review_settings",pid,{id:pid,interval_days:body.interval_days,updated_at:now()}); return reviewState(st,pid);}
        if(method==="POST" && !id) {fields(body.answers,REVIEW_PROMPTS.map(p=>p.id)); const answers=Object.fromEntries(Object.entries(body.answers).map(([k,v])=>[k,text(v)])),assistance=body.assistance||"none"; assert(ASSISTANCE.includes(assistance),"辅助方式不正确。"); assert(rows(st,"reviews").length<10000,"回顾记录已达到上限。"); const pr=project(st,pid),r={id:uid(),version:pr.version,source_hash:pr.source_hash,created_at:now(),answers,assistance,status:"self_reported",mastery_claim:REVIEW_CLAIM}; insert(st,"reviews",pid,r); return {review:r,...reviewState(st,pid)};}
      }
      if(feature==="checks") {
        const pr=project(st,pid),last=latest(st,pid);
        if(method==="GET" && !id)return {checks:rows(st,"checks",pid).map(c=>publicCheck(st,pid,c))};
        if(method==="GET" && id==="inventory") {const as=rows(st,"activities",pid).filter(a=>a.options.length&&a.answer_ids.length); return {all:as.length,wrong:as.filter(a=>last.get(a.id)?.status==="incorrect").length,due:as.filter(a=>due(last.get(a.id))).length,unseen:as.filter(a=>!last.has(a.id)).length,threshold:80,review_rule:"答错或标记猜测后 10 分钟复习；明确答对后 1 天复习。"};}
        if(method==="POST" && id==="start") {
          const count=integer(body.count??5,1,30,"检测数量应为 1 至 30。"),scope=body.scope||"all",mode=body.mode||"exam",cid=body.concept_id??null; assert(["all","wrong","due"].includes(scope) && ["practice","exam"].includes(mode),"检测范围或模式不正确。"); if(cid!==null)assert(rows(st,"concepts",pid).some(c=>c.id===identity(cid)),"检测知识点不存在。"); assert(rows(st,"checks").length<10000,"检测记录已达到上限。"); const all=rows(st,"activities",pid).filter(a=>a.options.length&&a.answer_ids.length && (cid===null||a.concept_id===cid) && (scope==="all" || scope==="wrong"&&last.get(a.id)?.status==="incorrect" || scope==="due"&&due(last.get(a.id)))); assert(all.length,"当前范围没有可自动评分的选择题，请调整范围。");
          const rotation=rows(st,"checks",pid).filter(c=>c.mode==="practice" && c.version===pr.version).length,selected=balanced(all,Math.min(count,all.length),mode==="practice"?last:new Map(),mode==="practice"?rotation:0),questions=selected.map(a=>({...Object.fromEntries(["id","concept_id","title","prompt","kind","option_mode"].map(k=>[k,a[k]])),options:shuffle(a.options)})),hints=rows(st,"hints",pid).filter(h=>h.version===pr.version),c={id:uid(),status:"active",threshold:80,question_count:selected.length,version:pr.version,source_hash:pr.source_hash,created_at:now(),scope,concept_id:cid,activity_ids:selected.map(a=>a.id),questions,initial_exposure:selected.some(a=>last.has(a.id)||hints.some(h=>h.activity_id===a.id))}; if(mode==="practice")Object.assign(c,{mode:"practice",answered_count:0,results:[],attempt_ids:[],current_index:0}); insert(st,"checks",pid,c); return {check:publicCheck(st,pid,c),questions};
        }
        const c=getCheck(st,pid,id);
        if(method==="GET")return {check:publicCheck(st,pid,c),questions:c.questions};
        assert(c.version===pr.version && c.source_hash===pr.source_hash,"源码或资料版本已改变，请开始新的检测。");
        if(method==="POST" && operation==="answer") {
          assert(c.mode==="practice","只有练习模式支持单题即时反馈。"); const aid=identity(body.activity_id),guessed=body.guessed??false; assert(typeof guessed==="boolean","猜测标记应为布尔值。"); const previous=c.results.find(r=>r.activity_id===aid); if(previous)return {check:publicCheck(st,pid,c),result:previous,already_answered:true}; assert(c.status==="active" && c.activity_ids[c.current_index]===aid,"请按本轮顺序提交当前题目。"); const submitted=submit(st,pid,aid,body.answer),attempt=submitted.attempt; if(guessed)attempt.guessed=true; const r=resultFor(activity(st,pid,aid),attempt,guessed); c.results.push(r); c.attempt_ids.push(attempt.id); c.answered_count++; c.current_index=c.answered_count; if(c.answered_count===c.question_count)finish(st,pid,c); return {check:publicCheck(st,pid,c),result:r,already_answered:false};
        }
        if(method==="POST" && operation==="submit") {
          assert(c.mode!=="practice","练习模式应逐题提交。"); assert(c.status==="active","检测已经提交，不能重复提交。"); list(body.answers,c.question_count,c.question_count,"答案"); const map=new Map(); body.answers.forEach(item=>{fields(item,["activity_id","answer"]); assert(!map.has(item.activity_id) && c.activity_ids.includes(item.activity_id),"检测题目标识重复或不匹配。"); validateAnswer(activity(st,pid,item.activity_id),item.answer); map.set(item.activity_id,item.answer);}); c.results=[]; c.attempt_ids=[]; c.initial_exposure=exposed(st,pid,c); c.activity_ids.forEach(aid=>{const a=submit(st,pid,aid,map.get(aid)).attempt; c.results.push(resultFor(activity(st,pid,aid),a)); c.attempt_ids.push(a.id);}); finish(st,pid,c); return {check:publicCheck(st,pid,c),results:c.results};
        }
      }
      if(feature==="packs" && method==="POST" && id==="preview") {const draft=preparePack(st,pid,body.pack); st.drafts=st.drafts.filter(d=>d.pid!==pid); st.drafts.push(draft); return {draft_id:draft.id,title:draft.title,activity_count:draft.activities.length,concept_count:draft.concepts.length,flow_count:draft.flows.length,concepts:draft.concepts,activities:draft.activities,flows:draft.flows,project_summary:draft.project_summary,flow_updates:draft.flow_updates,concept_updates:draft.concept_updates,updated_concept_count:draft.concept_updates.length,warnings:["源码锚点已校验；教学内容和标准答案仍需审阅。","仅保存声明式 JSON 数据，不执行代码。",...qualityWarnings(draft.activities)]};}
      if(feature==="packs" && method==="POST" && operation==="commit") {
        assert(body.confirmed===true,"请先预览并明确确认写入。"); const draft=st.drafts.find(d=>d.pid===pid && d.id===id && d.kind==="pack"); assert(draft,"预览草稿不存在或已失效。"); const pr=project(st,pid); assert(draft.project_version===pr.version && draft.source_hash===pr.source_hash,"源码已变化，请重新预览。"); assert(draft.concept_update_bases.every(base=>rows(st,"concepts",pid).some(c=>same(c,base))) && draft.flow_update_bases.every(base=>rows(st,"flows",pid).some(f=>same(f,base))),"知识或调用链已在预览后更新，请重新预览。");
        const checked=preparePack(st,pid,{schema_version:draft.schema_version,project_version:draft.project_version,...(draft.schema_version===2?{source_hash:draft.source_hash,concepts:draft.concepts,concept_updates:draft.concept_updates,flows:draft.flows,flow_updates:draft.flow_updates,project_summary:draft.project_summary}:{}),activities:draft.activities,title:draft.title});
        checked.concepts.forEach(c=>insert(st,"concepts",pid,c)); checked.concept_updates.forEach(c=>replace(st,"concepts",pid,c)); checked.activities.forEach(a=>insert(st,"activities",pid,{...a,origin:"agent_import"})); checked.flows.forEach(f=>insert(st,"flows",pid,f)); checked.flow_updates.forEach(f=>replace(st,"flows",pid,f)); saveSummary(st,pid,checked.project_summary); st.drafts=st.drafts.filter(d=>d.id!==id); return {project:pr,added_count:checked.activities.length,added_concept_count:checked.concepts.length,added_flow_count:checked.flows.length,updated_concept_count:checked.concept_updates.length,updated_flow_count:checked.flow_updates.length,project_summary:projectSummary(st,pid)};
      }
      fail("页面或接口不存在。",404);
    }
    function commitIntake(st,draft) {assert(rows(st,"projects").length<12,"最多保存 12 个项目。"); const pid=uid(),at=now(); insert(st,"projects",pid,{id:pid,name:draft.project_name,version:1,source_hash:draft.source_hash,file_count:draft.files.length,created_at:at,updated_at:at,scan_report:draft.scan_report}); [["files",draft.files],["concepts",draft.concepts],["activities",draft.activities],["flows",draft.flows]].forEach(([t,rs])=>rs.forEach(r=>insert(st,t,pid,t==="activities"?{...r,origin:"agent_import"}:clone(r)))); saveSummary(st,pid,draft.project_summary); st.drafts=st.drafts.filter(d=>d.id!==draft.id); return detail(st,pid);}
    async function request(path,method="GET",body={}) {
      return serial(async()=>{
        assert(typeof path==="string" && path.length<=4000,"请求地址无效。"); assert(["GET","POST","DELETE"].includes(method),"离线请求方法不支持。"); assert(body===undefined || object(body),"请求应为 JSON 对象。");
        const mutation=method!=="GET",next=mutation?clone(state):state; if(mutation)safeJSON(body||{},LIMITS.max_backup_bytes+1048576);
        const result=await route(next,path,method,body||{});
        if(mutation) {next.backup.exported_at=now(); safeJSON(next.backup,LIMITS.max_backup_bytes); await storage.save(clone(next)); state=next;}
        return clone(result);
      });
    }
    return {request,async fetch(path,init={}) {let body; try {body=typeof init.body==="string"?JSON.parse(init.body):init.body;} catch {return {ok:false,status:400,json:async()=>({error:"JSON 内容无效。"})};} try {const data=await request(path,init.method||"GET",body); return {ok:true,status:200,json:async()=>data};} catch(error) {return {ok:false,status:error.status||503,json:async()=>({error:error.message||"离线操作未完成，原数据保持不变。"})};}},close:()=>storage.close?.()};
  }
  return {createStore,indexedDBStorage,validateBackup,sha256,sourceHash,ProtocolError,TABLES,LIMITS};
});
