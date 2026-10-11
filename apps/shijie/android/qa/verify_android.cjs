'use strict';
/* Actual APK acceptance; intentionally refuses to target any physical phone. */
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),assert=require('node:assert/strict');
const {execFileSync}=require('node:child_process');
let playwright;try{playwright=require('playwright');}catch{playwright=require('../../.qa/node_modules/playwright');}
const {_android}=playwright,root=path.resolve(__dirname,'..'),workspace=path.resolve(root,'../..');
const serial=process.env.VERIFY_ANDROID_SERIAL,flavor=process.env.SHIJIE_QA_FLAVOR||'personal';
assert.equal(serial,'emulator-5582','Use only this app’s disposable, owned emulator-5582.');
assert(['sample','personal'].includes(flavor));
const pkg='io.shijie.learning',version='0.4.1';
const adb=process.env.ANDROID_ADB||path.join(workspace,'mobile/tools/android-sdk/platform-tools/adb.exe');
const output=path.join(root,'build',flavor,'native-verification');fs.mkdirSync(output,{recursive:true});
const debug=JSON.parse(fs.readFileSync(path.join(root,'build',flavor,'debug/apk-verification.json'),'utf8'));
const release=JSON.parse(fs.readFileSync(path.join(root,'dist',`shijie-${version}-${flavor}-verification.json`),'utf8'));
const debugAPK=path.join(root,'build',flavor,'debug',debug.apk),releaseAPK=path.join(root,'dist',release.apk);
const sha=file=>crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const command=args=>execFileSync(adb,['-s',serial,...args],{encoding:'utf8',windowsHide:true,maxBuffer:64*1024*1024});
const checks=[],errors=[],externalRequests=[],measures=[];let device,page,failure,beforeUpgrade,savedName;
const pass=message=>{checks.push(message);process.stdout.write('PASS '+message+'\n');};
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const quote=value=>"'"+String(value).replace(/'/g,"'\\''")+"'";
function installedHash(){const file=command(['shell','pm','path',pkg]).trim().replace(/^package:/,'');assert(/^\/data\/app\/[A-Za-z0-9_./+=~-]+\/base\.apk$/.test(file));return command(['shell','sha256sum',file]).trim().split(/\s+/)[0];}
async function attach(){const webview=await device.webView({pkg},{timeout:30000});page=await webview.page();page.setDefaultTimeout(20000);page.on('pageerror',error=>errors.push(error.message));page.on('request',r=>{const u=new URL(r.url());if(u.origin!=='https://appassets.androidplatform.net'&&!['data:','blob:'].includes(u.protocol))externalRequests.push(u.origin+u.pathname);});await page.waitForFunction(()=>window.ShijieOffline?.appStore&&document.querySelector('#workspace')&&!document.querySelector('#workspace').hidden);}
async function freshAttach(){device=(await _android.devices()).find(d=>d.serial()===serial);assert(device);await attach();}
async function api(url,method='GET',body){return page.evaluate(async({url,method,body})=>(await window.ShijieOffline.appStore).request(url,method,body),{url,method,body});}
async function view(name){await page.locator(`.view-nav [data-view="${name}"]`).click();await page.locator(`#${name}-view`).waitFor({state:'visible'});}
function nodes(xml){return [...xml.matchAll(/<node\b([^>]*)>/g)].map(match=>Object.fromEntries([...match[1].matchAll(/([\w-]+)="([^"]*)"/g)].map(m=>[m[1],m[2]])));}
async function tree(){return nodes(command(['shell','uiautomator','dump','/sdcard/shijie-qa-ui.xml'])&&command(['shell','cat','/sdcard/shijie-qa-ui.xml']));}
async function tapNode(predicate,label){for(let i=0;i<12;i++){const node=(await tree()).find(predicate);if(node){const n=node.bounds.match(/\d+/g).map(Number);command(['shell','input','tap',String(Math.round((n[0]+n[2])/2)),String(Math.round((n[1]+n[3])/2))]);return node;}await delay(150);}throw Error('Native file picker control unavailable: '+label);}
async function downloads(){const current=await tree();if(current.some(n=>n.text==='Downloads'&&n['resource-id']?.endsWith('/toolbar')))return;const menu=current.find(n=>/Show roots|Open navigation drawer/i.test(n['content-desc']||''));if(menu){const n=menu.bounds.match(/\d+/g).map(Number);command(['shell','input','tap',String((n[0]+n[2])/2),String((n[1]+n[3])/2)]);await tapNode(n=>n.text==='Downloads','Downloads');}}
async function apiBackup(){return api('/api/backup');}

(async()=>{try{
 assert.equal(sha(debugAPK),debug.sha256);assert.equal(sha(releaseAPK),release.sha256);
 assert.deepEqual(debug.assetSHA256,release.assetSHA256);assert.equal(debug.dexSHA256,release.dexSHA256);assert.equal(debug.signerCertificateSHA256,release.signerCertificateSHA256);
 assert(debug.assetSHA256['assets/site/legal/LICENSE']);assert(debug.assetSHA256['assets/site/legal/NOTICE']);
 pass('Debug 和 release 的页面、种子与 DEX 字节一致，签名身份一致；验收绑定实际二进制 SHA256');
 assert.equal(installedHash(),debug.sha256,'Install the current debug APK before running this script');
 device=(await _android.devices()).find(d=>d.serial()===serial);assert(device);
 command(['shell','svc','wifi','disable']);command(['shell','svc','data','disable']);
 command(['shell','am','start','-n',pkg+'/.MainActivity']);await attach();
 assert.equal(await page.evaluate(()=>window.ShijieNative.getVersion()),version);
 const baseline=await apiBackup(),pid=baseline.data.projects[0].id,initialChecks=new Set(baseline.data.checks.map(row=>row.id||row.data.id));
 const objectives=baseline.data.activities.filter(row=>row.project_id===pid&&row.data.options?.length&&row.data.answer_ids?.length);
 assert(objectives.length>=1);assert.equal((await api(`/api/projects/${pid}/checks/inventory`)).all,objectives.length);
 const project=await api('/api/projects/'+pid);assert(project.flows.length);assert(project.concepts.length);assert(project.project_summary.purpose);
 pass(`真正关闭网络后，APK 读取 ${project.flows.length} 条功能链、${project.concepts.length} 个知识点和 ${objectives.length} 道客观题`);
 assert.equal(await page.locator('.view-nav button').count(),4);await page.screenshot({path:path.join(output,'overview.png'),fullPage:false});
 pass('手机保留看懂项目、拆解功能、我的理解、复习验收四个入口');
 await view('flow');assert(await page.locator('.method-node').count());
 const first=await page.locator('#trace-position').innerText();if(await page.locator('#trace-next').isEnabled()){await page.locator('#trace-next').click();assert.notEqual(await page.locator('#trace-position').innerText(),first);}
 assert((await page.locator('#trace-frame').innerText()).length>20);assert((await page.locator('#trace-outcome').innerText()).length>10);
 await page.screenshot({path:path.join(output,'call-chain.png'),fullPage:false});pass('真实 WebView 展示方法调用链、输入与参数传递，并可点击逐步追踪');
 await view('reflection');const interval=(await api(`/api/projects/${pid}/reviews`)).interval_days;
 await page.locator('#review-interval').fill(String(interval===3?4:3));await page.locator('#review-settings button').click();
 await page.waitForFunction(()=>!document.querySelector('#review-settings button').disabled);
 assert.notEqual((await api(`/api/projects/${pid}/reviews`)).interval_days,interval);pass('真实触控修改定期回顾间隔，提交后离线记录持久化');
 await view('check');await page.locator('#check-mode').selectOption('practice');await page.locator('#check-count').fill('3');await page.locator('#check-start').click();await page.waitForFunction(()=>!document.querySelector('#check-start').disabled);await page.locator('#check-question:not([hidden])').waitFor();
 let aid=await page.locator('#check-question').getAttribute('data-activity-id');const truth=new Map(objectives.map(row=>[row.data.id,row.data]));
 for(const id of truth.get(aid).answer_ids)await page.locator(`#check-question input[name="check-answer"][value="${id}"]`).check();
 await page.locator('#check-answer-submit').click();await page.waitForFunction(()=>document.querySelector('#check-next')&&!document.querySelector('#check-next').disabled);
 const active=(await api(`/api/projects/${pid}/checks`)).checks.find(c=>!initialChecks.has(c.id)&&c.status==='active'&&c.mode==='practice');assert(active);
 const detail=await api(`/api/projects/${pid}/checks/${active.id}`);assert.equal(detail.check.results[0].correct,true);assert.equal(detail.check.answered_count,1);assert.equal(detail.check.results[0].explanation,truth.get(aid).explanation);
 assert((await page.locator('#check-question').innerText()).includes(truth.get(aid).explanation.slice(0,25)));
 pass('真实触控作答，固定答案精确判分、逐项解析与已答数量正确');
 await page.locator('#check-next').click();const pending=await page.locator('#check-question').getAttribute('data-activity-id');
 const layout=await page.evaluate(()=>({width:innerWidth,overflow:document.documentElement.scrollWidth>innerWidth+1,nav:[...document.querySelectorAll('.view-nav button')].map(b=>({height:b.getBoundingClientRect().height,font:parseFloat(getComputedStyle(b).fontSize)})),choices:[...document.querySelectorAll('#check-question .answer-option')].map(b=>({height:b.getBoundingClientRect().height,font:parseFloat(getComputedStyle(b.querySelector('.option-text')).fontSize)}))}));measures.push(layout);assert.equal(layout.overflow,false);assert(layout.nav.length===4&&layout.nav.every(n=>n.height>=44));assert(layout.choices.length>1&&layout.choices.every(n=>n.height>=44&&n.font>=16));
 await page.screenshot({path:path.join(output,'question.png'),fullPage:false});pass('实际 Android 尺寸下无横向溢出，导航/答案触控区域至少 44px，答案字至少 16px');
 const savedBeforeRestart=await apiBackup();await device.close();device=null;command(['shell','am','force-stop',pkg]);command(['shell','am','start','-n',pkg+'/.MainActivity']);await freshAttach();assert.deepEqual((await apiBackup()).data,savedBeforeRestart.data);
 await view('check');await page.locator('#check-history-panel').evaluate(node=>node.open=true);await page.locator(`[data-resume-check="${active.id}"]`).click();await page.locator('#check-question:not([hidden])').waitFor();assert.equal(await page.locator('#check-question').getAttribute('data-activity-id'),pending);
 pass('真正 force-stop 并重新打开后，首答、回顾设置、原场次和未完成题目全部保留');
 await page.locator('#manage-open').click();await page.locator('#manage-dialog[open]').waitFor();
 await page.locator('#manage-dialog details').filter({hasText:'版本与许可'}).locator('summary').click();
 await page.getByRole('button',{name:'查看许可与版权说明'}).click();await page.locator('#source-dialog[open]').waitFor();
 const legal=await page.locator('#source-content').innerText();assert(legal.includes('Apache License')&&legal.includes('Version 2.0')&&legal.includes('Original application code')&&legal.includes('Copyright 2026'));
 await page.locator('#source-dialog [data-close="source-dialog"]').click();
 await page.evaluate(()=>{window.__exports=[];window.addEventListener('shijie-native-export',event=>window.__exports.push(event.detail));});
 await page.locator('#backup-download').click();await downloads();await tapNode(n=>/^SAVE$/i.test(n.text||''),'Save');
 await page.waitForFunction(()=>window.__exports.some(e=>e.ok));savedName=await page.evaluate(()=>window.__exports.find(e=>e.ok).name);assert(savedName.endsWith('.json'));
 const exported=JSON.parse(command(['shell','cat '+quote('/sdcard/Download/'+savedName)]));assert.deepEqual(exported.data,(await apiBackup()).data);pass('真正 ACTION_CREATE_DOCUMENT 写出系统所选 JSON 备份，落盘内容与学习记录一致');
 await api(`/api/projects/${pid}/reviews/settings`,'POST',{interval_days:9});assert.equal((await api(`/api/projects/${pid}/reviews`)).interval_days,9);
 await page.locator('label:has(#restore-file)').click();await downloads();await tapNode(n=>n.text===savedName,'Saved backup');
 const pickerTree=await tree();if(pickerTree.some(n=>/^SELECT|^OPEN$/i.test(n.text||'')))await tapNode(n=>/^SELECT|^OPEN$/i.test(n.text||''),'Select');
 await page.locator('#restore-confirm').waitFor({state:'visible'});await page.locator('#restore-confirm').click();await page.locator('#confirm-dialog[open]').waitFor();await page.locator('#confirm-ok').click();await page.locator('#manage-dialog').waitFor({state:'hidden'});assert.deepEqual((await apiBackup()).data,exported.data);
 pass('真正 ACTION_OPEN_DOCUMENT 选择已导出备份，预览确认恢复并完整核对记录');
 assert.equal(errors.length,0,errors.join('\n'));assert.equal(externalRequests.length,0,externalRequests.join('\n'));
 assert(!command(['shell','dumpsys','package',pkg]).includes('android.permission.INTERNET'));pass('运行无外部资源请求/脚本异常，真实安装应用没有 INTERNET 权限');
 beforeUpgrade=await apiBackup();await device.close();device=null;command(['shell','am','force-stop',pkg]);command(['install','-r',releaseAPK]);assert.equal(installedHash(),release.sha256);command(['shell','am','start','-n',pkg+'/.MainActivity']);
 // Release deliberately cannot be remotely inspected. Upgrade back to the byte-identical
 // debug code only to read the persisted state after exercising the actual release APK.
 await delay(1000);
 let releaseReady=false;for(let i=0;i<8;i++){const visible=(await tree()).map(n=>n.text||'').join(' ');if(visible.includes('看懂项目')&&visible.includes('复习验收')){releaseReady=true;break;}await delay(200);}
 assert(releaseReady,'Actual installed release APK must show the four-stage UI after launch');
 command(['shell','am','force-stop',pkg]);command(['install','-r',debugAPK]);command(['shell','am','start','-n',pkg+'/.MainActivity']);await freshAttach();assert.deepEqual((await apiBackup()).data,beforeUpgrade.data);
 pass('真实 release 覆盖安装并离线启动，随后同代码调试包读取验证：升级未清空学习记录');
}catch(error){failure={message:error.message,stack:error.stack};console.error(error);try{if(page)await page.screenshot({path:path.join(output,'failure.png'),fullPage:false});}catch{}}
finally{if(device)await device.close();const report={status:failure?'FAILED':'OK',testsRun:checks.length+(failure?1:0),passed:checks.length,failures:failure?1:0,errors:0,skipped:0,checks,measures,pageErrors:errors,externalRequests,failure,debugAPK_SHA256:debug.sha256,releaseAPK_SHA256:release.sha256,sameCodeAndAssets:true,signerCertificateSHA256:release.signerCertificateSHA256,flavor,scope:'真实 Android 15 AOSP 模拟器及系统文档选择器。正式 release 被实际安装启动；交互在与其 assets/DEX 相同的 debug APK 验收。不代表已在用户手机实测。'};fs.writeFileSync(path.join(output,'verification.json'),JSON.stringify(report,null,2));if(failure)process.exitCode=1;}})();
