'use strict';
/* Development-only UI checks. Uses isolated SQLite and Edge; always stops its server. */
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const readline = require('node:readline');
const {spawn} = require('node:child_process');
const crypto = require('node:crypto');
let playwright;
try { playwright = require('playwright'); } catch (_) { playwright = require('../.qa/node_modules/playwright'); }
const ROOT = path.resolve(__dirname, '..');
const OUTPUT = path.join(ROOT, 'output', 'browser');
const EDGE = process.env.STUDY_QA_BROWSER || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe';
let temporary, server, browser, page, base, token;
const results = [], pageErrors = [];
let externalRequests = 0;
const externalOrigins = [];
let environmentInjectionBlocked = 0;
let sourceHashes = {};

async function appHashes() {
  const entries = [];
  async function collect(baseDir) {
    for (const item of await fs.readdir(baseDir, {withFileTypes: true})) {
      if (item.name === '__pycache__') continue;
      const filename = path.join(baseDir, item.name);
      if (item.isDirectory()) await collect(filename);
      else entries.push([path.relative(ROOT, filename).split(path.sep).join('/'), crypto.createHash('sha256').update(await fs.readFile(filename)).digest('hex')]);
    }
  }
  for (const dir of ['src', 'web', 'prompts', 'examples']) await collect(path.join(ROOT, dir));
  return Object.fromEntries(entries.sort((a, b) => a[0].localeCompare(b[0])));
}

async function check(name, task) {
  await task(); results.push({name, passed: true}); process.stdout.write('PASS ' + name + '\n');
}
async function api(route, method = 'GET', body, expected = 200) {
  const response = await fetch(base + route, {method, headers: {Origin: base, 'X-Study-Token': token || '', 'Content-Type': 'application/json'}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
  const value = await response.json();
  assert.equal(response.status, expected, 'API status for ' + route + ': ' + JSON.stringify(value));
  return value;
}
async function start() {
  sourceHashes = await appHashes();
  temporary = await fs.mkdtemp(path.join(os.tmpdir(), 'project-study-qa-'));
  const command = process.env.STUDY_QA_PYTHON || (process.platform === 'win32' ? 'py' : 'python3');
  const args = [...(process.platform === 'win32' && !process.env.STUDY_QA_PYTHON ? ['-3.12'] : []), path.join(ROOT, 'scripts/qa_server.py'), '--data-dir', temporary];
  server = spawn(command, args, {cwd: ROOT, windowsHide: true, stdio: ['pipe', 'pipe', 'pipe'], env: {...process.env, PYTHONIOENCODING: 'utf-8'}});
  let errors = ''; server.stderr.on('data', chunk => { errors += chunk; });
  const lines = readline.createInterface({input: server.stdout});
  const ready = await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(Error('Disposable server startup timeout')), 15000);
    lines.once('line', line => { clearTimeout(timer); try { resolve(JSON.parse(line)); } catch (_) { reject(Error('Invalid server readiness response')); } });
    server.once('error', err => { clearTimeout(timer); reject(err); });
    server.once('exit', () => { clearTimeout(timer); reject(Error('Disposable server exited: ' + errors)); });
  }); lines.close(); base = 'http://127.0.0.1:' + ready.port;
  token = (await api('/api/bootstrap')).token;
  browser = await playwright.chromium.launch({executablePath: EDGE, headless: true});
  page = await browser.newPage({viewport: {width: 1440, height: 1000}, deviceScaleFactor: 1});
  page.on('pageerror', err => pageErrors.push(err.message));
  await page.route('**/*', async route => {
    const url = new URL(route.request().url());
    // Existing local antivirus injects this exact host into HTTP pages. Keep the
    // block inside this disposable browser; every other external URL still fails.
    if (url.hostname === 'me.kis.v2.scr.kaspersky-labs.com') { environmentInjectionBlocked++; await route.abort(); return; }
    if (url.origin !== base) { externalRequests++; externalOrigins.push(url.origin + ' ' + url.protocol); await route.abort(); } else await route.continue();
  });
  await fs.mkdir(OUTPUT, {recursive: true});
}
async function projectId() {
  return page.locator('#project-select').inputValue();
}
async function chooseActivity(activity) {
  await page.locator('[data-view="practice"]').click();
  await page.locator('#practice-kind-filter').selectOption('');
  await page.locator('#practice-concept-filter').selectOption('');
  await page.locator('#activity-list').getByRole('button').filter({hasText: activity.title}).first().click();
  await page.locator('#answer-form').waitFor();
}

