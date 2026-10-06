/* Optional browser QA: npm install --no-save --package-lock=false playwright,
 * then node scripts/verify_browser.cjs.
 * The application itself does not depend on Node or Playwright. This runner uses
 * a disposable loopback server, an isolated browser and synthetic user data.
 * AI calls use an explicit FixtureProvider; no real model request is made.
 */
'use strict';
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const path=require('node:path');
const os=require('node:os');
const {spawn}=require('node:child_process');
const readline=require('node:readline');
const crypto=require('node:crypto');
const {chromium}=require('playwright');
const ROOT=path.resolve(__dirname,'..');
const OUTPUT=path.join(ROOT,'output','browser');
const SCREENSHOTS=path.join(ROOT,'docs','screenshots');
const results=[];
const demoAnswers=new Map();
let ownedServer=null,browser=null,context=null,page=null,temporary=null;
let url='',port=0,externalRequests=0;
let environmentInjectionBlocked=0;
const pageErrors=[];
let testedHashes={};

async function sourceHashes(){
  const entries=[];
  async function collect(base){for(const entry of await fs.readdir(base,{withFileTypes:true})){if(entry.name==='__pycache__')continue;const location=path.join(base,entry.name);if(entry.isDirectory())await collect(location);else if(entry.isFile())entries.push([path.relative(ROOT,location).split(path.sep).join('/'),crypto.createHash('sha256').update(await fs.readFile(location)).digest('hex')]);}}
  for(const directory of ['src','web','examples','prompts'])await collect(path.join(ROOT,directory));
  return Object.fromEntries(entries.sort((a,b)=>a[0].localeCompare(b[0])));
}
async function removeOwnedTemporary(directory,prefix){
  const resolved=await fs.realpath(directory),temporaryRoot=await fs.realpath(os.tmpdir());
  if(path.dirname(resolved)!==temporaryRoot||!path.basename(resolved).startsWith(prefix))throw Error('Temporary cleanup refused an unexpected target.');
  await fs.rm(resolved,{recursive:true,force:true});
}

