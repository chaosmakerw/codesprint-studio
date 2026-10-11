'use strict';
/* Four-stage learning journey checks. Uses an owned temporary database/browser. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');
const crypto = require('node:crypto');
const {spawn} = require('node:child_process');
let playwright;
try { playwright = require('playwright'); } catch (_) { playwright = require('../.qa/node_modules/playwright'); }
const ROOT = path.resolve(__dirname, '..');
const OUTPUT = path.join(ROOT, 'output', 'learning-journey');
const EDGE = process.env.STUDY_QA_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
let temporary, server, browser, page, base, token, javaBundle, javaId;
let baselineHashes;
const results = [], pageErrors = [], externalRequests = [];
const privateInputs = {};
let injectedRequestsBlocked = 0;

async function sourceHashes() {
  const entries = [];
  async function collect(directory) {
    for (const item of await fs.readdir(directory, {withFileTypes:true})) {
      if (item.name === '__pycache__') continue;
      const filename = path.join(directory, item.name);
      if (item.isDirectory()) await collect(filename);
      else entries.push([path.relative(ROOT, filename).split(path.sep).join('/'), crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex')]);
    }
  }
  for (const directory of ['src', 'web', 'prompts', 'examples']) await collect(path.join(ROOT, directory));
  return Object.fromEntries(entries.sort((a,b) => a[0].localeCompare(b[0])));
}
async function check(name, action) {
  try {
    await action(); results.push({name, passed:true}); process.stdout.write('PASS ' + name + '\n');
  } catch (error) {
    if(page)try{await page.screenshot({path:path.join(OUTPUT,'failure-'+name.replace(/[^a-z0-9]+/gi,'-').slice(0,90)+'.png'),fullPage:false});}catch(_){}
    results.push({name, passed:false, error:error.message}); throw error;
  }
}
async function api(route, method='GET', body, expected=200) {
  const response = await fetch(base + route, {method, headers:{Origin:base,'X-Study-Token':token || '','Content-Type':'application/json'}, ...(body === undefined ? {} : {body:JSON.stringify(body)})});
  const value = await response.json();
  assert.equal(response.status, expected, 'API status ' + route + ': ' + JSON.stringify(value));
  return value;
}
async function start() {
  baselineHashes = await sourceHashes();
  temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'shijie-journey-'));
  const command = process.env.STUDY_QA_PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
  const args = [...(process.platform === 'win32' && !process.env.STUDY_QA_PYTHON ? ['-3.12'] : []), path.join(ROOT,'scripts','qa_server.py'), '--data-dir', temporary];
  server = spawn(command,args,{cwd:ROOT,windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
  let errors=''; server.stderr.on('data',chunk=>{ errors += chunk.toString(); });
  const lines = readline.createInterface({input:server.stdout});
  const ready = await new Promise((resolve,reject)=>{
    const timer = setTimeout(()=>reject(Error('Owned server startup timeout')),15000);
    lines.once('line',line=>{ clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (_) { reject(Error('Invalid server readiness response')); } });
    server.once('error',error=>{ clearTimeout(timer); reject(error); });
    server.once('exit',()=>{ clearTimeout(timer); reject(Error('Owned server exited: ' + errors)); });
  });
  lines.close(); base='http://127.0.0.1:' + ready.port; token=(await api('/api/bootstrap')).token;
  browser=await playwright.chromium.launch({executablePath:EDGE,headless:true});
  page=await browser.newPage({viewport:{width:1440,height:1000},deviceScaleFactor:1});
  page.on('pageerror',error=>pageErrors.push(error.message));
  await page.route('**/*',async route=>{
    const url=new URL(route.request().url());
    // The installed local antivirus injects this known host. Keep its block
    // inside the disposable context and count every other external request.
    if (url.hostname === 'me.kis.v2.scr.kaspersky-labs.com') { injectedRequestsBlocked++; await route.abort(); return; }
    if (url.origin !== base) { externalRequests.push(url.origin); await route.abort(); } else await route.continue();
  });
  await fs.mkdir(OUTPUT,{recursive:true});
  javaBundle=JSON.parse(await fs.readFile(path.join(ROOT,'examples','java-demo','learning-bundle.json'),'utf8'));
}
async function stop() {
  if (browser) await browser.close();
  if (server && server.exitCode === null) {
    const exited=new Promise(resolve=>server.once('exit',resolve));
    server.stdin.write('stop\n'); server.stdin.end();
    await Promise.race([exited,new Promise(resolve=>setTimeout(resolve,5000))]);
    if (server.exitCode === null) server.kill();
  }
  if (temporary) {
    const resolved=path.resolve(temporary), parent=path.resolve(os.tmpdir());
    assert(resolved.startsWith(parent+path.sep) && path.basename(resolved).startsWith('shijie-journey-'), 'Cleanup must stay inside owned temporary directory');
    await fs.rm(resolved,{recursive:true,force:true});
  }
}