async function verifyFlowTeaching(bundle, pid, label) {
  const flow = bundle.flows[0];
  await check(label + ': meaningful scene and ordered chain precede source', async () => {
    await page.locator('[data-view="flow"]').click();
    await page.locator('#flow-list [data-flow-id="' + flow.id + '"]').click();
    assert.equal(await page.locator('#flow-steps [data-step-id]').count(), flow.steps.length);
    const content = await page.locator('#flow-detail').innerText();
    assert(content.includes(flow.title) && content.includes(flow.scenario) && content.includes(flow.input) && content.includes(flow.expected));
    assert.equal(await page.locator('#flow-detail details[open]').count(), 0);
    assert.equal(await page.locator('#source-dialog').isVisible(), false);
    assert((await page.locator('#project-overview').innerText()).includes(flow.title));
  });
  await check(label + ': step teaching links to knowledge and returns to same step', async () => {
    const step = flow.steps[1];
    await page.locator('#flow-steps [data-step-id="' + step.id + '"]').click();
    const content = await page.locator('#flow-step-detail').innerText();
    for (const value of [step.title,step.actor,step.input,step.output,step.action,step.why]) assert(content.includes(value));
    const id = step.concept_ids[0], concept = bundle.concepts.find(item => item.id === id);
    await page.locator('#flow-step-detail [data-flow-concept-id="' + id + '"]').click();
    assert((await page.locator('#concept-detail').innerText()).includes(concept.title));
    assert((await page.locator('#concept-detail').innerText()).includes(concept.lesson.slice(0,30)));
    assert(await page.locator('#flow-back').isVisible());
    await page.locator('#flow-back').click();
    assert.equal(await page.locator('#flow-steps [data-step-id="' + step.id + '"]').getAttribute('aria-pressed'), 'true');
    assert((await page.locator('#flow-step-detail').innerText()).includes(step.why));
  });
  await check(label + ': concrete parameter values change with actual registered frames', async () => {
    const example = flow.trace_cases[0];
    await page.locator('#trace-case-select').selectOption(example.id);
    for (let i = 0; i < example.frames.length; i++) {
      const frame = example.frames[i];
      assert((await page.locator('#trace-frame').innerText()).includes(frame.explanation));
      const content = await page.locator('#trace-values').innerText();
      for (const value of frame.values) for (const field of ['name','type','value','source']) assert(content.includes(value[field]), 'Missing trace field ' + field);
      assert.equal(await page.locator('#flow-steps [data-step-id="' + frame.step_id + '"]').getAttribute('aria-pressed'), 'true');
      if (i < example.frames.length-1) await page.locator('#trace-next').click();
    }
    assert(await page.locator('#trace-next').isDisabled());
    assert((await page.locator('#trace-outcome').innerText()).includes(example.result));
    const failing = flow.trace_cases.find(item => /失败|拒绝|重复|非法|空|未登录/.test(item.title));
    if (failing) {
      await page.locator('#trace-case-select').selectOption(failing.id);
      for (let i=1; i<failing.frames.length; i++) await page.locator('#trace-next').click();
      assert((await page.locator('#trace-outcome').innerText()).includes(failing.result));
    }
    assert.equal((await api('/api/projects/' + pid)).summary.attempt_count, 0, 'Reading a chain must not fabricate learner attempts.');
  });
}