function pythonCommand(args){
  if(process.env.PYTHON)return [process.env.PYTHON,args];
  return process.platform==='win32'?['py',['-3.12',...args]]:['python3',args];
}
async function python(args){
  const [command,parameters]=pythonCommand(args);
  return new Promise((resolve,reject)=>{
    const child=spawn(command,parameters,{cwd:ROOT,env:{...process.env,PYTHONIOENCODING:'utf-8'},windowsHide:true});
    let out='',error='';child.stdout.on('data',chunk=>out+=chunk);child.stderr.on('data',chunk=>error+=chunk);
    child.once('error',reject);child.once('exit',code=>code===0?resolve(out):reject(Error('Python helper failed; see isolated local QA output.')));
  });
}
async function startServer(provider='disabled',requestedPort=0,directory=temporary){
  const [command,args]=pythonCommand([path.join(ROOT,'tests','audit','browser_server.py'),'--data-dir',directory,'--port',String(requestedPort),'--provider',provider]);
  const child=spawn(command,args,{cwd:ROOT,env:{...process.env,PYTHONIOENCODING:'utf-8'},windowsHide:true,stdio:['pipe','pipe','pipe']});
  ownedServer=child;
  let stderr='';child.stderr.on('data',chunk=>stderr+=chunk);
  const lines=readline.createInterface({input:child.stdout});
  const ready=await new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>reject(Error('Disposable server did not become ready.')),20000);
    lines.once('line',line=>{clearTimeout(timer);try{resolve(JSON.parse(line));}catch(_){reject(Error('Invalid disposable-server readiness response.'));}});
    child.once('error',error=>{clearTimeout(timer);reject(error);});
    child.once('exit',code=>{clearTimeout(timer);reject(Error('Disposable server exited before readiness.'));});
  });
  lines.close();
  port=ready.port;url='http://127.0.0.1:'+port;
  return ready;
}
async function stopServer(){
  if(!ownedServer)return;
  const child=ownedServer;ownedServer=null;
  if(child.exitCode!==null)return;
  await new Promise(resolve=>{
    let finished=false;
    const done=()=>{if(!finished){finished=true;resolve();}};
    child.once('exit',done);child.stdin.end('stop\n');
    setTimeout(()=>{if(!finished){child.kill();done();}},6000).unref();
  });
}
async function test(name,run){
  const started=Date.now();
  try{await run();results.push({name,status:'PASS',duration_ms:Date.now()-started});console.log('PASS '+name);}
  catch(error){results.push({name,status:'FAIL',duration_ms:Date.now()-started,error:String(error.message).replaceAll(ROOT,'[project]').slice(0,900)});console.log('FAIL '+name);}
}
async function go(route='/index.html'){
  await page.goto(url+route,{waitUntil:'domcontentloaded'});
  if(route.startsWith('/interview'))await page.waitForFunction(()=>/道可用题目/.test(document.querySelector('#quizInventory')?.textContent||''));
  else if(route==='/'||route.startsWith('/plan'))await page.waitForFunction(()=>document.querySelector('#planTaskList')?.dataset.ready==='true');
  else await page.waitForFunction(()=>document.querySelector('#uploadCategory')?.options.length>0);
}
async function api(route,method='GET',body){
  return page.evaluate(async ({route,method,body})=>{
    const bootstrap=await fetch('/api/bootstrap').then(response=>response.json());
    const response=await fetch(route,{method,headers:{'X-Archive-Token':bootstrap.token,...(body===undefined?{}:{'Content-Type':'application/json'})},...(body===undefined?{}:{body:JSON.stringify(body)})});
    return {status:response.status,body:await response.json()};
  },{route,method,body});
}
async function catalog(){const result=await api('/api/quiz/catalog');assert.equal(result.status,200);return result.body;}
async function selectSource(sourceKey){
  const response=await api('/api/materials');const material=response.body.items.find(item=>item.source_key===sourceKey);assert.ok(material,'source exists');
  await page.locator(`[data-material-id="${material.id}"]`).click();await page.waitForFunction(id=>document.querySelector('#downloadMaterial')?.href.endsWith('/'+id+'/file'),material.id);
  return material;
}
async function startQuiz(mode='practice',topic='',count='10',scope='all'){
  await go('/interview.html');await page.locator(`[data-topic="${topic}"]`).click();await page.selectOption('#quizMode',mode);await page.selectOption('#quizCount',count);await page.selectOption('#quizScope',scope);
  const responsePromise=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/quiz/sessions');
  await page.click('#quizStart');const response=await responsePromise;assert.equal(response.status(),201);const session=await response.json();await page.locator('#quizRun').waitFor({state:'visible'});return session;
}
function answers(question){return demoAnswers.get(question.prompt)||['b'];}
async function answerUI(question,selected,guessed=false){
  await page.locator('#quizQuestionTitle').waitFor({state:'visible'});
  assert.equal(await page.locator('#quizPrompt').textContent(),question.prompt);
  for(const id of selected)await page.locator(`#quizOptions input[value="${id}"]`).check();
  if(guessed)await page.check('#quizGuessed');
  const responsePromise=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname.endsWith('/answer'));
  await page.click('#quizSubmit');const response=await responsePromise;assert.equal(response.status(),200);return response.json();
}
async function download(selector){
  const pending=page.waitForEvent('download');await page.click(selector);const file=await pending;assert.equal(await file.failure(),null);return fs.readFile(await file.path());
}
async function comfortableControls(selectors){
  const checks=await page.evaluate(selectors=>selectors.flatMap(selector=>[...document.querySelectorAll(selector)].filter(node=>node.getClientRects().length).map(node=>{
    const rect=node.getBoundingClientRect(),style=getComputedStyle(node);
    return {selector,height:rect.height,width:rect.width,font:parseFloat(style.fontSize)};
  })),selectors);
  assert.ok(checks.length,'visible controls were inspected');
  for(const check of checks){assert.ok(check.height>=43.5&&check.width>=43.5,`${check.selector} needs a 44px tap target (${check.width} x ${check.height})`);assert.ok(check.font>=15.5,`${check.selector} needs readable 16px text (${check.font})`);}
}
async function main(){
  await fs.mkdir(OUTPUT,{recursive:true});await fs.mkdir(SCREENSHOTS,{recursive:true});
  temporary=await fs.mkdtemp(path.join(os.tmpdir(),'codesprint-browser-'));
  testedHashes=await sourceHashes();
  const demo=JSON.parse(await fs.readFile(path.join(ROOT,'examples','questions-demo.json'),'utf8'));for(const q of demo)demoAnswers.set(q.prompt,q.answer);
  await startServer();
  browser=await chromium.launch({headless:true,...(process.env.BROWSER_EXECUTABLE?{executablePath:process.env.BROWSER_EXECUTABLE}:process.platform==='win32'?{channel:'msedge'}:{})});
  context=await browser.newContext({viewport:{width:1440,height:1000},acceptDownloads:true,reducedMotion:'no-preference'});
  // Some local security tools inject their own script into every HTTP response.
  // Isolate that known environment injection inside this disposable context;
  // do not change the system tool or suppress any project/external URL check.
  const environmentHost='me.kis.v2.scr.kaspersky-labs.com';
  await context.route('**/*',route=>new URL(route.request().url()).hostname===environmentHost?route.abort():route.continue());
  context.on('request',request=>{if(new URL(request.url()).hostname===environmentHost){environmentInjectionBlocked++;return;}if(!request.url().startsWith(url+'/')&&!request.url().startsWith('data:')&&!request.url().startsWith('blob:'))externalRequests++;});
  page=await context.newPage();page.setDefaultTimeout(15000);page.on('pageerror',error=>pageErrors.push(error.message));
  await go();
  await test('Empty installation has no inherited personal library',async()=>{assert.equal((await catalog()).total,0);assert.equal((await api('/api/materials')).body.items.length,0);await page.locator('#libraryEmpty').waitFor({state:'visible'});});
  await test('UI imports six original materials and eighteen source-bound examples',async()=>{await page.click('#demoImport');await page.waitForFunction(()=>document.querySelector('#materialCount')?.textContent==='6');assert.equal((await catalog()).total,18);assert.equal((await api('/api/materials')).body.items.length,6);});
  await test('Repeated demo import is idempotent',async()=>{const response=await api('/api/demo/import','POST',{confirmed:true});assert.equal(response.status,200);assert.equal(response.body.written,0);assert.equal((await catalog()).total,18);});
  await test('Missing model key disables generation only',async()=>{await page.click('#generateTab');assert.equal(await page.isDisabled('#generateButton'),true);assert.equal(await page.isDisabled('#uploadOpen'),false);assert.equal(await page.isDisabled('#backupButton'),false);const before=await catalog();const source=(await api('/api/materials')).body.items[0];const response=await api('/api/generation/drafts','POST',{material_id:source.id,count:1,topic:'java',keywords:['HashMap'],focus:['mechanism'],consent:true});assert.equal(response.status,502);assert.equal((await catalog()).total,before.total);});
  const original=Buffer.from('# 浏览器原创测试资料\n\n标签查找：local-audit-note。\n<script>window.__qa_pwned=true</script>\n<img src="https://example.org/unexpected" onerror="window.__qa_pwned=true">\n', 'utf8');
  let uploaded=null,backup=null,persistentSession=null;
  await test('Upload preserves UTF-8 text and treats HTML as inert text',async()=>{await page.click('#uploadOpen');await page.setInputFiles('#uploadFile',{name:'browser-fixture.md',mimeType:'text/markdown',buffer:original});await page.selectOption('#uploadCategory','其他资料');await page.fill('#uploadTags','local-audit-note, 原创测试');await page.check('#uploadRights');await page.click('#uploadSubmit');await page.locator('#uploadDialog').waitFor({state:'hidden'});await page.waitForFunction(()=>document.querySelector('#documentText')?.textContent.includes('local-audit-note'));assert.equal(await page.locator('#documentText').textContent(),original.toString());assert.equal(await page.evaluate(()=>Boolean(window.__qa_pwned)),false);uploaded=(await api('/api/materials?search=local-audit-note')).body.items[0];assert.ok(uploaded);assert.equal(externalRequests,0);});
  await test('Library search and category filtering find uploaded source',async()=>{await page.fill('#materialSearch','local-audit-note');await page.waitForFunction(()=>document.querySelector('#materialCount')?.textContent==='1');assert.equal(await page.locator('#materialList button').count(),1);await page.selectOption('#materialCategory','其他资料');assert.equal((await api('/api/materials?search=local-audit-note&category='+encodeURIComponent('其他资料'))).body.items.length,1);await page.fill('#materialSearch','');await page.selectOption('#materialCategory','');await page.waitForFunction(()=>document.querySelector('#materialCount')?.textContent==='7');});
  await test('Original file download matches the uploaded bytes',async()=>{assert.deepEqual(await download('#downloadMaterial'),original);});
  await test('Favorite survives browser reload',async()=>{await page.click('#favoriteButton');await page.waitForFunction(()=>document.querySelector('#favoriteButton')?.getAttribute('aria-pressed')==='true');await page.reload({waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('#favoriteButton')?.getAttribute('aria-pressed')==='true');assert.equal((await api('/api/materials/'+uploaded.id)).body.favorite,true);});
  await test('Trash pauses source questions and UI restores the source',async()=>{await selectSource('demo:java-concurrency');await page.click('#trashButton');await page.waitForFunction(()=>document.querySelector('#studioStatus')?.textContent.includes('回收站'));assert.equal((await catalog()).total,15);await page.waitForFunction(()=>document.querySelector('#materialCount')?.textContent==='1'&&document.querySelector('#trashToggle')?.getAttribute('aria-pressed')==='true');await page.locator('#materialList button').click();await page.waitForFunction(()=>document.querySelector('#trashButton')?.textContent==='恢复资料');await page.click('#trashButton');await page.waitForFunction(()=>document.querySelector('#studioStatus')?.textContent.includes('已恢复'));assert.equal((await catalog()).total,18);});
  await test('Domain random sampling is unique and options retain stable ids',async()=>{const a=await startQuiz('practice','concurrency');assert.equal(a.total,3);assert.equal(new Set(a.questions.map(q=>q.id)).size,3);assert.ok(a.questions.every(q=>q.topic==='concurrency'));assert.ok(a.questions.every(q=>[...q.options.map(o=>o.id)].sort().join('')==='abcd'));const response=await api('/api/quiz/sessions/'+a.id+'/finish','POST',{});assert.equal(response.status,200);const orders=new Set();for(let i=0;i<5;i++){const session=(await api('/api/quiz/sessions','POST',{mode:'practice',topic:'concurrency',count:3,scope:'all'})).body;orders.add(session.questions.map(q=>q.id).join(','));await api('/api/quiz/sessions/'+session.id+'/finish','POST',{});}assert.ok(orders.size>1,'random order is observed across real rounds');});
  await test('Single and multiple UI answers use exact grading and eighty percent threshold',async()=>{const session=await startQuiz();let current=session;for(let index=0;index<session.questions.length;index++){const q=session.questions[index];let selected=answers(q);if(index>=8)selected=[];if(selected.length){current=await answerUI(q,selected);}else{const pending=page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname.endsWith('/answer'));await page.click('#quizSkip');current=await(await pending).json();}await page.locator('#quizNext').waitFor({state:'visible'});await page.click('#quizNext');}assert.equal(current.result.correct,8);assert.equal(current.result.total,10);assert.equal(current.result.percent,80);assert.equal(current.result.passed,true);await page.locator('#quizResult').waitFor({state:'visible'});assert.equal(await page.locator('#quizScore').textContent(),'80%');const multiple=demo.find(q=>q.type==='multiple');const source=(await api('/api/materials')).body.items.find(x=>x.source_key===multiple.source_key);const partial=(await api('/api/quiz/sessions','POST',{mode:'practice',count:30,material:source.id})).body;for(const q of partial.questions){const chosen=q.type==='multiple'?answers(q).slice(0,1):answers(q);const result=await api('/api/quiz/sessions/'+partial.id+'/answer','POST',{question_id:q.id,selected:chosen});if(q.type==='multiple')assert.equal(result.body.questions.find(x=>x.id===q.id).feedback.correct,false);} });
  await test('Correct guesses still enter the weak-question review set',async()=>{const before=(await catalog()).stats.wrong;const session=await startQuiz('practice','spring');const q=session.questions[0];const current=await answerUI(q,answers(q),true);const feedback=current.questions[0].feedback;assert.equal(feedback.correct,true);assert.ok(feedback.due_at-Math.floor(Date.now()/1000)>=580&&feedback.due_at-Math.floor(Date.now()/1000)<=610);assert.ok((await catalog()).stats.wrong>=before);await page.locator('#quizVerdict').waitFor({state:'visible'});assert.match(await page.locator('#quizVerdict').textContent(),/复习/);persistentSession=current;});
  await test('A saved first answer cannot be rewritten',async()=>{const q=persistentSession.questions[0];const wrong=q.options.find(o=>!answers(q).includes(o.id)).id;const response=await api('/api/quiz/sessions/'+persistentSession.id+'/answer','POST',{question_id:q.id,selected:[wrong],guessed:true});assert.equal(response.status,400);const saved=(await api('/api/quiz/sessions/'+persistentSession.id)).body.questions[0];assert.deepEqual([...saved.response.selected].sort(),[...answers(q)].sort());});
  await test('Reload offers unfinished-round continuation',async()=>{await go('/interview.html');await page.locator('#quizResume').waitFor({state:'visible'});await page.click('#quizResume');await page.waitForFunction(()=>document.querySelector('#quizQuestionIndex')?.textContent.startsWith('2 / 3'));});
  await test('Stopping and restarting real SQLite server preserves the round and uploads',async()=>{const savedPort=port;await stopServer();await startServer('disabled',savedPort);await go('/interview.html');await page.locator('#quizResume').waitFor({state:'visible'});await page.click('#quizResume');await page.waitForFunction(()=>document.querySelector('#quizQuestionIndex')?.textContent.startsWith('2 / 3'));assert.deepEqual((await api('/api/quiz/sessions/'+persistentSession.id)).body.questions[0].response,persistentSession.questions[0].response);assert.equal((await api('/api/materials/'+uploaded.id)).body.favorite,true);});
  await test('Early finish counts unanswered questions against the fixed denominator',async()=>{const response=await api('/api/quiz/sessions/'+persistentSession.id+'/finish','POST',{});assert.equal(response.status,200);assert.equal(response.body.result.total,3);assert.equal(response.body.result.correct,1);assert.equal(response.body.result.passed,false);assert.equal(response.body.answered,3);});
  await test('Exam hides answers until completion',async()=>{const session=await startQuiz('exam','','10');const current=await answerUI(session.questions[0],answers(session.questions[0]));assert.equal(current.finished,false);assert.ok(current.questions.every(q=>!q.feedback));assert.equal(await page.isVisible('#quizFeedback'),false);await page.click('#quizEnd');await page.locator('#quizResult').waitFor({state:'visible'});assert.equal(await page.locator('#quizScore').textContent(),'10%');assert.equal(await page.locator('#quizResultReview details').count(),10);});
  await test('Backup is an actual downloadable ZIP with files and SQLite records',async()=>{await go('/index.html');backup=await download('#backupButton');assert.equal(backup.readUInt32LE(0),0x04034b50);const location=path.join(OUTPUT,'synthetic-backup.zip');await fs.writeFile(location,backup);const result=JSON.parse(await python(['-c','import json,sys,zipfile; z=zipfile.ZipFile(sys.argv[1]); d=json.loads(z.read("backup.json")); print(json.dumps({"files":len(z.namelist()),"schema":d["schema"],"materials":len(d["tables"]["materials"]),"questions":len(d["tables"]["questions"]),"sessions":len(d["tables"]["quiz_sessions"]),"original":z.read("blobs/"+sys.argv[2]).hex()}))',location,crypto.createHash('sha256').update(original).digest('hex')]));assert.ok(result.files>=8);assert.equal(result.schema,'codesprint-backup-v2');assert.equal(result.materials,7);assert.equal(result.questions,18);assert.ok(result.sessions>0);assert.equal(result.original,original.toString('hex'));});
  await test('Bad restore makes no partial changes and valid UI restore recovers backup',async()=>{const before=await catalog();await page.click('#restoreOpen');await page.setInputFiles('#restoreFile',{name:'invalid.zip',mimeType:'application/zip',buffer:Buffer.from('invalid')});await page.check('#restoreConsent');await page.click('#restoreSubmit');await page.waitForFunction(()=>document.querySelector('#restoreStatus')?.textContent.includes('无效')||document.querySelector('#restoreStatus')?.textContent.includes('校验')&&!document.querySelector('#restoreSubmit')?.disabled);assert.equal((await catalog()).total,before.total);await page.setInputFiles('#restoreFile',{name:'synthetic.zip',mimeType:'application/zip',buffer:backup});await page.click('#restoreSubmit');await page.locator('#restoreDialog').waitFor({state:'hidden'});assert.equal((await catalog()).total,before.total);assert.equal((await api('/api/materials/'+uploaded.id)).body.favorite,true);await page.locator(`[data-material-id="${uploaded.id}"]`).click();assert.deepEqual(await download('#downloadMaterial'),original);});
  await test('AI stub draft remains outside bank until explicit preview confirmation',async()=>{const oldPort=port;await stopServer();await startServer('fixture',oldPort);await go();await selectSource('demo:java-concurrency');await page.click('#generateTab');await page.fill('#generationKeywords','volatile');await page.fill('#generationCount','1');await page.selectOption('#generationTopic','concurrency');for(const value of ['scenario','boundary'])await page.locator(`input[name="focus"][value="${value}"]`).uncheck();assert.equal(await page.isDisabled('#generateButton'),false);const before=(await catalog()).total;await page.click('#generateButton');assert.equal(await page.isVisible('#draftPreview'),false);assert.equal((await catalog()).total,before);await page.check('#generationConsent');await page.click('#generateButton');await page.locator('#draftPreview').waitFor({state:'visible'});assert.equal((await catalog()).total,before);assert.equal(await page.isDisabled('#commitButton'),true);assert.match(await page.locator('#draftQuestions').textContent(),/引用原文/);await page.check('#commitConsent');await page.click('#commitButton');await page.waitForFunction(()=>document.querySelector('#draftStatus')?.textContent.includes('已写入'));assert.equal((await catalog()).total,before+1);assert.equal(await page.isDisabled('#commitButton'),true);});
  let planningTask=null;
  await test('Home opens the unified study plan without creating progress',async()=>{await go('/');assert.match(await page.title(),/拾知.*今日计划/);await page.locator('#planEmpty').waitFor({state:'visible'});assert.equal((await api('/api/planning/tasks')).body.total,0);assert.equal(await page.locator('.site-header nav a').count(),3);});
  await test('A user creates a dated task linked to real material and can read it',async()=>{const source=(await api('/api/materials')).body.items.find(item=>item.source_key==='demo:java-concurrency');await page.click('#planAddOpen');await page.fill('#taskTitle','理解 volatile 与原子性');await page.fill('#taskMinutes','45');await page.selectOption('#taskTopic','concurrency');await page.selectOption('#taskMaterial',source.id);await page.fill('#taskNotes','读完资料后，解释 counter++ 的三个步骤；再独立答题。<script>window.__plan_pwned=true</script>');await page.click('#taskSave');await page.locator('#taskDialog').waitFor({state:'hidden'});await page.locator('[data-task-id]').waitFor({state:'visible'});planningTask=(await api('/api/planning/tasks')).body.items.find(task=>task.title==='理解 volatile 与原子性');assert.ok(planningTask);assert.equal(planningTask.completed,false);assert.equal(await page.evaluate(()=>Boolean(window.__plan_pwned)),false);await page.locator(`[data-task-id="${planningTask.id}"] [data-task-read]`).click();await page.waitForFunction(()=>document.querySelector('#documentText')?.textContent.includes('volatile 可以'));assert.equal(new URL(page.url()).searchParams.get('task'),planningTask.id);assert.equal(new URL(await page.locator('#practiceMaterial').getAttribute('href'),page.url()).searchParams.get('task'),planningTask.id);});
  await test('Reading flows into a task-linked round and its real score does not complete the task',async()=>{await page.click('#practiceMaterial');await page.waitForFunction(()=>/道可用题目/.test(document.querySelector('#quizInventory')?.textContent||''));const pending=page.waitForResponse(response=>response.request().method()==='POST'&&new URL(response.url()).pathname==='/api/quiz/sessions');await page.click('#quizStart');const response=await pending;assert.equal(response.status(),201);const round=await response.json();assert.equal(round.task_id,planningTask.id);for(const q of round.questions){await answerUI(q,answers(q));await page.click('#quizNext');}await page.locator('#quizResult').waitFor({state:'visible'});await page.locator('.site-header nav a[href^="plan.html"]').click();await page.locator(`[data-task-id="${planningTask.id}"]`).waitFor({state:'visible'});const task=(await api('/api/planning/tasks/'+planningTask.id)).body;assert.equal(task.quiz.finished,1);assert.equal(task.quiz.percent,100);assert.equal(task.completed,false);assert.match(await page.locator(`[data-task-id="${planningTask.id}"]`).textContent(),/已交卷 1/);});
  await test('Task completion and reopening are explicit and survive reload',async()=>{let row=page.locator(`[data-task-id="${planningTask.id}"]`);await row.locator('[data-task-complete]').click();await page.waitForFunction(id=>document.querySelector(`[data-task-id="${id}"] [data-task-complete]`)?.getAttribute('aria-pressed')==='true',planningTask.id);await page.reload({waitUntil:'domcontentloaded'});row=page.locator(`[data-task-id="${planningTask.id}"]`);await row.waitFor({state:'visible'});assert.equal(await row.locator('[data-task-complete]').getAttribute('aria-pressed'),'true');await row.locator('[data-task-complete]').click();await page.waitForFunction(id=>document.querySelector(`[data-task-id="${id}"] [data-task-complete]`)?.getAttribute('aria-pressed')==='false',planningTask.id);assert.equal((await api('/api/planning/tasks/'+planningTask.id)).body.quiz.finished,1);});
  await test('Plan preview and confirmed merge preserve existing task progress',async()=>{const before=(await api('/api/planning/tasks')).body.total;await api('/api/planning/tasks/'+planningTask.id,'PATCH',{completed:true});const document={schema:'shizhi-plan-v1',tasks:[{id:planningTask.id,date:planningTask.date,title:'不能覆盖已有任务',topic:'concurrency',material_id:planningTask.material_id,estimated_minutes:1},{id:'qa-plan-next',date:planningTask.date,title:'从资料解释线程池拒绝策略',topic:'concurrency',material_id:planningTask.material_id,estimated_minutes:35,notes:'先阅读，再独立说明设计边界。'}]};await page.click('#planImportOpen');await page.fill('#planImportText',JSON.stringify(document));await page.click('#planImportPreview');await page.locator('#planImportPreviewPanel').waitFor({state:'visible'});assert.equal(await page.isDisabled('#planImportSubmit'),true);assert.equal((await api('/api/planning/tasks')).body.total,before);await page.check('#planImportConsent');await page.click('#planImportSubmit');await page.locator('#planImportDialog').waitFor({state:'hidden'});assert.equal((await api('/api/planning/tasks')).body.total,before+1);const originalTask=(await api('/api/planning/tasks/'+planningTask.id)).body;assert.equal(originalTask.title,planningTask.title);assert.equal(originalTask.completed,true);assert.equal(originalTask.quiz.finished,1);const retry=await api('/api/planning/import','POST',{...document,confirmed:true});assert.equal(retry.status,200);assert.equal((await api('/api/planning/tasks')).body.total,before+1);});
  await test('Complete backup and UI restore retain the plan and linked scores together',async()=>{await go('/index.html');const completeBackup=await download('#backupButton');await api('/api/planning/tasks/'+planningTask.id,'PATCH',{completed:false,notes:'本次临时变化'});await page.click('#restoreOpen');await page.setInputFiles('#restoreFile',{name:'plan-backup.zip',mimeType:'application/zip',buffer:completeBackup});await page.check('#restoreConsent');await page.click('#restoreSubmit');await page.locator('#restoreDialog').waitFor({state:'hidden'});const restored=(await api('/api/planning/tasks/'+planningTask.id)).body;assert.equal(restored.completed,true);assert.equal(restored.quiz.finished,1);assert.equal(restored.quiz.percent,100);assert.equal(restored.notes,planningTask.notes);assert.equal((await api('/api/planning/tasks')).body.total,2);});
  await test('First-use help is discoverable and copies only the selected prompt on explicit click',async()=>{await page.evaluate(()=>localStorage.removeItem('shizhi:guide-seen'));await go('/plan.html');assert.equal(await page.locator('#firstUseGuide').getAttribute('open'),'');await page.reload({waitUntil:'domcontentloaded'});assert.equal(await page.locator('#firstUseGuide').getAttribute('open'),null);const original=await page.evaluate(()=>{window.__qaClipboardDescriptor=Object.getOwnPropertyDescriptor(navigator,'clipboard');Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{window.__qaCopiedPrompt=text;}}});return Boolean(window.__qaCopiedPrompt);});assert.equal(original,false);await page.click('[data-help-open]');await page.locator('#helpDialog').waitFor({state:'visible'});await page.click('[data-prompt-kind="plan"]');assert.match(await page.locator('#helpPromptText').inputValue(),/shizhi-plan-v1/);assert.equal(await page.evaluate(()=>window.__qaCopiedPrompt),undefined);await page.click('#helpCopyPrompt');await page.waitForFunction(()=>document.querySelector('#helpCopyStatus')?.textContent.startsWith('已复制'));assert.equal(await page.evaluate(()=>window.__qaCopiedPrompt),await page.locator('#helpPromptText').inputValue());await page.locator('#helpDialog .dialog-close').click();await page.evaluate(()=>{if(window.__qaClipboardDescriptor)Object.defineProperty(navigator,'clipboard',window.__qaClipboardDescriptor);else delete navigator.clipboard;delete window.__qaClipboardDescriptor;delete window.__qaCopiedPrompt;});});
  await test('Four visual themes survive reload and navigation without changing records',async()=>{await go('/index.html');const before=(await catalog()).total;for(const theme of ['studio','paper','terminal','dusk']){await page.click('[data-theme-open]');await page.locator(`[data-theme-id="${theme}"]`).click();await page.locator('#themeDialog .button').click();assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme);await page.reload({waitUntil:'domcontentloaded'});await page.waitForFunction(()=>document.querySelector('#uploadCategory')?.options.length>0);assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme);await go('/interview.html');assert.equal(await page.evaluate(()=>document.documentElement.dataset.theme),theme);assert.equal((await catalog()).total,before);await go();}});
  await test('All themes fit plan, material and quiz pages at phone and desktop widths',async()=>{for(const theme of ['studio','paper','terminal','dusk']){await page.evaluate(theme=>CodeSprintThemes.apply(theme),theme);for(const width of [320,390,412,1440]){await page.setViewportSize({width,height:900});for(const route of ['/plan.html','/index.html','/interview.html']){await go(route);if(route.startsWith('/interview')){await page.click('#quizStart');await page.locator('#quizRun').waitFor({state:'visible'});}const dimensions=await page.evaluate(()=>({client:document.documentElement.clientWidth,scroll:document.documentElement.scrollWidth}));assert.ok(dimensions.scroll<=dimensions.client+1,`${theme} ${width} ${route} overflow`);}}}});
  await test('Phone navigation, filters and common actions have readable text and large tap targets',async()=>{for(const theme of ['studio','paper','terminal','dusk']){await page.evaluate(theme=>CodeSprintThemes.apply(theme),theme);await page.setViewportSize({width:390,height:844});await go('/plan.html');assert.equal(await page.locator('#planFilters').getAttribute('open'),null);const firstTitle=await page.locator('[data-task-id] h3').first().boundingBox();assert.ok(firstTitle&&firstTitle.y+firstTitle.height<=844,'first task title is visible in the initial phone viewport');await comfortableControls(['#planFilterToggle']);await page.click('#planFilterToggle');await comfortableControls(['.site-header nav a','.header-tools button','#planAddOpen','#planImportOpen','#planPrevDay','#planNextDay','#planToday','#planAllDates','#planStatusFilter','#planSearch','[data-task-complete]','.task-actions a','.task-actions button']);await page.click('#planFilterToggle');await page.click('#planAddOpen');await comfortableControls(['#taskTitle','#taskDate','#taskMinutes','#taskTopic','#taskMaterial','#taskSave','#taskDialog .dialog-close']);await page.locator('#taskDialog [data-task-close]').last().click();await go('/index.html');await comfortableControls(['.site-header nav a','#uploadOpen','#backupButton','#restoreOpen','#trashToggle','#materialSearch','#materialCategory','#readTab','#generateTab','#practiceMaterial','#downloadMaterial']);await go('/interview.html');await comfortableControls(['.site-header nav a','#quizTopics button','#quizMode','#quizScope','#quizCount','#quizStart']);await page.click('#quizStart');await page.locator('#quizRun').waitFor({state:'visible'});await comfortableControls(['#quizOptions label','#quizSubmit','#quizSkip','#quizEnd']);}});
  await test('Phone starts inside the question and shows all four choices',async()=>{await page.setViewportSize({width:390,height:844});await page.evaluate(()=>CodeSprintThemes.apply('studio'));await startQuiz('practice','concurrency');await page.waitForFunction(()=>{const choices=[...document.querySelectorAll('#quizOptions label')];return window.scrollY>0&&choices.length===4&&choices.every(choice=>{const box=choice.getBoundingClientRect();return box.top>=0&&box.bottom<=innerHeight;});});assert.equal(await page.locator('#quizOptions label').count(),4);});
  await test('No page exception or external resource request occurred',async()=>{assert.deepEqual(pageErrors,[]);assert.equal(externalRequests,0);});

  // Public screenshots are created with a second empty profile and fresh data.
  // They show only original examples, never synthetic uploads, private state,
  // local paths, browser chrome, model secrets, or upstream response text.
  await context.close();context=null;await stopServer();
  const screenshotData=await fs.mkdtemp(path.join(os.tmpdir(),'codesprint-screenshots-'));
  try{
    await startServer('disabled',0,screenshotData);context=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'no-preference'});await context.route('**/*',route=>new URL(route.request().url()).hostname===environmentHost?route.abort():route.continue());page=await context.newPage();await go();await page.click('#demoImport');await page.waitForFunction(()=>document.querySelector('#materialCount')?.textContent==='6');await selectSource('demo:java-concurrency');await page.evaluate(()=>{document.querySelector('#firstUseGuide').open=false;});
    for(const theme of ['studio','paper','terminal','dusk']){await page.evaluate(theme=>{CodeSprintThemes.apply(theme);window.scrollTo({top:0,behavior:'instant'});},theme);await page.screenshot({path:path.join(SCREENSHOTS,theme+'.png'),animations:'disabled'});}
    const originalSource=(await api('/api/materials')).body.items.find(item=>item.source_key==='demo:java-concurrency');const today=await page.evaluate(()=>{const d=new Date();return new Date(d.getTime()-d.getTimezoneOffset()*60000).toISOString().slice(0,10);});await api('/api/planning/tasks','POST',{title:'看懂 volatile 的并发边界',date:today,topic:'concurrency',material_id:originalSource.id,estimated_minutes:45,notes:'从资料跟踪 counter++ 的读取、加一与写回。自己画出两个线程的执行顺序，再做一轮练习。'});await api('/api/planning/tasks','POST',{title:'把设计理由说给自己听',date:today,topic:'java',estimated_minutes:25,notes:'闭卷解释一个知识点，写下还不确定的地方，留给下一次复习。'});await go('/plan.html');await page.evaluate(()=>CodeSprintThemes.apply('studio'));await page.locator('[data-task-id]').first().waitFor({state:'visible'});await page.screenshot({path:path.join(SCREENSHOTS,'plan.png'),animations:'disabled'});
    await page.setViewportSize({width:390,height:844});await go('/plan.html');await page.screenshot({path:path.join(OUTPUT,'mobile-plan.png'),fullPage:true,animations:'disabled'});await go('/index.html');await selectSource('demo:java-concurrency');await page.screenshot({path:path.join(OUTPUT,'mobile-material.png'),fullPage:true,animations:'disabled'});await go('/interview.html');await page.evaluate(()=>CodeSprintThemes.apply('studio'));await page.click('#quizStart');await page.locator('#quizRun').waitFor({state:'visible'});await page.screenshot({path:path.join(SCREENSHOTS,'mobile.png'),animations:'disabled'});
  }finally{await stopServer();await removeOwnedTemporary(screenshotData,'codesprint-screenshots-');}
  assert.deepEqual(await sourceHashes(),testedHashes,'application sources stayed frozen during this run');
}

