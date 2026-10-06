/* Fixed answer keys and review dates live on the local server, never in the browser. */
(() => {
  'use strict';
  if(window.__CS_CONTENT_FRAME__)return;
  let controller, token='', session=null, topic='', material='', taskId='', task=null, catalog=null, activeIndex=0, busy=false;
  const $=selector=>document.querySelector(selector);
  const labels={java:'Java 基础',concurrency:'并发与 JVM',spring:'Spring 与 HTTP',data:'数据库 / Redis / MQ',project:'Agent 项目',algorithm:'算法思路'};
  const fmtDate=value=>new Date(value*1000).toLocaleString('zh-CN',{month:'numeric',day:'numeric',hour:'2-digit',minute:'2-digit'});
  const element=(tag,text,className)=>{const node=document.createElement(tag);if(text!==undefined)node.textContent=text;if(className)node.className=className;return node;};
  function message(text=''){if($('#quizStatus'))$('#quizStatus').textContent=text;}
  function safeSourceURL(value){
    try{const url=new URL(value||'index.html',location.href);if(url.origin===location.origin){if(url.pathname==='/materials/'||url.pathname==='/materials')url.pathname='/index.html';if(!/^\/(index\.html)?$/.test(url.pathname))return new URL('index.html',location.href).href;if(session?.task_id)url.searchParams.set('task',session.task_id);return url.href;}if(url.protocol==='https:')return url.href;}catch(_){}
    return new URL('index.html',location.href).href;
  }
  async function api(path,body){
    const scope=controller;if(!scope)throw new DOMException('Practice left','AbortError');
    const response=await fetch(path,{signal:scope.signal,method:body===undefined?'GET':'POST',headers:{'X-Archive-Token':token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
    const data=await response.json();if(scope!==controller||scope.signal.aborted)throw new DOMException('Practice left','AbortError');if(!response.ok)throw Error(data.error||'本机服务暂时不可用。');return data;
  }
  function listening(node,type,handler){node.addEventListener(type,handler,{signal:controller.signal});}
  function switchPanel(name){for(const id of ['quizSetup','quizRun','quizResult'])$('#'+id).hidden=id!==name;}
  function sourceLink(source){const a=element('a','查看依据：'+source.title+' · '+source.section+' ↗');a.href=safeSourceURL(source.url);return a;}
  function selectedText(q,ids){return q.options.map((o,i)=>ids.includes(o.id)?String.fromCharCode(65+i)+' '+o.text:null).filter(Boolean).join('；')||'未作答';}
  function reviewItem(q){
    const box=element('details',undefined,'quiz-result-item');const summary=element('summary',(q.feedback.correct?'✓ ':'× ')+q.title+(q.response.guessed?' · 猜的':''));box.append(summary);
    box.append(element('p','题目：'+q.prompt),element('p','你的选择：'+selectedText(q,q.response.selected)),element('p','正确选择：'+selectedText(q,q.feedback.answer)),element('p',q.feedback.explanation),sourceLink(q.feedback.source));if(q.feedback.review_paused)box.append(element('p','题目或资料版本已改变：保留本轮原题成绩，不更新当前题库的复习安排。','quiz-method'));return box;
  }
  function result(){
    switchPanel('quizResult');const r=session.result;
    $('#quizResultMode').textContent=session.mode==='exam'?'EXAM / 测验首答':'PRACTICE / 练习首答';
    $('#quizResultTitle').textContent=r.passed?'本轮通过':'继续复习';$('#quizScore').textContent=r.percent+'%';
    $('#quizResultDetail').textContent=`首答正确 ${r.correct} / ${r.total} 题；通过线 80%。${r.guessed?'其中 '+r.guessed+' 题标记为猜测，需要继续复习。':''}这份结果不代替闭卷口述或手写验收。`;
    $('#quizTaskReturn').hidden=!session.task_id;if(session.task_id)$('#quizTaskReturn').href='plan.html?task='+encodeURIComponent(session.task_id);
    $('#quizResultReview').replaceChildren(...session.questions.map(reviewItem));$('#quizResultTitle').scrollIntoView({block:'nearest'});
  }
  function renderQuestion(){
    switchPanel('quizRun');const q=session.questions[activeIndex],answered=Boolean(q.response);
    $('#quizQuestionIndex').textContent=`${activeIndex+1} / ${session.total} · ${labels[q.topic]}`;
    $('#quizQuestionType').textContent=q.type==='multiple'?'多选 · 全部选对':'单选';
    $('#quizProgressBar').style.width=(session.answered/session.total*100)+'%';
    $('#quizQuestionTitle').textContent=q.title;$('#quizPrompt').textContent=q.prompt;
    $('#quizOptions').replaceChildren(element('legend','选择答案','sr-only'));
    q.options.forEach((o,index)=>{const label=element('label',undefined,'quiz-option'),input=document.createElement('input');input.type=q.type==='multiple'?'checkbox':'radio';input.name='quiz-answer';input.value=o.id;input.checked=q.response?.selected.includes(o.id)||false;input.disabled=answered;
      if(q.feedback?.answer.includes(o.id))label.classList.add('is-answer');else if(q.feedback&&!q.feedback.correct&&input.checked)label.classList.add('is-wrong');
      label.append(input,element('b',String.fromCharCode(65+index)),element('span',o.text));$('#quizOptions').append(label);});
    $('#quizGuessed').checked=q.response?.guessed||false;$('#quizGuessed').disabled=answered;
    $('#quizSubmit').hidden=answered;$('#quizSubmit').disabled=false;$('#quizSkip').hidden=answered;$('#quizNext').hidden=!answered;$('#quizEnd').hidden=answered&&session.finished;
    $('#quizFeedback').hidden=!q.feedback;
    if(q.feedback){$('#quizVerdict').textContent=q.feedback.correct?(q.response.guessed?'选对了，仍需要复习':'回答正确'):'这题需要复习';$('#quizCorrectOptions').textContent='正确选择：'+selectedText(q,q.feedback.answer);$('#quizExplanation').textContent=q.feedback.explanation;$('#quizSource').href=safeSourceURL(q.feedback.source.url);$('#quizSource').textContent='查看依据：'+q.feedback.source.title+' · '+q.feedback.source.section+' ↗';$('#quizNextReview').textContent=q.feedback.review_paused?'题目或资料版本已改变：本次成绩仍保留，不更新当前题库的复习安排。':'下次到期：'+fmtDate(q.feedback.due_at);}
    $('#quizNext').textContent=session.finished?'查看本轮结果 →':'下一题 →';
    $('#quizQuestionTitle').focus({preventScroll:true});
    if(!answered&&matchMedia('(max-width: 720px)').matches)$('#quizRun').scrollIntoView({block:'start',behavior:'auto'});
  }
  function useSession(data){session=data;message();window.ShizhiHelp?.showTask(session.task_id||'');if(session.finished){result();return;}activeIndex=session.questions.findIndex(q=>!q.response);renderQuestion();}
  async function refresh(){
    catalog=await api('/api/quiz/catalog'+(material?'?material='+encodeURIComponent(material):''));if(!controller||controller.signal.aborted)return;
    $('#quizInventory').textContent=catalog.total+' 道可用题目'+(catalog.unavailable?' · '+catalog.unavailable+' 道来源待核对':'');
    $('#quizStart').disabled=catalog.total===0;if(catalog.total===0)message(material?'这份资料暂未编写选择题，可以切换全部题库。':'题库还没有题目。请回到资料与出题，导入原创示例，或上传资料并确认 AI 草稿。');
    $('#quizReviewStats').textContent=`已练 ${catalog.stats.practiced} / ${catalog.total} 题 · 薄弱 ${catalog.stats.wrong} 题 · 到期 ${catalog.stats.due} 题`;
    $('#quizMaterialScope').hidden=!material;$('#quizMaterialScope').replaceChildren();if(material){$('#quizMaterialScope').append(element('span','当前只练这份资料的题目。 '));const a=element('a','切换全部题库 ↗');a.href='/interview.html';$('#quizMaterialScope').append(a);}
    $('#quizTopics').replaceChildren();const all=[{id:'',label:'全部主题',count:catalog.total},...catalog.topics];
    all.forEach(t=>{const button=element('button');button.type='button';button.dataset.topic=t.id;button.setAttribute('aria-current',String(topic===t.id));button.append(element('span',t.label),element('small',String(t.count)));button.disabled=busy||Boolean(task?.topic&&task.topic!==t.id);if(task?.topic&&task.topic!==t.id)button.title='当前任务限定了练习领域。返回计划可以另建任务。';listening(button,'click',()=>{topic=t.id;$('#quizTopics').querySelectorAll('button').forEach(b=>b.setAttribute('aria-current',String(b.dataset.topic===topic)));message();});$('#quizTopics').append(button);});
    $('#quizResume').hidden=!catalog.active;
    $('#quizSources').replaceChildren();for(const source of catalog.sources||[]){const box=element('div',undefined,'quiz-source-row'),a=element('a',source.name+' ↗');a.href=safeSourceURL(source.url);a.target='_blank';a.rel='noopener noreferrer';box.append(a,element('span',source.checked_at?' 核实 '+source.checked_at:''));const articles=element('div');for(const article of source.articles||[]){const link=element('a',article.title+' ↗');link.href=safeSourceURL(article.url);link.target='_blank';link.rel='noopener noreferrer';articles.append(link);}box.append(articles);$('#quizSources').append(box);}
    $('#quizHistory').replaceChildren();if(!catalog.recent.length)$('#quizHistory').append(element('p','暂无作答记录。开始一轮练习，首答与复习安排会自动保存。','quiz-method'));
    for(const old of catalog.recent){const row=element('div',undefined,'quiz-history-row'),b=element('button',`${old.mode==='exam'?'测验':'练习'} · ${old.result.correct}/${old.result.total} · ${old.result.percent}%`);b.type='button';listening(b,'click',async()=>{try{useSession(await api('/api/quiz/sessions/'+old.id));}catch(e){message(e.message);}});row.append(b,element('span',fmtDate(old.created_at)));$('#quizHistory').append(row);}
  }
  async function submit(skip=false){
    if(busy)return;const scope=controller,q=session.questions[activeIndex],selected=skip?[]:[...$('#quizOptions').querySelectorAll('input:checked')].map(i=>i.value);
    if(!skip&&!selected.length){message('先选择答案，或点击“暂时不会”。');return;}
    busy=true;$('#quizSubmit').disabled=true;$('#quizSkip').disabled=true;message();
    try{session=await api('/api/quiz/sessions/'+session.id+'/answer',{question_id:q.id,selected,guessed:!skip&&$('#quizGuessed').checked});if(session.mode==='exam'){if(session.finished)result();else{activeIndex++;renderQuestion();}}else renderQuestion();}
    catch(e){if(e.name!=='AbortError')message(e.message);}finally{if(scope===controller){busy=false;if($('#quizSubmit')){$('#quizSubmit').disabled=false;$('#quizSkip').disabled=false;}}}
  }
  function destroy(){controller?.abort();controller=null;session=null;busy=false;}
  async function mount(){
    destroy();if(!$('#quizApp'))return;controller=new AbortController();const scope=controller;token='';const params=new URL(window.CodeSprintRouter?.currentURL||location.href).searchParams;topic=labels[params.get('topic')]?params.get('topic'):'';material=params.get('material')||'';taskId=params.get('task')||'';task=null;
    if(location.protocol==='file:'){message('请先启动本项目的本地服务，再在浏览器中进入记忆练习。');$('#quizStart').disabled=true;return;}
    listening($('#quizStartForm'),'submit',async event=>{event.preventDefault();if(busy)return;busy=true;$('#quizStart').disabled=true;try{const request={mode:$('#quizMode').value,scope:$('#quizScope').value,count:Number($('#quizCount').value)};if(taskId){request.task_id=taskId;if(!task?.topic&&topic)request.topic=topic;if(!task?.material_id&&material)request.material=material;}else{request.topic=topic;request.material=material;}useSession(await api('/api/quiz/sessions',request));}catch(e){if(e.name!=='AbortError')message(e.message);}finally{if(scope===controller){busy=false;if($('#quizStart'))$('#quizStart').disabled=false;}}});
    listening($('#quizMode'),'change',()=>{if($('#quizMode').value==='exam'){$('#quizScope').value='all';$('#quizCount').value='20';}});
    listening($('#quizAnswerForm'),'submit',event=>{event.preventDefault();submit();});listening($('#quizSkip'),'click',()=>submit(true));
    listening($('#quizNext'),'click',()=>{if(session.finished)result();else{activeIndex++;renderQuestion();}});
    listening($('#quizEnd'),'click',async()=>{if(busy)return;busy=true;$('#quizEnd').disabled=true;try{useSession(await api('/api/quiz/sessions/'+session.id+'/finish',{}));}catch(e){if(e.name!=='AbortError')message(e.message);}finally{if(scope===controller){busy=false;if($('#quizEnd'))$('#quizEnd').disabled=false;}}});
    listening($('#quizAgain'),'click',async()=>{switchPanel('quizSetup');message();window.ShizhiHelp?.showTask(taskId);try{await refresh();}catch(e){if(e.name!=='AbortError')message(e.message);}});
    listening($('#quizResume'),'click',async()=>{try{useSession(await api('/api/quiz/sessions/'+catalog.active));}catch(e){if(e.name!=='AbortError')message(e.message);}});
    listening(document,'keydown',event=>{if(event.repeat||event.ctrlKey||event.metaKey||event.altKey||!session||$('#quizRun').hidden||event.target.closest('a,button,select'))return;if(/^[1-4]$/.test(event.key)&&!session.questions[activeIndex].response){const inputs=[...$('#quizOptions').querySelectorAll('input')],input=inputs[Number(event.key)-1];if(input&&!input.disabled){event.preventDefault();input.checked=input.type==='radio'||!input.checked;input.dispatchEvent(new Event('change',{bubbles:true}));}}else if(event.key==='Enter'){event.preventDefault();if(!$('#quizNext').hidden)$('#quizNext').click();else submit();}});
    try{token=(await api('/api/bootstrap')).token;if(taskId){task=await window.ShizhiHelp.getTask(taskId);topic=task.topic||topic;material=task.material_id||material;}await refresh();}catch(e){if(e.name!=='AbortError')message(e.message);$('#quizStart').disabled=true;}
  }
  window.CodeSprintQuiz={mount,destroy};document.addEventListener('codesprint:navigation-start',destroy);
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',()=>{if($('#quizApp'))mount();},{once:true});else if(!window.__CS_ROUTER_MOUNTING__&&$('#quizApp'))mount();
})();