async function verifyGenericWorkflow() {
  const bundleFile = path.join(ROOT, 'examples', 'python-notes', 'learning-bundle.json');
  const bundle = JSON.parse(await fs.readFile(bundleFile, 'utf8'));
  let genericId, wrongId, firstCheckId;
  await check('onboarding prompt and explicit research authorization', async () => {
    await page.locator('#manage-open').click();
    await page.locator('#onboarding-prompt').waitFor({state: 'attached'});
    const prompt = await api('/api/coach-prompt?mode=onboarding');
    assert(prompt.prompt.length > 400);
    assert(!(await page.locator('#allow-research').isChecked()));
    assert((await page.locator('#onboarding-prompt').textContent()).includes('allow_research=false'));
    await page.locator('#allow-research').check();
    assert((await page.locator('#onboarding-prompt').textContent()).includes('allow_research=true'));
    await page.locator('#allow-research').uncheck();
  });
  await check('non-Java complete learning bundle preview and confirmed import', async () => {
    const before = (await api('/api/bootstrap')).projects.length;
    await page.locator('#bundle-file').setInputFiles(bundleFile);
    await page.locator('#bundle-commit').waitFor({state: 'visible'});
    assert.equal((await api('/api/bootstrap')).projects.length, before);
    assert((await page.locator('#bundle-preview').innerText()).includes(bundle.concepts[0].title));
    assert((await page.locator('#bundle-preview').innerText()).includes(bundle.flows[0].title));
    await page.locator('#bundle-commit').click();
    if (await page.locator('#confirm-dialog').isVisible()) await page.locator('#confirm-ok').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('完整学习包已导入'));
    genericId = await projectId();
    const actual = await api('/api/projects/' + genericId);
    assert.equal(actual.project.name, bundle.project.name);
    assert.equal(actual.concepts.length, bundle.concepts.length);
    assert.equal(actual.activities.length, bundle.activities.length);
    assert.equal(actual.summary.attempt_count, 0);
    assert.deepEqual(actual.flows, bundle.flows);
  });
  await verifyFlowTeaching(bundle, genericId, 'Python project guide');
  await check('flow-only supplement previews and preserves knowledge and activities', async () => {
    const before = await api('/api/projects/' + genericId);
    const flow = structuredClone(bundle.flows[0]); flow.id = 'qa-extra-flow'; flow.title = '补充场景：任务参数边界';
    const pack = {schema_version:2,project_version:before.project.version,source_hash:before.project.source_hash,title:'只补充导读',concepts:[],activities:[],flows:[flow]};
    const filename = path.join(temporary,'flow-only-pack.json'); await fs.writeFile(filename,JSON.stringify(pack));
    await page.locator('[data-view="handoff"]').click(); await page.locator('#pack-file').setInputFiles(filename);
    await page.locator('#pack-commit').waitFor({state:'visible'});
    assert((await page.locator('#pack-preview').innerText()).includes(flow.title));
    assert.equal((await api('/api/projects/' + genericId)).flows.length,before.flows.length);
    await page.locator('#pack-commit').click(); if (await page.locator('#confirm-dialog').isVisible()) await page.locator('#confirm-ok').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('写入'));
    const after = await api('/api/projects/' + genericId);
    assert.equal(after.flows.length,before.flows.length+1); assert.deepEqual(after.concepts,before.concepts); assert.deepEqual(after.activities,before.activities);
  });
  await check('arbitrary concepts, detailed lesson and learning sources', async () => {
    await page.locator('[data-view="knowledge"]').click();
    const concept = bundle.concepts[0];
    await page.locator('#group-nav button').filter({hasText:concept.group}).first().click();
    await page.locator('#concept-list [data-concept-id="' + concept.id + '"]').click();
    assert((await page.locator('#concept-detail').innerText()).includes(concept.title));
    assert((await page.locator('#concept-detail').innerText()).includes(concept.mechanism));
    const content = await page.locator('#concept-detail').innerText();
    assert(concept.failures.every(failure => content.includes(failure)));
    const links = page.locator('#concept-detail .concept-practice-list button');
    assert(await links.count() <= 5);
    const labels = await links.allTextContents(); assert.equal(new Set(labels).size, labels.length);
    assert(concept.lesson && (await page.locator('#concept-detail').innerText()).includes(concept.lesson.slice(0, 30)));
    const detail = await api('/api/projects/' + genericId);
    assert(detail.materials.length >= 4);
  });
  await check('random check: four of five is 80 percent and passes', async () => {
    await page.locator('[data-view="check"]').click();
    await page.locator('#check-count').fill('5');
    await page.locator('#check-concept-filter').selectOption('');
    await page.locator('#check-scope').selectOption('all');
    await page.locator('#check-start').click();
    await page.locator('#check-question').waitFor({state: 'visible'});
    const seen = new Set();
    for (let index = 0; index < 5; index++) {
      const aid = await page.locator('#check-question').getAttribute('data-activity-id');
      assert(!seen.has(aid)); seen.add(aid);
      const task = bundle.activities.find(activity => activity.id === aid); assert(task);
      const chosen = index === 0 ? [task.options.find(option => !task.answer_ids.includes(option.id)).id] : task.answer_ids;
      if (index === 0) wrongId = aid;
      for (const id of chosen) await page.locator('#check-question input[name="check-answer"][value="' + id + '"]').check();
      if (index < 4) {
        await page.locator('#check-next').click();
        await page.waitForFunction(previous => document.querySelector('#check-question').dataset.activityId !== previous, aid);
      }
    }
    await page.locator('#check-submit').click();
    await page.locator('#check-result').waitFor({state: 'visible'});
    const history = await api('/api/projects/' + genericId + '/checks');
    const finished = history.checks.find(item => item.status === 'finished');
    assert(finished); firstCheckId = finished.id;
    assert.equal(finished.score, 80); assert.equal(finished.correct_count, 4); assert.equal(finished.question_count, 5); assert.equal(finished.passed, true);
    assert((await page.locator('#check-result').innerText()).includes('本次检测通过'));
  });
  await check('wrong-question check uses the actual one-question pool', async () => {
    await page.locator('#check-scope').selectOption('wrong'); await page.locator('#check-count').fill('10');
    await page.locator('#check-start').click();
    await page.locator('#check-question').waitFor({state: 'visible'});
    assert.equal(await page.locator('#check-question').getAttribute('data-activity-id'), wrongId);
    const task = bundle.activities.find(activity => activity.id === wrongId);
    for (const id of task.answer_ids) await page.locator('#check-question input[name="check-answer"][value="' + id + '"]').check();
    await page.locator('#check-submit').click(); await page.locator('#check-result').waitFor({state: 'visible'});
    const history = await api('/api/projects/' + genericId + '/checks');
    const correction = history.checks.find(item => item.id !== firstCheckId && item.status === 'finished');
    assert.equal(correction.question_count, 1); assert.equal(correction.score, 100); assert.equal(correction.independent, false);
  });
  await check('backup restores generic lessons and finished check records', async () => {
    const backup = await api('/api/backup');
    await api('/api/restore', 'POST', {confirmed: true, backup});
    const history = await api('/api/projects/' + genericId + '/checks');
    assert.equal(history.checks.find(item => item.id === firstCheckId).score, 80);
    assert.equal((await api('/api/projects/' + genericId)).concepts.length, bundle.concepts.length);
    assert.equal((await api('/api/projects/' + genericId)).flows.length, bundle.flows.length + 1);
  });
  await check('uploaded materials preserve old answers with version boundaries', async () => {
    const filename = path.join(temporary, 'additional.md');
    await fs.writeFile(filename, 'A prepared task is not yet independently mastered.\n', 'utf8');
    await page.locator('#manage-open').click(); await page.locator('#materials-file').setInputFiles(filename);
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('资料已更新'));
    const detail = await api('/api/projects/' + genericId);
    assert.equal(detail.project.version, 2); assert(detail.summary.stale_attempt_count >= 6);
    assert(detail.materials.some(source => source.path === 'materials/additional.md'));
    assert.equal(detail.flows.length,0);
    assert((await api('/api/projects/' + genericId + '/checks')).checks.every(item => item.stale));
    if (await page.locator('#manage-dialog').isVisible()) await page.keyboard.press('Escape');
  });
  await check('Agent can write a new knowledge point after source changes', async () => {
    const detail = await api('/api/projects/' + genericId);
    const source = await api('/api/projects/' + genericId + '/source?path=materials%2Fadditional.md');
    const anchor = {path: source.path, line: 1, quote: source.text.split('\n')[0], sha256: source.sha256};
    const concept = {id: 'fresh-evidence-boundary', title: '实践证据的边界', group: '复核', explanation: '准备好代码和证明独立能力是不同的状态。', mechanism: '先记录实际操作，再检查帮助和验证范围。', why: '保留来源与独立性。', prerequisites: [], failures: ['把代码存在当成已掌握。'], lesson: '先保存第一次回答，再进行实际操作和延迟复测。', evidence: [anchor], references: []};
    const activity = {id: 'fresh-evidence-check', concept_id: concept.id, kind: 'predict', title: '区分准备与独立', prompt: '只有准备好的任务，是否足以证明独立掌握？', options: [{id: 'yes', text: '足以证明。'}, {id: 'no', text: '还需要实际独立操作和复核。'}], answer_ids: ['no'], rubric: ['区分准备与能力证据。'], explanation: '准备不等于独立完成。', hints: ['查看来源里 not yet 的含义。'], evidence: [anchor]};
    const pack = {schema_version: 2, project_version: detail.project.version, source_hash: detail.project.source_hash, title: '新增知识点', concepts: [concept], activities: [activity]};
    const filename = path.join(temporary, 'new-concept-pack.json'); await fs.writeFile(filename, JSON.stringify(pack));
    await page.locator('[data-view="handoff"]').click(); await page.locator('#pack-file').setInputFiles(filename);
    await page.locator('#pack-commit').waitFor({state: 'visible'}); assert((await page.locator('#pack-preview').innerText()).includes(concept.title));
    await page.locator('#pack-commit').click(); if (await page.locator('#confirm-dialog').isVisible()) await page.locator('#confirm-ok').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('写入'));
    const actual = await api('/api/projects/' + genericId);
    assert(actual.concepts.some(item => item.id === concept.id)); assert(actual.activities.some(item => item.id === activity.id));
    await page.locator('[data-view="knowledge"]').click();
  });
}