(async()=>{
  try{await main();}
  catch(error){results.push({name:'Runner setup or dependent operation',status:'ERROR',error:String(error.message).replaceAll(ROOT,'[project]').slice(0,900)});}
  finally{
    await context?.close().catch(()=>{});await browser?.close().catch(()=>{});await stopServer();
    if(temporary)await removeOwnedTemporary(temporary,'codesprint-browser-');
    const passed=results.filter(result=>result.status==='PASS').length,failures=results.filter(result=>result.status==='FAIL').length,errors=results.filter(result=>result.status==='ERROR').length;
    const summary={status:failures||errors?'FAIL':'PASS',tests_run:results.length,passed,failures,errors,skipped:0,external_requests:externalRequests,environment_injection_blocked:environmentInjectionBlocked,real_model_requests:0,app_sha256:crypto.createHash('sha256').update(JSON.stringify(testedHashes)).digest('hex'),app_files:testedHashes,scope:'Real Edge browser + loopback HTTP + temporary SQLite; explicit AI FixtureProvider; known local-security-tool HTML injection blocked only in the isolated test context; not a deployed service or real-model validation.',tests:results};
    await fs.mkdir(OUTPUT,{recursive:true});await fs.writeFile(path.join(OUTPUT,'verification.json'),JSON.stringify(summary,null,2)+'\n');console.log(JSON.stringify({status:summary.status,tests_run:summary.tests_run,passed,failures,errors,skipped:0}));process.exitCode=failures||errors?1:0;
  }
})();
