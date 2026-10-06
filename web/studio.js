/* Materials stay local; only a selected, consented text is submitted for drafting. */
(() => {
  'use strict';
  const $=selector=>document.querySelector(selector);
  const node=(tag,text,className)=>{const n=document.createElement(tag);if(text!==undefined)n.textContent=String(text);if(className)n.className=className;return n;};
  const state={token:'',bootstrap:null,items:[],selected:null,trash:false,busy:false,draft:null,searchTimer:null,revision:0};
  const topicNames={java:'Java 基础',concurrency:'并发与 JVM',spring:'Spring 与 HTTP',data:'数据库 / Redis / MQ',project:'Agent 项目',algorithm:'算法思路'};
  const focusNames={mechanism:'运行机制',scenario:'实际场景',boundary:'设计边界',debug:'故障判断',design:'设计取舍'};
  const presets={
    collections:{topic:'java',keywords:'HashMap, equals/hashCode, 扩容, 遍历, 线程安全, 集合选择'},
    concurrency:{topic:'concurrency',keywords:'线程池, 拒绝策略, 可见性, 原子性, CAS, JVM 内存, GC, 并发隔离'},
    spring:{topic:'spring',keywords:'IOC, AOP, 代理调用, 事务边界, 参数校验, 异常处理, HTTP 状态码'},
    redis:{topic:'data',keywords:'TTL, 缓存一致性, Lua 原子操作, 限流, 幂等, 重复消费, Offset, 重试'},
    agent:{topic:'project',keywords:'Tool Calling, 可信身份, 参数校验, 对象级权限, 超时, RAG 引用, 拒答, 评测'}
  };
  function message(text=''){ $('#studioStatus').textContent=text; }
  function describeError(error){return error.name==='AbortError'?'请求已取消。':error.message||'请求失败，请检查服务是否仍在运行。';}
  async function api(path,options={}) {
    const headers={'X-Archive-Token':state.token,...(options.headers||{})};
    const response=await fetch(path,{...options,headers,credentials:'same-origin'});
    if(!response.ok){let data;try{data=await response.json();}catch(_){throw Error('本地服务返回异常，请重试。');}throw Error(data.error||'操作没有完成。');}
    return options.download?response:response.json();
  }
  const post=(path,body)=>api(path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  const patch=(path,body)=>api(path,{method:'PATCH',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
  function size(bytes){if(bytes<1024)return bytes+' B';if(bytes<1024*1024)return (bytes/1024).toFixed(1)+' KB';return (bytes/1024/1024).toFixed(1)+' MB';}
  function categoryLabel(id){const entry=(state.bootstrap?.categories||[]).find(c=>typeof c==='object'&&c.id===id);return entry?.label||entry?.name||id||'未分类';}
  function fillCategories(){
    for(const entry of state.bootstrap.categories||[]){const id=typeof entry==='string'?entry:entry.id,label=typeof entry==='string'?entry:entry.label||entry.name||id;for(const select of [$('#materialCategory'),$('#uploadCategory')]){const option=node('option',label);option.value=id;select.append(option);}}
    if(!$('#uploadCategory').options.length){for(const [id,label]of Object.entries(topicNames)){const o=node('option',label);o.value=id;$('#uploadCategory').append(o);}}
  }
  function clearDraft(){state.draft=null;$('#draftPreview').hidden=true;$('#draftQuestions').replaceChildren();$('#commitConsent').checked=false;$('#commitButton').disabled=true;$('#draftStatus').textContent='';}
  function tab(name){$('#readerPanel').hidden=name!=='read';$('#generationPanel').hidden=name!=='generate';$('#readTab').setAttribute('aria-selected',String(name==='read'));$('#generateTab').setAttribute('aria-selected',String(name==='generate'));$('#readTab').tabIndex=name==='read'?0:-1;$('#generateTab').tabIndex=name==='generate'?0:-1;}
  function displayList(){
    const list=$('#materialList');list.replaceChildren();$('#materialCount').textContent=state.items.length;
    if(!state.items.length){list.append(node('p',state.trash?'回收站为空。':'没有匹配的资料。','field-help'));return;}
    for(const item of state.items){const button=node('button',undefined,'material-item');button.type='button';button.dataset.materialId=item.id;button.setAttribute('aria-current',String(state.selected?.id===item.id));const title=node('strong');if(item.favorite)title.append(node('span','★ ','item-star'));title.append(document.createTextNode(item.title));const meta=node('small',categoryLabel(item.category)+' · '+size(item.size||0));button.append(title,meta);button.addEventListener('click',()=>selectMaterial(item.id));list.append(button);}
  }
  async function loadMaterials(){
    const revision=++state.revision,params=new URLSearchParams({search:$('#materialSearch').value.trim(),category:$('#materialCategory').value});if(state.trash)params.set('trashed','true');
    const data=await api('/api/materials?'+params);if(revision!==state.revision)return;
    state.items=data.items||[];displayList();$('#libraryEmpty').hidden=Boolean(state.selected);
  }
  function configureGeneration(){
    const configured=Boolean(state.bootstrap?.aiConfigured),isText=Boolean(state.selected?.text_content?.trim())&&!state.selected?.trashed;
    $('#generateButton').disabled=!configured||!isText||state.busy;
    const paragraph=$('#aiConfiguration');paragraph.replaceChildren();
    if(!configured){paragraph.textContent='模型尚未配置。上传、示例导入和已有题目的练习均可使用。将项目中的 .env.example 复制为 .env，填写自己的 AI_BASE_URL、AI_MODEL 和 AI_API_KEY 后重启服务并刷新此页面；详细步骤见项目 README。';}
    else if(!isText)paragraph.textContent=state.selected?.trashed?'回收站中的资料不能生成题目，请先恢复资料。':'当前文件没有可读正文。请使用 UTF-8 文本或 Markdown 资料出题。';
    else paragraph.textContent='模型已配置。每次生成仅发送你选中的这份正文；生成结果先留在草稿区，不自动写入题库。';
  }
  async function selectMaterial(id){
    if(state.busy){message('当前任务正在处理，请等待完成再切换资料。');return;}
    message();const revision=++state.revision;
    try {const item=await api('/api/materials/'+encodeURIComponent(id)+(state.trash?'?trashed=true':''));if(revision!==state.revision)return;state.selected=item;clearDraft();$('#libraryEmpty').hidden=true;$('#documentView').hidden=false;
      $('#documentTitle').textContent=item.title;$('#documentCategory').textContent=categoryLabel(item.category)+' / SOURCE';$('#documentMeta').textContent=(item.filename||'资料')+' · '+size(item.size||0)+(item.tags?.length?' · '+(Array.isArray(item.tags)?item.tags.join(' / '):item.tags):'');
      $('#documentText').textContent=item.text_content||'';$('#binaryNotice').hidden=Boolean(item.text_content?.trim());$('#favoriteButton').textContent=item.favorite?'★':'☆';$('#favoriteButton').setAttribute('aria-pressed',String(Boolean(item.favorite)));$('#trashButton').textContent=item.trashed?'恢复资料':'移入回收站';$('#practiceMaterial').href='interview.html?material='+encodeURIComponent(item.id);
      $('#downloadMaterial').href='/api/materials/'+encodeURIComponent(item.id)+'/file';$('#downloadMaterial').download=item.filename||item.title;
      $('#downloadMaterial').hidden=Boolean(item.trashed);$('#practiceMaterial').hidden=Boolean(item.trashed);
      const url=new URL(location.href);url.searchParams.set('material',id);if(item.trashed)url.searchParams.set('trashed','true');else url.searchParams.delete('trashed');history.replaceState(null,'',url);displayList();configureGeneration();tab('read');
    }catch(error){message(describeError(error));}
  }
  async function updateSelected(fields){
    if(!state.selected||state.busy)return;try{await patch('/api/materials/'+encodeURIComponent(state.selected.id),fields);const selected=state.selected.id;if(typeof fields.trashed==='boolean'){state.trash=fields.trashed;$('#trashToggle').setAttribute('aria-pressed',String(state.trash));$('#trashToggle').textContent=state.trash?'返回资料库':'回收站';}await loadMaterials();await selectMaterial(selected);message(fields.trashed===true?'资料已移入回收站，关联题目暂停出题。':fields.trashed===false?'资料已恢复。':'资料已保存。');}catch(error){message(describeError(error));}
  }
  function openUpload(){if(state.busy)return;$('#uploadStatus').textContent='';$('#uploadDialog').showModal();}
  function selectedFile(){const file=$('#uploadFile').files[0];$('#uploadFileLabel').textContent=file?file.name+' · '+size(file.size):'选择文件，或拖到这里';}
  function setBusy(value){state.busy=value;for(const id of ['uploadSubmit','demoImport','uploadOpen','emptyUpload','backupButton','restoreOpen','trashToggle','favoriteButton','trashButton','renameButton','materialCategory','materialSearch'])$('#'+id).disabled=value;configureGeneration();}
  async function upload(event){event.preventDefault();if(state.busy)return;const file=$('#uploadFile').files[0];if(!file||!$('#uploadRights').checked)return;if(file.size>50*1024*1024){$('#uploadStatus').textContent='单个资料文件不能超过 50 MB。';return;}setBusy(true);$('#uploadStatus').textContent='正在保存原文件…';
    try{const params=new URLSearchParams({filename:file.name,category:$('#uploadCategory').value,tags:$('#uploadTags').value});const result=await api('/api/upload?'+params,{method:'POST',headers:{'Content-Type':'application/octet-stream'},body:file});$('#uploadDialog').close();$('#uploadForm').reset();selectedFile();state.trash=false;$('#trashToggle').setAttribute('aria-pressed','false');$('#materialSearch').value='';$('#materialCategory').value='';setBusy(false);await loadMaterials();const id=result.id||result.item?.id||result.material?.id;if(id)await selectMaterial(id);message('资料已保存。你可以阅读原文，再选择是否让 AI 出题。');}
    catch(error){$('#uploadStatus').textContent=describeError(error);}finally{setBusy(false);}
  }
  function splitKeywords(value){return [...new Set(value.split(/[,，;；\n]/).map(v=>v.trim()).filter(Boolean))];}
  function renderDraft(data){
    state.draft=data;$('#draftPreview').hidden=false;$('#draftCount').textContent=(data.questions||[]).length+' 题';$('#commitConsent').checked=false;$('#commitButton').disabled=true;$('#draftStatus').textContent='';
    const warnings=(data.warnings||[]).map(w=>typeof w==='string'?w:JSON.stringify(w));$('#draftWarnings').hidden=!warnings.length;$('#draftWarnings').textContent=warnings.join('\n');
    const container=$('#draftQuestions');container.replaceChildren();
    (data.questions||[]).forEach((question,index)=>{const box=node('details',undefined,'draft-question');box.open=index===0;box.append(node('summary',(index+1)+'. '+question.title+' · '+(question.type==='multiple'?'多选':'单选')));const body=node('div',undefined,'draft-question-body');body.append(node('p',question.prompt));const list=node('ol',undefined,'draft-options');
      for(const [oi,option] of (question.options||[]).entries()){const row=node('li'),correct=(question.answer||[]).includes(option.id);const title=node('strong',String.fromCharCode(65+oi)+'. '+option.text+(correct?' ✓ 正确选项':''));if(correct)title.className='is-answer';row.append(title,node('p',question.option_explanations?.[option.id]||option.explanation||'请确认这项的解释是否完整。'));list.append(row);}body.append(list,node('strong','设计与答案依据'),node('p',question.explanation));body.append(node('strong','引用原文'),node('blockquote',question.evidence_quote||'未提供引用，暂不要确认。','evidence-quote'));body.append(node('p','考察：'+(focusNames[question.focus]||question.focus||'未标注')+' · 关键词：'+(question.keywords||[]).join(' / '),'draft-tags'));box.append(body);container.append(box);});
    $('#draftPreview').scrollIntoView({block:'start',behavior:matchMedia('(prefers-reduced-motion: reduce)').matches?'auto':'smooth'});
  }
  async function generate(event){
    event.preventDefault();if(state.busy||!state.selected||!state.bootstrap.aiConfigured)return;
    const keywords=splitKeywords($('#generationKeywords').value),focus=[...document.querySelectorAll('input[name="focus"]:checked')].map(x=>x.value);
    if(!keywords.length||keywords.length>12){message('请填写 1—12 个重点关键词，使用逗号分隔。');$('#generationKeywords').focus();return;}if(!focus.length){message('至少选择一种出题角度。');return;}if(!$('#generationConsent').checked){message('请先确认资料使用权限与模型发送范围。');return;}
    const payload={material_id:state.selected.id,topic:$('#generationTopic').value,count:Number($('#generationCount').value),keywords,focus,audience:'java-intern',extra_instructions:$('#generationInstructions').value.trim(),consent:true};
    clearDraft();setBusy(true);message('正在生成草稿并检查来源、答案与逐项解释，请保持页面打开…');$('#generateButton').textContent='正在生成并校验…';
    try{const draft=await post('/api/generation/drafts',payload);renderDraft(draft);message('草稿已生成，尚未写入题库。请核对原文依据与每个选项的解释。');}catch(error){message(describeError(error));}finally{setBusy(false);$('#generateButton').textContent='生成待核对草稿 ↗';}
  }
  async function commit(){if(state.busy||!state.draft||!$('#commitConsent').checked)return;setBusy(true);$('#commitButton').disabled=true;$('#draftStatus').textContent='正在确认写入…';try{const result=await post('/api/generation/drafts/'+encodeURIComponent(state.draft.id)+'/commit',{confirmed:true});$('#draftStatus').textContent=(result.already_committed?'这份草稿此前已写入，未重复添加。':'已写入 '+result.written+' 道题。')+' 当前题库 '+result.total+' 题。';$('#commitButton').textContent='已写入题库 ✓';const a=node('a','开始练习这份资料 →','text-link');a.href='interview.html?material='+encodeURIComponent(state.selected.id);$('#draftStatus').append(document.createTextNode(' '),a);state.draft=null;}catch(error){$('#draftStatus').textContent=describeError(error);$('#commitButton').disabled=false;}finally{setBusy(false);}}
  async function demo(){if(state.busy)return;setBusy(true);message('正在导入可合法分发的原创示例…');try{await post('/api/demo/import',{confirmed:true});setBusy(false);await loadMaterials();if(state.items[0])await selectMaterial(state.items[0].id);message('原创示例已导入。可阅读资料，或直接前往记忆练习。重复导入不会重复添加。');}catch(error){message(describeError(error));}finally{setBusy(false);}}
  async function downloadBackup(){if(state.busy)return;setBusy(true);message('正在生成完整备份…');try{const response=await api('/api/backup',{download:true}),blob=await response.blob(),url=URL.createObjectURL(blob),a=node('a');a.href=url;a.download='codesprint-backup-'+new Date().toISOString().slice(0,10)+'.zip';document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),60000);message('备份已交给浏览器下载，请保存在私人位置；其中包含你的资料和练习记录。');}catch(error){message(describeError(error));}finally{setBusy(false);}}
  async function restore(event){event.preventDefault();if(state.busy)return;const file=$('#restoreFile').files[0];if(!file||!$('#restoreConsent').checked)return;if(file.size>128*1024*1024){$('#restoreStatus').textContent='备份 ZIP 不能超过 128 MB。';return;}setBusy(true);$('#restoreSubmit').disabled=true;$('#restoreStatus').textContent='正在校验备份完整性并恢复…';try{const result=await api('/api/restore?confirmed=true',{method:'POST',headers:{'Content-Type':'application/zip'},body:file});$('#restoreDialog').close();$('#restoreForm').reset();state.selected=null;clearDraft();$('#documentView').hidden=true;state.trash=false;$('#trashToggle').setAttribute('aria-pressed','false');$('#materialSearch').value='';$('#materialCategory').value='';const url=new URL(location.href);url.searchParams.delete('material');history.replaceState(null,'',url);await loadMaterials();message('恢复完成：'+result.materials+' 份资料、'+result.questions+' 道题。');}catch(error){$('#restoreStatus').textContent=describeError(error);}finally{setBusy(false);$('#restoreSubmit').disabled=false;}}
  async function mount(){
    $('#uploadOpen').addEventListener('click',openUpload);$('#emptyUpload').addEventListener('click',openUpload);document.querySelectorAll('[data-upload-close]').forEach(b=>b.addEventListener('click',()=>{if(!state.busy)$('#uploadDialog').close();}));$('#uploadDialog').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});$('#uploadFile').addEventListener('change',selectedFile);$('#uploadForm').addEventListener('submit',upload);
    const drop=$('#fileDrop');for(const event of ['dragenter','dragover'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.add('is-dragging');});for(const event of ['dragleave','drop'])drop.addEventListener(event,e=>{e.preventDefault();drop.classList.remove('is-dragging');});drop.addEventListener('drop',event=>{if(event.dataTransfer.files.length){const transfer=new DataTransfer();transfer.items.add(event.dataTransfer.files[0]);$('#uploadFile').files=transfer.files;selectedFile();}});
    $('#materialSearch').addEventListener('input',()=>{clearTimeout(state.searchTimer);state.searchTimer=setTimeout(()=>loadMaterials().catch(e=>message(describeError(e))),180);});$('#materialCategory').addEventListener('change',()=>loadMaterials().catch(e=>message(describeError(e))));
    $('#trashToggle').addEventListener('click',()=>{if(state.busy)return;state.trash=!state.trash;$('#trashToggle').setAttribute('aria-pressed',String(state.trash));$('#trashToggle').textContent=state.trash?'返回资料库':'回收站';state.selected=null;$('#documentView').hidden=true;clearDraft();const url=new URL(location.href);url.searchParams.delete('material');if(state.trash)url.searchParams.set('trashed','true');else url.searchParams.delete('trashed');history.replaceState(null,'',url);loadMaterials().catch(e=>message(describeError(e)));});
    $('#favoriteButton').addEventListener('click',()=>updateSelected({favorite:!state.selected.favorite}));$('#trashButton').addEventListener('click',()=>updateSelected({trashed:!state.selected.trashed}));$('#renameButton').addEventListener('click',()=>{if(!state.selected||state.busy)return;const title=prompt('资料名称',state.selected.title);if(title?.trim())updateSelected({title:title.trim()});});
    $('#readTab').addEventListener('click',()=>tab('read'));$('#generateTab').addEventListener('click',()=>tab('generate'));document.querySelector('.document-tab-group').addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();const next=event.key==='Home'?'read':event.key==='End'?'generate':event.target.id==='readTab'?'generate':'read';tab(next);$('#'+(next==='read'?'readTab':'generateTab')).focus();});$('#generationForm').addEventListener('submit',generate);document.querySelectorAll('[data-keyword]').forEach(button=>button.addEventListener('click',()=>{const preset=presets[button.dataset.keyword];$('#generationKeywords').value=preset.keywords;$('#generationTopic').value=preset.topic;}));$('#commitConsent').addEventListener('change',()=>{$('#commitButton').disabled=!$('#commitConsent').checked||!state.draft||state.busy;});$('#commitButton').addEventListener('click',commit);$('#demoImport').addEventListener('click',demo);$('#backupButton').addEventListener('click',downloadBackup);
    $('#restoreOpen').addEventListener('click',()=>{if(!state.busy){$('#restoreStatus').textContent='';$('#restoreDialog').showModal();}});document.querySelectorAll('[data-restore-close]').forEach(b=>b.addEventListener('click',()=>{if(!state.busy)$('#restoreDialog').close();}));$('#restoreDialog').addEventListener('cancel',e=>{if(state.busy)e.preventDefault();});$('#restoreForm').addEventListener('submit',restore);
    if(location.protocol==='file:'){message('请先启动本项目的本地服务，再在浏览器中打开。直接双击 HTML 无法保存资料或练习记录。');setBusy(true);return;}
    try{state.bootstrap=await api('/api/bootstrap');state.token=state.bootstrap.token;fillCategories();const params=new URL(location.href).searchParams;state.trash=params.get('trashed')==='true';$('#trashToggle').setAttribute('aria-pressed',String(state.trash));$('#trashToggle').textContent=state.trash?'返回资料库':'回收站';await loadMaterials();const id=params.get('material');if(id)await selectMaterial(id);configureGeneration();}catch(error){message(describeError(error));setBusy(true);}
  }
  window.CodeSprintStudio={refresh:loadMaterials,select:selectMaterial};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',mount,{once:true});else mount();
})();