async function run() {
  await start(); let pid, snapshot, objective, subjective, first;
  if (process.env.STUDY_QA_EXISTING_BACKUP) {
    const backup = JSON.parse(await fs.readFile(process.env.STUDY_QA_EXISTING_BACKUP,'utf8'));
    await api('/api/restore','POST',{confirmed:true,backup});
    const selected = backup.data.projects[0].id;
    await page.goto(base,{waitUntil:'networkidle'});
    await page.locator('#project-select').selectOption(selected);
    await page.locator('[data-view="flow"]').click();
    const initial = await api('/api/projects/' + selected);
    await check('existing project opens scene teaching without modifying prior answers',async () => {
      assert(initial.flows.length >= 3); assert(initial.summary.attempt_count > 0);
      assert.equal(await page.locator('#source-dialog').isVisible(),false);
      await page.evaluate(() => window.scrollTo(0,0));
      await page.screenshot({path:path.join(OUTPUT,'existing-project-start.png'),fullPage:false});
    });
    for (let index=0; index<initial.flows.length; index++) {
      const flow=initial.flows[index];
      await check('existing project scene ' + (index+1) + ': steps, knowledge and registered parameter snapshots',async () => {
        await page.locator('#flow-list [data-flow-id="'+flow.id+'"]').click();
        assert.equal(await page.locator('#flow-steps [data-step-id]').count(),flow.steps.length);
        for (const step of flow.steps) {
          await page.locator('#flow-steps [data-step-id="'+step.id+'"]').click();
          const content = await page.locator('#flow-step-detail').innerText();
          for (const value of [step.title,step.actor,step.action,step.input,step.output,step.why]) assert(content.includes(value));
          assert(await page.locator('#flow-step-detail [data-flow-concept-id]').count() > 0);
        }
        const last=flow.steps.at(-1), id=last.concept_ids[0];
        await page.locator('#flow-step-detail [data-flow-concept-id="'+id+'"]').click();
        assert((await page.locator('#concept-detail').innerText()).includes(initial.concepts.find(c=>c.id===id).title));
        await page.locator('#flow-back').click();
        assert.equal(await page.locator('#flow-steps [data-step-id="'+last.id+'"]').getAttribute('aria-pressed'),'true');
        for (const example of flow.trace_cases) {
          await page.locator('#trace-case-select').selectOption(example.id);
          for (let i=0;i<example.frames.length;i++) {
            const frame=example.frames[i], content=await page.locator('#trace-values').innerText();
            assert((await page.locator('#trace-frame').innerText()).includes(frame.explanation));
            for (const value of frame.values) for (const field of ['name','type','value','source']) assert(content.includes(value[field]));
            if(i<example.frames.length-1) await page.locator('#trace-next').click();
          }
          assert((await page.locator('#trace-outcome').innerText()).includes(example.result));
        }
      });
    }
    await check('existing project 390px and 320px scenes remain readable',async () => {
      await page.locator('#flow-list [data-flow-id="'+initial.flows[0].id+'"]').click();
      for(const width of [390,320]) {
        await page.setViewportSize({width,height:844});
        assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth+1),false);
        await page.evaluate(() => window.scrollTo(0,0));
        await page.screenshot({path:path.join(OUTPUT,'existing-project-phone-'+width+'.png'),fullPage:false});
      }
    });
    await check('existing records and source remain unchanged through reading',async () => {
      const after=await api('/api/backup');
      for(const table of ['projects','files','attempts','hints','evidence','checks']) assert.deepEqual(after.data[table],backup.data[table],table+' changed by reading');
      assert.equal(pageErrors.length,0,pageErrors.join('\n')); assert.equal(externalRequests,0,externalOrigins.join(', '));
      assert.deepEqual(await appHashes(),sourceHashes);
    });
    return;
  }
  if (process.env.STUDY_QA_ONLY_MOBILE === '1') {
    if (process.env.STUDY_QA_PROJECT) await api('/api/projects/scan', 'POST', {path: process.env.STUDY_QA_PROJECT});
    else await api('/api/projects/demo', 'POST', {});
    await page.setViewportSize({width: 390, height: 844});
    await page.goto(base, {waitUntil: 'networkidle'});
    await page.waitForFunction(() => !!document.querySelector('#project-select').value);
    const diagnostic = await page.evaluate(() => ({viewport: innerWidth, scrollWidth: document.documentElement.scrollWidth, elements: [...document.querySelectorAll('body *')].map(el => ({tag: el.tagName, id: el.id, class: typeof el.className === 'string' ? el.className : '', right: Math.round(el.getBoundingClientRect().right), width: Math.round(el.getBoundingClientRect().width), minWidth: getComputedStyle(el).minWidth})).filter(el => el.right > innerWidth + 1).slice(0, 18)}));
    process.stdout.write(JSON.stringify(diagnostic, null, 2) + '\n');
    await check('focused 390px project knowledge layout', async () => {
      assert(diagnostic.scrollWidth <= diagnostic.viewport + 1, 'Mobile knowledge layout is wider than viewport');
    });
    await page.locator('[data-view="practice"]').click();
    await check('focused 390px practice layout', async () => {
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    });
    return;
  }
  await check('empty startup and original demo import', async () => {
    await page.goto(base, {waitUntil: 'networkidle'});
    assert(await page.locator('#welcome').isVisible());
    await page.locator('#demo-start').click();
    await page.waitForFunction(() => !!document.querySelector('#project-select').value);
    pid = await projectId();
    const detail = await api('/api/projects/' + pid);
    assert(detail.concepts.length >= 5 && detail.activities.length >= 10);
    assert(await page.locator('#workspace').isVisible());
    snapshot = await api('/api/backup');
    const activities = snapshot.data.activities.filter(row => row.project_id === pid).map(row => row.data);
    objective = activities.find(a => a.options.length && a.kind === 'predict');
    subjective = activities.find(a => !a.options.length && a.kind !== 'transfer');
    assert(objective && subjective);
    assert.equal(await page.locator('[data-view="flow"]').getAttribute('aria-current'),'page');
    assert(detail.flows.length > 0);
  });
  const javaBundle = JSON.parse(await fs.readFile(path.join(ROOT,'examples','java-demo','learning-bundle.json'),'utf8'));
  await verifyFlowTeaching(javaBundle,pid,'Java project guide');
  await check('knowledge groups and source provenance', async () => {
    await page.locator('[data-view="knowledge"]').click();
    assert(await page.locator('#group-nav button').count() <= 8);
    assert.equal(await page.locator('#concept-detail details.code-evidence[open]').count(),0);
    await page.locator('#concept-detail details.code-evidence > summary').click();
    assert((await page.locator('#concept-detail').innerText()).includes('只读源码快照'));
    const open = page.locator('#concept-detail .source-link').first();
    await open.click(); await page.locator('#source-dialog').waitFor({state: 'visible'});
    await page.waitForFunction(() => document.querySelector('#source-content').innerText.includes('package'));
    assert((await page.locator('#source-content').innerText()).length > 40);
    await page.keyboard.press('Escape'); assert(!(await page.locator('#source-dialog').isVisible()));
  });
  await check('graded hint and fixed-answer attempt', async () => {
    await chooseActivity(objective);
    assert(!(await page.locator('#activity-detail').innerText()).includes(objective.explanation));
    await page.locator('#hint-next').click();
    await page.waitForFunction(() => document.querySelector('#hint-next').dataset.level === '2');
    for (const id of objective.answer_ids) await page.locator('#answer-form input[name="answer"][value="' + id + '"]').check();
    await page.locator('#answer-submit').click(); await page.locator('#attempt-result').waitFor({state: 'visible'});
    const detail = await api('/api/projects/' + pid + '/activities/' + objective.id);
    first = detail.history[0]; assert.equal(first.score, 100); assert.equal(first.hint_level, 1);
    assert.equal(first.mastery_claim, '选择题分数不代表独立掌握');
  });
  await check('retries preserve original answer and history', async () => {
    const wrong = objective.options.find(o => !objective.answer_ids.includes(o.id)); assert(wrong);
    await api('/api/projects/' + pid + '/attempts', 'POST', {activity_id: objective.id, answer: [wrong.id], assistance: 'none'});
    const detail = await api('/api/projects/' + pid + '/activities/' + objective.id);
    assert.equal(detail.history.length, 2); assert.equal(detail.history[0].id, first.id); assert.equal(detail.history[1].score, 0);
  });
  await check('free response remains pending review', async () => {
    await chooseActivity(subjective);
    await page.locator('#answer-form textarea').fill('先跟踪变量类型，再区分参数来源与服务器身份。我尚未脱离提示独立复现，测试只作为待复核证据。');
    await page.locator('#answer-submit').click(); await page.locator('#attempt-result').waitFor({state: 'visible'});
    const detail = await api('/api/projects/' + pid + '/activities/' + subjective.id);
    assert.equal(detail.history[0].status, 'review_required'); assert.equal(detail.history[0].score, null);
    await page.locator('#evidence-section > summary').click();
    await page.locator('#evidence-form textarea[name="summary"]').fill('人工核对源码并复述分支，尚未运行Java。');
    await page.locator('#evidence-form select[name="test_type"]').selectOption('manual');
    await page.locator('#evidence-form textarea[name="scope"]').fill('未启动Spring/HTTP/数据库/模型。');
    await page.locator('#evidence-submit').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('证据已保存'));
    const saved = await api('/api/projects/' + pid + '/activities/' + subjective.id);
    const record = saved.evidence[0];
    assert.equal(record.status, 'review_required'); assert.equal(record.tests_run, 0);
    await api('/api/projects/' + pid + '/evidence', 'POST', {...record, tests_run: 1}, 400);
  });
  await check('manual Agent handoff contains bounded relative snapshots', async () => {
    await page.locator('[data-view="handoff"]').click();
    const handoff = await api('/api/projects/' + pid + '/handoff');
    assert(handoff.source_files.some(f => f.text && f.sha256));
    assert(!JSON.stringify(handoff.project).includes(ROOT));
    assert(handoff.attempts.some(a => a.id === first.id));
  });
  await check('Agent task preview, safe rendering and explicit commit', async () => {
    const activity = {...structuredClone(subjective), id: 'qa-agent-task', title: '核对源码 <img src=x onerror="window.studyInjected=1">', hints: ['只有一个提示，先核对题目中的源码依据。']};
    const pack = {schema_version: 1, project_version: 1, title: '隔离验收任务', activities: [activity]};
    const file = path.join(temporary, 'task-pack.json'); await fs.writeFile(file, JSON.stringify(pack));
    await page.locator('#pack-file').setInputFiles(file);
    await page.locator('#pack-commit').waitFor({state: 'visible'});
    assert.equal((await api('/api/projects/' + pid)).activities.some(a => a.id === activity.id), false);
    assert.equal(await page.evaluate(() => !!window.studyInjected), false);
    await page.locator('#pack-commit').click();
    if (await page.locator('#confirm-dialog').isVisible()) await page.locator('#confirm-ok').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('写入'));
    assert((await api('/api/projects/' + pid)).activities.some(a => a.id === activity.id));
    const bad = {...pack, activities: [{...activity, id: 'qa-invalid-anchor', evidence: [{...activity.evidence[0], sha256: '0'.repeat(64)}]}]};
    const before = (await api('/api/projects/' + pid)).activities.length;
    await api('/api/projects/' + pid + '/packs/preview', 'POST', {pack: bad}, 400);
    assert.equal((await api('/api/projects/' + pid)).activities.length, before);
    await chooseActivity(activity);
    await page.locator('#hint-next').click();
    await page.waitForFunction(() => !document.querySelector('#hint-next'));
    assert.equal((await api('/api/projects/' + pid + '/activities/' + activity.id)).hints_requested.length, 1);
  });
  await check('backup download and atomic restore', async () => {
    await page.locator('#manage-open').click();
    const [download] = await Promise.all([page.waitForEvent('download'), page.locator('#backup-download').click()]);
    const saved = path.join(temporary, 'saved.backup.json'); await download.saveAs(saved);
    const backup = JSON.parse(await fs.readFile(saved, 'utf8')); assert.equal(backup.app, 'project-study');
    await page.locator('#restore-file').setInputFiles(saved); await page.locator('#restore-confirm').click();
    if (await page.locator('#confirm-dialog').isVisible()) await page.locator('#confirm-ok').click();
    await page.waitForFunction(() => document.querySelector('#toast').textContent.includes('恢复'));
    assert.equal((await api('/api/projects/' + pid + '/activities/' + objective.id)).history[0].id, first.id);
    if (await page.locator('#manage-dialog').isVisible()) await page.keyboard.press('Escape');
  });
  if (process.env.STUDY_QA_PROJECT) await check('real selected Java project imported read only', async () => {
    await page.locator('#manage-open').click();
    await page.locator('#project-path').fill(process.env.STUDY_QA_PROJECT); await page.locator('#scan-submit').click();
    await page.waitForFunction(previous => document.querySelector('#project-select').value !== previous, pid);
    const actual = await api('/api/projects/' + await projectId());
    assert(actual.project.file_count >= 150 && actual.concepts.length >= 12);
    assert.equal(actual.summary.attempt_count, 0);
    assert(!(await page.locator('body').innerText()).includes(process.env.STUDY_QA_PROJECT));
    if (await page.locator('#manage-dialog').isVisible()) await page.keyboard.press('Escape');
    await page.locator('[data-view="flow"]').click();
    assert((await page.locator('#flow-detail').innerText()).includes('当前内容缺少项目导读'));
    assert.equal(await page.locator('#flow-steps').count(),0);
  });
  await verifyGenericWorkflow();
  await check('desktop screenshot and safe browser runtime', async () => {
    await page.locator('[data-view="knowledge"]').click();
    await page.screenshot({path: path.join(OUTPUT, 'desktop.png'), fullPage: true});
    assert.equal(pageErrors.length, 0, pageErrors.join('\n')); assert.equal(externalRequests, 0, externalOrigins.join(', '));
  });
  await check('phone viewport and readable controls', async () => {
    await page.setViewportSize({width: 390, height: 844});
    await page.screenshot({path: path.join(OUTPUT, 'mobile.png'), fullPage: true});
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1);
    assert.equal(overflow, false, 'Phone page overflows horizontally');
    await page.locator('[data-view="practice"]').click();
    await page.screenshot({path: path.join(OUTPUT, 'mobile-practice.png'), fullPage: true});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.locator('[data-view="check"]').click();
    await page.locator('#check-scope').selectOption('all');
    await page.locator('#check-count').fill('5');
    await page.locator('#check-start').click();
    await page.locator('#check-question').waitFor({state: 'visible'});
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
    await page.screenshot({path: path.join(OUTPUT, 'mobile-check.png'), fullPage: true});
    await page.locator('[data-view="handoff"]').click();
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1), false);
  });
  await check('390px and 320px project guide, trace controls and folded source', async () => {
    const imported = await api('/api/bundles/preview','POST',{bundle:javaBundle});
    const demo = await api('/api/bundles/' + imported.draft_id + '/commit','POST',{confirmed:true});
    await page.reload({waitUntil:'networkidle'});
    await page.locator('#project-select').selectOption(demo.project.id);
    await page.waitForFunction(id => document.querySelector('#flow-list [data-flow-id="'+id+'"]'),javaBundle.flows[0].id);
    await page.locator('[data-view="flow"]').click();
    for (const width of [390,320]) {
      await page.setViewportSize({width,height:844});
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth > innerWidth + 1),false,'Guide wider than phone at '+width);
      const sizes = await page.locator('#flow-list button, #flow-steps button, #trace-next, #trace-prev, [data-flow-concept-id]').evaluateAll(nodes => nodes.map(node => node.getBoundingClientRect().height));
      assert(sizes.every(height => height >= 44),'Phone guide controls smaller than 44px');
      await page.locator('#trace-next').click();
      assert((await page.locator('#trace-values').innerText()).length > 15);
      assert.equal(await page.locator('#flow-detail details[open]').count(),0);
      await page.screenshot({path:path.join(OUTPUT,'mobile-guide-'+width+'.png'),fullPage:true});
    }
    await page.setViewportSize({width:1440,height:1000});
    await page.screenshot({path:path.join(OUTPUT,'desktop-guide.png'),fullPage:true});
    await page.evaluate(() => window.scrollTo(0,0));
    await page.screenshot({path:path.join(OUTPUT,'desktop-guide-start.png'),fullPage:false});
  });
  await check('source version remained fixed during browser checks', async () => {
    assert.deepEqual(await appHashes(), sourceHashes);
  });
}