async function openView(view) {
  await page.locator('.view-nav [data-view="'+view+'"]').click();
  await page.locator('#'+view+'-view').waitFor({state:'visible'});
}
async function selectProject(pid,expectedName) {
  await page.locator('#project-select').selectOption(pid);
  await page.waitForFunction(id=>document.querySelector('#project-select').value===id,pid);
  await page.locator('#sidebar-project-name').waitFor({state:'visible'});
  if(expectedName)await page.waitForFunction(name=>document.querySelector('#sidebar-project-name').textContent===name,expectedName);
}
async function answersForCurrent(bundle, chosen) {
  const aid=await page.locator('#check-question').getAttribute('data-activity-id');
  const activity=bundle.activities.find(item=>item.id===aid); assert(activity,'Question must belong to imported source-bound bundle');
  const answer=chosen ? chosen(activity) : activity.answer_ids;
  for (const id of answer) await page.locator('#check-question input[name="check-answer"][value="'+id+'"]').check();
  return {activity,answer};
}
async function startQuiz({mode='practice',scope='all',count=5,concept='' }={}) {
  await openView('check');
  await page.locator('#check-mode').selectOption(mode);
  await page.locator('#check-scope').selectOption(scope);
  await page.locator('#check-count').fill(String(count));
  await page.locator('#check-concept-filter').selectOption(concept);
  const ready=page.waitForResponse(response=>response.url().endsWith('/checks/start') && response.request().method()==='POST');
  await page.locator('#check-start').click();
  const response=await ready, data=await response.json();assert.equal(response.status(),200,'Start a fresh valid round');
  await page.waitForFunction(id=>document.querySelector('#check-question')?.dataset.activityId===id && !document.querySelector('#check-start').disabled && !document.querySelector('#check-feedback'),data.questions[0].id);
  await page.locator('#check-question').waitFor({state:'visible'});
}
async function verifySummary(bundle,pid,label) {
  await check(label+': project purpose and learning meaning form the first stage',async()=>{
    await openView('overview');
    const actual=await api('/api/projects/'+pid);
    assert(actual.project_summary,'A project must have a structured summary');
    for (const field of ['purpose','audience','scenario','learning_value','limitations']) {
      const node=page.locator('[data-summary-field="'+field+'"]');
      assert.equal(await node.count(),1,'Summary field rendered once: '+field);
      assert((await node.innerText()).includes(bundle.project_summary[field]),'Full summary text: '+field);
    }
    const content=await page.locator('#project-overview').innerText();
    for (const capability of bundle.project_summary.capabilities) assert(content.includes(capability));
    assert.equal(await page.locator('#source-dialog').isVisible(),false);
    assert.equal(await page.locator('#project-overview pre').count(),0,'Summary should not be a source code wall');
    await page.screenshot({path:path.join(OUTPUT,label.toLowerCase()+'-overview-desktop.png'),fullPage:true});
  });
}
async function verifyFlows(bundle,pid,label) {
  await check(label+': every declared method is visible in its feature chain',async()=>{
    await openView('flow');
    for (const flow of bundle.flows) {
      await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
      assert.equal(await page.locator('#flow-steps .flow-step').count(),flow.steps.length);
      const signatures=[];
      for (const step of flow.steps) {
        for (const method of step.methods || []) {
          const node=page.locator('#flow-steps [data-step-id="'+step.id+'"] .method-node[data-method-id="'+method.id+'"]');
          assert.equal(await node.count(),1,'One visible method per declared node: '+method.signature);
          await node.locator('.method-explanation > summary').click();
          const content=await node.innerText();
          for (const value of [method.signature,method.input,method.output]) assert(content.includes(value),'Method field must be readable without finding code');
          await node.locator('.method-explanation > summary').click();
          signatures.push(method.signature);
        }
      }
      assert(signatures.length>=flow.steps.length,'A complete scene has method-level nodes');
      assert.deepEqual(await page.locator('#flow-steps .method-signature').allTextContents(),signatures,'Declared order is rendered in order');
      assert.equal(await page.locator('#source-dialog').isVisible(),false);
      assert.equal(await page.locator('#flow-detail .code-evidence[open]').count(),0,'Source is optional');
    }
    await page.locator('#flow-list [data-flow-id]').first().click();
    await page.screenshot({path:path.join(OUTPUT,label.toLowerCase()+'-flow-desktop.png'),fullPage:false});
  });
  await check(label+': normal and rejected examples show the entire ordered parameter journey',async()=>{
    let sawRejected=false,sawReturn=false;
    for (const flow of bundle.flows) {
      await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
      for (const trace of flow.trace_cases) {
        await page.locator('#trace-case-select').selectOption(trace.id);
        await page.locator('#trace-expand-all').click();
        const events=page.locator('#trace-timeline .trace-event');
        assert.equal(await events.count(),trace.frames.length,'Every recorded call/return frame appears at once');
        for (let index=0;index<trace.frames.length;index++) {
          const frame=trace.frames[index],event=events.nth(index);
          assert.equal(await event.getAttribute('data-step-id'),frame.step_id);
          assert.equal(await event.getAttribute('data-method-id'),frame.method_id || null);
          const content=await event.innerText(); assert(content.includes(frame.explanation));
          for (const value of frame.values) for (const field of ['name','type','value','source']) assert(content.includes(value[field]),'Visible parameter '+field);
          const method=flow.steps.find(step=>step.id===frame.step_id)?.methods?.find(method=>method.id===frame.method_id);
          if (method?.relation==='return') sawReturn=true;
        }
        assert((await page.locator('#trace-outcome').innerText()).includes(trace.result),'Final result belongs to the complete journey');
        const skipped=trace.skipped_step_ids || [];
        const actualSkipped=await page.locator('#flow-steps .step-not-reached').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-step-id') || node.querySelector('[data-step-id]')?.getAttribute('data-step-id')));
        assert.deepEqual(actualSkipped.slice().sort(),skipped.slice().sort(),'Only explicitly skipped steps can be labelled not called');
        if (skipped.length) sawRejected=true;
      }
    }
    assert(sawRejected,'At least one illegal case explicitly stops before downstream calls');
    assert(sawReturn,'At least one method return is shown');
    assert.equal((await api('/api/projects/'+pid)).summary.attempt_count,0,'Reading does not fabricate a successful attempt');
  });
  await check(label+': step knowledge opens and returns to the same case and step',async()=>{
    const flow=bundle.flows[0],step=flow.steps.find(item=>item.concept_ids?.length);
    await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
    const trace=flow.trace_cases[flow.trace_cases.length-1];
    await page.locator('#trace-case-select').selectOption(trace.id);
    await page.locator('#flow-steps .flow-step[data-step-id="'+step.id+'"] > .flow-step-button').click();
    const cid=step.concept_ids[0],concept=bundle.concepts.find(item=>item.id===cid);
    await page.locator('#flow-step-detail [data-flow-concept-id="'+cid+'"]').click();
    await page.locator('#knowledge-view').waitFor({state:'visible'});
    assert((await page.locator('#concept-detail').innerText()).includes(concept.mechanism));
    await page.locator('#flow-back').click();
    await page.locator('#flow-view').waitFor({state:'visible'});
    assert.equal(await page.locator('#trace-case-select').inputValue(),trace.id);
    assert.equal(await page.locator('#flow-steps .flow-step[data-step-id="'+step.id+'"] > .flow-step-button').getAttribute('aria-pressed'),'true');
  });
}
async function runJourney() {
  await page.goto(base,{waitUntil:'networkidle'});
  await check('four stages are the complete main navigation; Agent setup is secondary',async()=>{
    assert.deepEqual(await page.locator('.view-nav [data-view]').evaluateAll(nodes=>nodes.map(node=>node.dataset.view)),['overview','flow','reflection','check']);
    for (const node of await page.locator('[data-agent-only]').all()) assert.equal(await node.isVisible(),false,'Upload controls must not occupy the learning frontend');
    await page.locator('#manage-open').click();
    assert.equal(await page.locator('#onboarding-copy').isVisible(),true);
    for(const node of await page.locator('[data-agent-only]').all())assert.equal(await node.isVisible(),false,'Manual Agent import controls stay hidden even inside instructions');
    const prompt=await api('/api/coach-prompt?mode=onboarding');
    assert(prompt.prompt.length>400);
    await page.keyboard.press('Escape');
  });
  await check('the original Java example imports through the documented Agent data interface',async()=>{
    const imported=await api('/api/projects/demo','POST',{}); javaId=imported.project.id;
    await page.reload({waitUntil:'networkidle'});
    assert.equal(await page.locator('#project-select').inputValue(),javaId);
    assert.equal(await page.locator('#overview-view').isVisible(),true,'First selected project opens on its purpose');
  });
  await verifySummary(javaBundle,javaId,'Java');
  await verifyFlows(javaBundle,javaId,'Java');
  await check('the trace shortcut takes the learner directly to the actual call sequence',async()=>{
    await openView('flow');await page.locator('#flow-list [data-flow-id]').first().click();
    await page.locator('#trace-jump').click();
    const box=await page.locator('#trace-panel > h3').boundingBox(),viewport=page.viewportSize();
    assert(box && box.y>=-1 && box.y<viewport.height/2,'Actual sequence heading is visible after the shortcut');
    assert((await page.locator('#flow-detail').innerText()).includes('本例'),'Candidate branches and the actual example must be distinguished');
  });
  await check('a knowledge lesson links to its objective quiz scope without reopening hidden practice views',async()=>{
    const objectiveIds=new Set(javaBundle.activities.filter(activity=>activity.options?.length && activity.answer_ids?.length).map(activity=>activity.concept_id));
    const flow=javaBundle.flows.find(flow=>flow.steps.some(step=>step.concept_ids.some(id=>objectiveIds.has(id))));
    const step=flow.steps.find(step=>step.concept_ids.some(id=>objectiveIds.has(id))),cid=step.concept_ids.find(id=>objectiveIds.has(id));
    await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
    await page.locator('#flow-steps .flow-step[data-step-id="'+step.id+'"] > .flow-step-button').click();
    await page.locator('#flow-step-detail [data-flow-concept-id="'+cid+'"]').click();await page.locator('#knowledge-view').waitFor({state:'visible'});
    await page.locator('#concept-quiz').click();await page.locator('#check-view').waitFor({state:'visible'});
    assert.equal(await page.locator('#check-concept-filter').inputValue(),cid);
    assert.equal(await page.locator('#practice-view').isVisible(),false);assert.equal((await api('/api/projects/'+javaId+'/checks')).checks.length,0,'The scope shortcut does not start or grade a hidden task');
    await page.locator('#check-concept-filter').selectOption('');
  });

  const reviewAnswers={purpose:'这个例子帮助买家只查询自己的订单，输入是工具名与空参数，输出是本人订单。',chain:'Dispatcher 先校验，再调用 OrderTools，取得服务器身份后调用 OrderService，最后返回 ToolResult。',boundary:'模型夹带 buyerId 时在 Validator 拒绝；OrderTools、身份读取与查询不会执行。',next_step:'我还需独立解释值比较和异常边界，下一次脱离提示复现非法参数的提前返回。'};
  let reviewId;
  await check('a learner chooses n days and writes four first-person project explanations',async()=>{
    await openView('reflection'); await page.locator('#review-interval').fill('7');
    await page.locator('#review-settings').evaluate(form=>form.requestSubmit());
    await page.waitForFunction(()=>document.querySelector('#review-status').textContent.includes('7'));
    for (const [key,value] of Object.entries(reviewAnswers)) await page.locator('#review-form textarea[name="'+key+'"]').fill(value);
    await page.locator('#review-assistance').selectOption('none'); await page.locator('#review-save').click();
    await page.waitForFunction(()=>document.querySelector('#review-history').querySelector('.review-record'));
    const state=await api('/api/projects/'+javaId+'/reviews');
    assert.equal(state.interval_days,7); assert.equal(state.due,false); assert.equal(state.history.length,1);
    assert.deepEqual(state.history[0].answers,reviewAnswers); assert.equal(state.history[0].status,'self_reported');
    assert.equal(Object.hasOwn(state.history[0],'score'),false); reviewId=state.history[0].id;
  });
  await check('periodic feedback survives reload and keeps evidence boundaries visible',async()=>{
    await page.reload({waitUntil:'networkidle'}); await openView('reflection');
    await page.getByText('过去的理解与反馈',{exact:true}).click();
    await page.locator('#review-history .review-record > summary').click();
    assert.equal(await page.locator('#review-interval').inputValue(),'7');
    const history=await page.locator('#review-history').innerText();
    for (const value of Object.values(reviewAnswers)) assert(history.includes(value));
    const view=await page.locator('#reflection-view').innerText();
    assert(/未验证|不证明|不自动|不会.*评分|不用关键词.*打分/.test(view),'A self-report must not be an automatic mastery score');
  });
  let practiceId,wrongId;
  await check('flashcard practice grades one answer at a time and preserves the whole-round denominator',async()=>{
    await startQuiz({count:5}); const seen=new Set();
    for (let index=0;index<5;index++) {
      const {activity}=await answersForCurrent(javaBundle,index===0 ? activity=>[activity.options.find(option=>!activity.answer_ids.includes(option.id)).id] : undefined);
      const displayed=await page.locator('#check-question .answer-option').evaluateAll(nodes=>nodes.map(node=>({id:node.querySelector('input').value,badge:node.querySelector('.option-id').textContent,text:node.querySelector('.option-text').textContent})));
      assert.deepEqual(displayed.map(option=>option.badge),displayed.map((_,position)=>String.fromCharCode(65+position)),'Visible option labels follow the current shuffled order');
      assert(!seen.has(activity.id)); seen.add(activity.id); if(index===0)wrongId=activity.id;
      assert.equal(await page.locator('#check-feedback').isVisible(),false,'Answers are hidden before submission');
      if(index===1)await page.locator('#check-guessed').check();
      await page.locator('#check-answer-submit').click(); await page.locator('#check-feedback').waitFor({state:'visible'});
      const feedback=await page.locator('#check-feedback').innerText(); assert(feedback.includes(activity.explanation));
      for(const id of activity.answer_ids) {
        const option=displayed.find(option=>option.id===id);
        assert(feedback.includes(option.badge+' · '+option.text),'Immediate feedback uses the current letter and the correct option text');
      }
      const record=(await api('/api/projects/'+javaId+'/checks')).checks.at(-1); practiceId=record.id;
      assert.equal(record.question_count,5); assert.equal(record.answered_count,index+1);
      if(index<4) {
        assert.equal(record.status,'active'); assert.equal(Object.hasOwn(record,'passed'),false,'One correct answer cannot pass an unfinished five-question round');
        await page.locator('#check-next').click(); await page.waitForFunction(id=>document.querySelector('#check-question').dataset.activityId!==id,activity.id);
      }
    }
    const record=(await api('/api/projects/'+javaId+'/checks')).checks.find(item=>item.id===practiceId);
    assert.equal(record.score,80); assert.equal(record.correct_count,4); assert.equal(record.passed,true); assert.equal(record.independent,false);
    assert.equal(record.results[1].guessed,true); assert.equal(record.results[1].needs_review,true);
    await page.locator('#check-next').click(); await page.locator('#check-result').waitFor({state:'visible'});
    const saved=await api('/api/projects/'+javaId+'/checks/'+practiceId);
    const historyPanel=page.locator('#check-history-panel');
    if(await historyPanel.getAttribute('open')===null)await historyPanel.locator('summary').first().click();
    await page.waitForFunction(()=>document.querySelector('#check-history .check-history-score')?.textContent.includes('4/5'));
    const historical=page.locator('#check-history .historical-question').first();
    const historyLoaded=page.waitForResponse(response=>response.request().method()==='GET' && response.url().endsWith('/checks/'+practiceId));
    await historical.locator('summary').first().click();assert.equal((await historyLoaded).status(),200);
    await page.waitForFunction(()=>document.querySelector('#check-history .historical-question')?.dataset.loaded==='true');
    const entries=historical.locator('.paper-section');assert.equal(await entries.count(),5);
    for(let index=0;index<saved.check.results.length;index++) {
      const result=saved.check.results[index],question=saved.questions.find(question=>question.id===result.activity_id),content=await entries.nth(index).innerText();
      assert(content.includes(question.prompt));assert(content.includes(result.explanation));
      for(const label of ['你的答案：','固定答案：'])assert(content.includes(label));
      for(const id of [...result.answer,...result.answer_ids]) {
        const position=question.options.findIndex(option=>option.id===id),option=question.options[position];
        assert(content.includes(String.fromCharCode(65+position)+' · '+option.text),'History explains the original shuffled answer in human-readable form');
      }
    }
  });
  await check('wrong-question practice uses its real pool and stops reusing corrected questions',async()=>{
    await startQuiz({scope:'wrong',count:10});
    assert.equal(await page.locator('#check-question').getAttribute('data-activity-id'),wrongId);
    await answersForCurrent(javaBundle); await page.locator('#check-answer-submit').click();
    await page.locator('#check-feedback').waitFor({state:'visible'});
    const record=(await api('/api/projects/'+javaId+'/checks')).checks.at(-1);
    assert.equal(record.question_count,1); assert.equal(record.score,100);
    const inventory=await api('/api/projects/'+javaId+'/checks/inventory'); assert.equal(inventory.wrong,0);
  });
  await check('an unfinished flashcard round resumes after reload without changing order or first answers',async()=>{
    await startQuiz({count:3}); const {activity}=await answersForCurrent(javaBundle);
    await page.locator('#check-guessed').check(); await page.locator('#check-answer-submit').click();
    await page.locator('#check-feedback').waitFor({state:'visible'});
    const record=(await api('/api/projects/'+javaId+'/checks')).checks.at(-1);
    const before=await api('/api/projects/'+javaId+'/checks/'+record.id);
    assert.equal(before.check.answered_count,1);assert.equal(before.check.status,'active');
    for(const question of before.questions)for(const hidden of ['answer_ids','explanation','hints'])assert.equal(Object.hasOwn(question,hidden),false);
    await page.reload({waitUntil:'networkidle'});await openView('check');
    await page.locator('#check-history-panel > summary').click();
    await page.locator('[data-resume-check="'+record.id+'"]').click();await page.locator('#check-question').waitFor({state:'visible'});
    assert.equal(await page.locator('#check-question').getAttribute('data-activity-id'),before.questions[1].id);
    const after=await api('/api/projects/'+javaId+'/checks/'+record.id);assert.deepEqual(after.questions,before.questions);
    assert.deepEqual(after.check.results[0],before.check.results[0]);assert.equal(after.check.results[0].activity_id,activity.id);
    for(let index=1;index<3;index++) {
      const {activity:next}=await answersForCurrent(javaBundle);await page.locator('#check-answer-submit').click();await page.locator('#check-feedback').waitFor({state:'visible'});
      if(index<2){await page.locator('#check-next').click();await page.waitForFunction(id=>document.querySelector('#check-question').dataset.activityId!==id,next.id);}
    }
    const finished=(await api('/api/projects/'+javaId+'/checks/'+record.id)).check;
    assert.equal(finished.status,'finished');assert.equal(finished.answered_count,3);assert.equal(finished.question_count,3);assert.equal(finished.score,100);
    assert.equal(finished.results[0].guessed,true);assert.equal(finished.independent,false);
  });
  await check('exam mode requires the complete answer set and passes four of five',async()=>{
    await startQuiz({mode:'exam',count:5});
    for(let index=0;index<5;index++) {
      const {activity}=await answersForCurrent(javaBundle,index===0 ? activity=>[activity.options.find(option=>!activity.answer_ids.includes(option.id)).id] : undefined);
      assert.equal(await page.locator('#check-feedback').isVisible(),false,'An exam does not show the solution before completion');
      if(index<4) { await page.locator('#check-next').click(); await page.waitForFunction(id=>document.querySelector('#check-question').dataset.activityId!==id,activity.id); }
    }
    await page.locator('#check-submit').click(); await page.locator('#check-result').waitFor({state:'visible'});
    const record=(await api('/api/projects/'+javaId+'/checks')).checks.at(-1);
    assert.equal(record.score,80); assert.equal(record.passed,true);
    const saved=await api('/api/projects/'+javaId+'/checks/'+record.id);
    for(let index=0;index<record.results.length;index++) {
      const result=record.results[index],question=saved.questions.find(question=>question.id===result.activity_id),details=page.locator('#check-result .check-feedback').nth(index);
      if(await details.getAttribute('open')===null)await details.locator('summary').click();
      const content=await details.innerText();assert(content.includes(question.prompt));assert(content.includes(result.explanation));
      for(const id of result.answer_ids) {
        const position=question.options.findIndex(option=>option.id===id);
        assert(content.includes(String.fromCharCode(65+position)+' · '+question.options[position].text),'Whole-round feedback uses the preserved shuffled letter and option text');
      }
    }
  });

  await check('multiple-choice practice rejects missing or extra selections and accepts the exact set',async()=>{
    const original=javaBundle.activities.find(activity=>activity.options?.length && activity.answer_ids?.length>=2 && activity.answer_ids.length<activity.options.length);
    assert(original,'Original source-bound example provides a meaningful multi-select');
    const fixture=structuredClone(javaBundle); fixture.title='QA exact selection'; fixture.project.name='Exact Set QA';
    fixture.activities=['qa-partial','qa-extra','qa-exact'].map(id=>({...structuredClone(original),id,title:id}));
    const preview=await api('/api/bundles/preview','POST',{bundle:fixture});
    const imported=await api('/api/bundles/'+preview.draft_id+'/commit','POST',{confirmed:true});
    await page.reload({waitUntil:'networkidle'}); await selectProject(imported.project.id,fixture.project.name);
    await startQuiz({count:3});
    const chosen={};
    for(let index=0;index<3;index++) {
      const {activity,answer}=await answersForCurrent(fixture,activity=>activity.id==='qa-partial' ? activity.answer_ids.slice(0,1) : activity.id==='qa-extra' ? [...activity.answer_ids,activity.options.find(option=>!activity.answer_ids.includes(option.id)).id] : activity.answer_ids);
      chosen[activity.id]=answer;
      await page.locator('#check-answer-submit').click();await page.locator('#check-feedback').waitFor({state:'visible'});
      if(index<2){await page.locator('#check-next').click();await page.waitForFunction(id=>document.querySelector('#check-question').dataset.activityId!==id,activity.id);}
    }
    const record=(await api('/api/projects/'+imported.project.id+'/checks')).checks.at(-1);
    for(const result of record.results)assert.equal(result.correct,result.activity_id==='qa-exact');
    assert.equal(record.score,33.33);assert.equal(record.passed,false);
    const first=record.results[0],attemptsBefore=(await api('/api/projects/'+imported.project.id)).summary.attempt_count;
    const duplicate=await api('/api/projects/'+imported.project.id+'/checks/'+record.id+'/answer','POST',{activity_id:first.activity_id,answer:fixture.activities.find(activity=>activity.id===first.activity_id).answer_ids});
    assert.equal(duplicate.already_answered,true);assert.deepEqual(duplicate.result,first);
    assert.deepEqual(duplicate.result.answer.slice().sort(),chosen[first.activity_id].slice().sort(),'Selection is an exact set independent of shuffled DOM order');
    assert.equal((await api('/api/projects/'+imported.project.id)).summary.attempt_count,attemptsBefore);
    await selectProject(javaId,javaBundle.project.name);
  });

  let backup;
  await check('backup roundtrip preserves explanations, intervals, quiz first answers and guessed review',async()=>{
    backup=await api('/api/backup'); await api('/api/restore','POST',{confirmed:true,backup});
    const after=await api('/api/backup'); assert.deepEqual(after.data,backup.data);
    const review=await api('/api/projects/'+javaId+'/reviews'); assert.equal(review.history[0].id,reviewId); assert.equal(review.interval_days,7);
    const check=(await api('/api/projects/'+javaId+'/checks')).checks.find(item=>item.id===practiceId); assert.equal(check.results[1].guessed,true);
  });
  await check('source version changes keep old self-reports and practice as historical evidence',async()=>{
    await api('/api/projects/'+javaId+'/materials','POST',{files:[{path:'extra-review.md',text:'A new revision requires a new explanation, not deletion of the first answer.\n'}]});
    const reviews=await api('/api/projects/'+javaId+'/reviews'); assert.equal(reviews.history[0].stale,true); assert.equal(reviews.due,true); assert.equal(reviews.interval_days,7);
    const checks=(await api('/api/projects/'+javaId+'/checks')).checks; assert(checks.every(item=>item.stale));
    const actual=await api('/api/projects/'+javaId); assert(actual.summary.stale_attempt_count>0);
    await api('/api/restore','POST',{confirmed:true,backup}); await page.reload({waitUntil:'networkidle'});
  });

  const pythonBundle=JSON.parse(await fs.readFile(path.join(ROOT,'examples','python-notes','learning-bundle.json'),'utf8'));
  let pythonId;
  await check('a non-Java learning bundle uses the same four-stage application without scanner guesses',async()=>{
    const preview=await api('/api/bundles/preview','POST',{bundle:pythonBundle});
    const imported=await api('/api/bundles/'+preview.draft_id+'/commit','POST',{confirmed:true}); pythonId=imported.project.id;
    await page.reload({waitUntil:'networkidle'}); await selectProject(pythonId,pythonBundle.project.name);
  });
  await verifySummary(pythonBundle,pythonId,'Python');
  await verifyFlows(pythonBundle,pythonId,'Python');

  await check('320px, 390px and 1440px keep all four stages readable and contained',async()=>{
    for(const width of [320,390,1440]) {
      await page.setViewportSize({width,height:width<600?844:1000});
      for(const view of ['overview','flow','reflection','check']) {
        await openView(view);
        if(view==='flow')await page.locator('#flow-list [data-flow-id]').first().click();
        const dimensions=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
        assert(dimensions.document<=dimensions.viewport+1 && dimensions.body<=dimensions.viewport+1, width+'px '+view+' has no document horizontal overflow');
        for(const selector of ['.view-nav [data-view]','#manage-open',...(view==='flow'?['#flow-list [data-flow-id]','#trace-case-select']:view==='reflection'?['#review-save']:view==='check'?['#check-start']:[])]) {
          for(const node of await page.locator(selector).all())if(await node.isVisible()) {const box=await node.boundingBox();assert(box.height>=43.5,selector+' remains at least44px high');}
        }
      }
      await openView('overview'); await page.screenshot({path:path.join(OUTPUT,'overview-'+width+'.png'),fullPage:true});
      await openView('flow'); await page.screenshot({path:path.join(OUTPUT,'flow-'+width+'.png'),fullPage:true});
    }
  });
  await check('keyboard navigation reaches the learning stages with visible focus',async()=>{
    await page.setViewportSize({width:1440,height:1000}); await openView('overview');
    await page.locator('.view-nav [data-view="overview"]').focus();
    await page.keyboard.press('Tab');
    assert.equal(await page.evaluate(()=>document.activeElement.dataset.view),'flow');
    const style=await page.evaluate(()=>{const s=getComputedStyle(document.activeElement);return {outlineStyle:s.outlineStyle,outlineWidth:s.outlineWidth,shadow:s.boxShadow};});
    assert(style.outlineStyle!=='none' && parseFloat(style.outlineWidth)>0 || style.shadow!=='none','Focused controls have a visible indicator');
    await page.keyboard.press('Enter'); await page.locator('#flow-view').waitFor({state:'visible'});
  });
  if(process.env.STUDY_QA_PRIVATE_BACKUP)await verifyPrivateSnapshot();
  await check('the complete learning journey produces no page errors or external requests',async()=>{
    assert.deepEqual(pageErrors,[]);assert.deepEqual(externalRequests,[]);
    assert.deepEqual(await sourceHashes(),baselineHashes,'QA does not change application sources or teaching content');
  });
}
async function verifyPrivateSnapshot() {
  const filename=path.resolve(process.env.STUDY_QA_PRIVATE_BACKUP);
  const bytes=await fs.readFile(filename), originalHash=crypto.createHash('sha256').update(bytes).digest('hex');
  privateInputs.backup={name:path.basename(filename),sha256:originalHash};
  const original=JSON.parse(bytes.toString('utf8')), preserve=await api('/api/backup');
  let pid;
  const immutableTables=['attempts','hints','evidence','checks','files','concepts'];
  await check('a private legacy backup is restored only into the owned temporary database',async()=>{
    await api('/api/restore','POST',{confirmed:true,backup:original});
    const actual=await api('/api/backup');pid=actual.data.projects[0].id;
    for(const table of immutableTables)assert.deepEqual(actual.data[table],original.data[table] || [],'Original private '+table+' retained');
    await page.reload({waitUntil:'networkidle'});await selectProject(pid,actual.data.projects[0].name);
    assert.equal(await page.locator('#overview-view').isVisible(),true);
    assert.equal(crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex'),originalHash,'Original backup bytes remain unchanged');
  });
  if(process.env.STUDY_QA_PRIVATE_GUIDE) {
    await check('updated private teaching replaces coarse guides while preserving every first answer and hint',async()=>{
      const guideFilename=path.resolve(process.env.STUDY_QA_PRIVATE_GUIDE),guideBytes=await fs.readFile(guideFilename);
      const guideHash=crypto.createHash('sha256').update(guideBytes).digest('hex');
      privateInputs.guide={name:path.basename(guideFilename),sha256:guideHash};
      const pack=JSON.parse(guideBytes.toString('utf8'));
      const before=await api('/api/projects/'+pid), preview=await api('/api/projects/'+pid+'/packs/preview','POST',{pack});
      await api('/api/projects/'+pid+'/packs/'+preview.draft_id+'/commit','POST',{confirmed:true});
      const after=await api('/api/projects/'+pid), actual=await api('/api/backup');
      assert.equal(after.project.version,before.project.version);assert.equal(after.project.source_hash,before.project.source_hash);
      for(const table of immutableTables)assert.deepEqual(actual.data[table],original.data[table] || [],'Teaching upgrade preserves '+table);
      const currentActivities=new Map(actual.data.activities.map(row=>[row.project_id+':'+row.data.id,row]));
      for(const row of original.data.activities)assert.deepEqual(currentActivities.get(row.project_id+':'+row.data.id),row,'Original private learning activity remains available');
      const objective=actual.data.activities.filter(row=>row.project_id===pid && row.data.options?.length && row.data.answer_ids?.length);
      const inventory=await api('/api/projects/'+pid+'/checks/inventory');
      assert.equal(inventory.all,objective.length,'The visible objective pool matches the actual source-bound activities');
      assert(inventory.all>=11,'All eleven original source-bound objective questions survive');
      assert.equal(new Set(after.flows.map(flow=>flow.id)).size,after.flows.length);
      const expectedIds=[...new Set([...before.flows.map(flow=>flow.id),...(pack.flows || []).map(flow=>flow.id)])];
      assert.deepEqual(after.flows.map(flow=>flow.id).slice().sort(),expectedIds.sort(),'Revisions retain stable feature identities');
      assert.equal(after.project_summary.purpose,pack.project_summary.purpose);
      const updated=(pack.flow_updates || []).map(update=>typeof update==='string'?update:update.id);
      if(updated.length)for(const id of updated)assert(after.flows.some(flow=>flow.id===id && flow.steps.every(step=>step.methods?.length)));
      await page.reload({waitUntil:'networkidle'});await selectProject(pid,after.project.name);await openView('overview');
      assert((await page.locator('#project-overview').innerText()).includes(pack.project_summary.learning_value));
      await page.screenshot({path:path.join(OUTPUT,'private-overview-desktop.png'),fullPage:true});
      await openView('flow');
      assert.equal(await page.locator('#flow-list [data-flow-id]').count(),after.flows.length,'All current features are navigable once');
      for(const flow of after.flows) {
        await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
        const signatures=flow.steps.flatMap(step=>(step.methods || []).map(method=>method.signature));
        assert(signatures.length>0,'Private feature includes method-level teaching');
        assert.deepEqual(await page.locator('#flow-steps .method-signature').allTextContents(),signatures);
        for(const trace of flow.trace_cases) {
          await page.locator('#trace-case-select').selectOption(trace.id);
          await page.locator('#trace-expand-all').click();
          const events=await page.locator('#trace-timeline .trace-event').evaluateAll(nodes=>nodes.map(node=>({step:node.getAttribute('data-step-id'),method:node.getAttribute('data-method-id'),text:node.innerText})));
          assert.equal(events.length,trace.frames.length);
          for(let index=0;index<trace.frames.length;index++) {
            const frame=trace.frames[index],event=events[index];
            assert.equal(event.step,frame.step_id);assert.equal(event.method,frame.method_id || null);
            assert(event.text.includes(frame.explanation),'Every private call/return explains its actual position');
            for(const value of frame.values)for(const field of ['name','type','value','source'])assert(event.text.includes(value[field]),'Private parameter '+field+' remains visible in order');
          }
          const skipped=await page.locator('#flow-steps .step-not-reached').evaluateAll(nodes=>nodes.map(node=>node.getAttribute('data-step-id')));
          assert.deepEqual(skipped.sort(),(trace.skipped_step_ids || []).slice().sort(),'Only explicit private downstream stops are labelled not called');
          assert((await page.locator('#trace-outcome').innerText()).includes(trace.result));
        }
      }
      await page.locator('#flow-list [data-flow-id]').first().click();
      await page.screenshot({path:path.join(OUTPUT,'private-flow-desktop.png'),fullPage:false});
      for(const width of [320,390,1440]) {
        await page.setViewportSize({width,height:width<600?844:1000});
        for(const view of ['overview','flow','reflection','check']) {
          await openView(view);
          const dimensions=await page.evaluate(()=>({viewport:document.documentElement.clientWidth,document:document.documentElement.scrollWidth,body:document.body.scrollWidth}));
          assert(dimensions.document<=dimensions.viewport+1 && dimensions.body<=dimensions.viewport+1,width+'px private '+view+' has no horizontal overflow');
          if(width===390 && ['reflection','check'].includes(view)) {
            await page.evaluate(()=>window.scrollTo(0,0));
            await page.screenshot({path:path.join(OUTPUT,'private-'+view+'-390.png'),fullPage:true});
          }
        }
        if(width===390) {
          await openView('overview');await page.screenshot({path:path.join(OUTPUT,'private-overview-390.png'),fullPage:true});
          await openView('flow');
          const orders=after.flows.find(flow=>flow.id==='guide-my-orders');
          if(orders) {
            await page.locator('#flow-list [data-flow-id="'+orders.id+'"]').click();
            const rejected=orders.trace_cases.find(trace=>trace.id==='blocked') || orders.trace_cases.find(trace=>trace.skipped_step_ids?.length);
            if(rejected)await page.locator('#trace-case-select').selectOption(rejected.id);
          }
          await page.evaluate(()=>window.scrollTo(0,0));
          await page.screenshot({path:path.join(OUTPUT,'private-flow-390-top.png'),fullPage:false});
          await page.locator('#trace-jump').click();
          await page.screenshot({path:path.join(OUTPUT,'private-flow-390-trace.png'),fullPage:false});
        }
      }
      assert.equal(crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex'),originalHash);
      assert.equal(crypto.createHash('sha256').update(await fs.readFile(guideFilename)).digest('hex'),guideHash,'The verified private guide input remains unchanged');
    });
  }
  await api('/api/restore','POST',{confirmed:true,backup:preserve});
}

(async()=>{
  let failure;
  try { await start(); await runJourney(); }
  catch (error) { failure=error; process.stderr.write(error.stack+'\n'); }
  finally {
    let unchanged=false;
    try { unchanged=JSON.stringify(await sourceHashes()) === JSON.stringify(baselineHashes); } catch (_) {}
    const report={status:failure?'FAILED':'OK',verifiedAt:new Date().toISOString(),testsRun:results.length,passed:results.filter(item=>item.passed).length,failures:results.filter(item=>!item.passed).length,errors:failure && !results.some(item=>!item.passed)?1:0,skipped:0,results,pageErrors,externalRequests,injectedRequestsBlocked,sourceUnchanged:unchanged,sourceHashes:baselineHashes || {},privateInputs};
    await fs.mkdir(OUTPUT,{recursive:true}); await fs.writeFile(path.join(OUTPUT,'results.json'),JSON.stringify(report,null,2));
    await stop();
    if (failure) process.exitCode=1;
  }
})();