(async () => {
  try { await run(); }
  catch (error) { results.push({name: 'verification stopped', passed: false, error: error.message}); process.stderr.write(error.stack + '\n'); process.exitCode = 1;
    if (page) try { await page.screenshot({path: path.join(OUTPUT, 'failure.png'), fullPage: true}); } catch (_) {}
  }
  finally {
    if (browser) await browser.close();
    if (server) { server.stdin.end('stop\n'); await Promise.race([new Promise(resolve => server.once('exit', resolve)), new Promise(resolve => setTimeout(resolve, 5000))]); if (server.exitCode === null) server.kill(); }
    await fs.mkdir(OUTPUT, {recursive: true});
    const reportName = process.env.STUDY_QA_EXISTING_BACKUP ? 'existing-project-results.json' : 'results.json';
    await fs.writeFile(path.join(OUTPUT, reportName), JSON.stringify({date: new Date().toISOString(), results, pageErrors, externalRequests, externalOrigins, environmentInjectionBlocked, sourceHashes}, null, 2));
    if (temporary) { const resolved = await fs.realpath(temporary), tempRoot = await fs.realpath(os.tmpdir()); if (path.dirname(resolved) !== tempRoot || !path.basename(resolved).startsWith('project-study-qa-')) throw Error('Unsafe QA cleanup target'); await fs.rm(resolved, {recursive: true, force: true}); }
    process.stdout.write('Browser checks: ' + results.filter(r => r.passed).length + ' passed, ' + results.filter(r => !r.passed).length + ' failed.\n');
  }
})();
