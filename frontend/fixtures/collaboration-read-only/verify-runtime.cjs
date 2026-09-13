// Exercises the actual React summary hook and extracted WorkspaceShell projection,
// selected-mail CTA, open/read, lifecycle, apply, close and fence callbacks.
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const {chromium}=require(process.env.CUEVION_PLAYWRIGHT||'/Users/rutger/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({headless:true,executablePath:process.env.CUEVION_CHROME||'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome'});
 const context=await browser.newContext({viewport:{width:1280,height:900}});
 const page=await context.newPage(),errors=[],external=[],checks=[];
 page.on('pageerror',e=>{errors.push(e.message);console.error(e.message);});
 page.on('console',m=>{if(m.type()==='error')errors.push(m.text());});
 await context.route('**/*',r=>{if(new URL(r.request().url()).hostname!=='127.0.0.1'){external.push(r.request().url());return r.abort();}return r.continue();});
 const check=async(name,fn)=>{await fn();checks.push(name);console.log('PASS '+name);};
 const state=()=>page.evaluate(()=>fixture.state());
 const button=name=>page.getByRole('button',{name,exact:true});
 const tab=name=>page.getByRole('tab',{name,exact:true});
 const shot=name=>page.screenshot({path:path.join(__dirname,name+'.png'),animations:'disabled'});
 try{
  await page.goto('http://127.0.0.1:4179');
  await check('active selected mail hydrates through real hook and shows one Open Collaboration CTA',async()=>{await button('Open Collaboration').waitFor();assert.equal(await page.locator('[data-open-collaboration]').count(),1);assert.equal((await state()).active,'needs_review');});
  await shot('active-source');
  await check('selected-mail CTA opens exact canonical collaborationId through actual open/read handlers',async()=>{await button('Open Collaboration').click();await tab('Settings').waitFor();await page.waitForFunction(()=>fixture.canonicalId()==='C'.repeat(22));assert.equal(await page.evaluate(()=>fixture.requests.filter(r=>r.operation==='read').at(-1).collaborationId),'C'.repeat(22));});
  await check('normal lifecycle handler resolves once after confirmation and publishes canonical response',async()=>{await tab('Settings').click();await button('Resolve collaboration').click();await button('Resolve').click();await button('Reopen collaboration').waitFor();assert.equal((await state()).canonical,'resolved');assert.equal((await state()).modal,'resolved');assert.equal(await page.evaluate(()=>fixture.requests.filter(r=>r.operation==='resolve').length),1);});
  const resolvedBeforeClose=await state();
  await check('real close/fence clears modal projection and returns to same selected source mail',async()=>{await button('Close collaboration').click();await page.locator('[data-selected-mail="mail"]').waitFor({state:'attached'});assert.equal((await state()).modal,null);});
  await check('resolve immediately retains canonical history and clears active projection before modal close',async()=>{assert.equal(resolvedBeforeClose.history,'resolved');assert.equal(resolvedBeforeClose.active,null);});
  await check('after close: same selected mail has exactly one View Collaboration CTA',async()=>{await button('View Collaboration').waitFor();assert.equal(await page.locator('[data-open-collaboration]').count(),1);assert.equal((await state()).history,'resolved');});
  await shot('resolved-source');
  await check('resolved history does not restore Priority; active-index empty fast path remains',async()=>{assert.equal(await page.evaluate(()=>fixture.prioritySource().source),'none');assert.deepEqual(await page.evaluate(()=>fixture.indexSizes()),{active:0,history:1});});
  await check('Manual Priority remains independent of resolved history',async()=>{assert.equal(await page.evaluate(()=>fixture.prioritySource('priority').source),'manual');assert.equal(await page.evaluate(()=>fixture.prioritySource('priority').level),'priority');});
  const requestsBeforeSelection=await page.evaluate(()=>fixture.requests.length);
  await check('unrelated mail with identical subject/sender has no CTA or fuzzy fallback',async()=>{await button('Select unrelated mail').click();assert.equal(await page.locator('[data-open-collaboration]').count(),0);assert.equal(await page.evaluate(()=>fixture.wrongSource()),null);});
  await check('reselect exact source mail restores View Collaboration without requests',async()=>{await button('Select source mail').click();await button('View Collaboration').waitFor();assert.equal(await page.evaluate(()=>fixture.requests.length),requestsBeforeSelection);});
  await check('hard refresh rehydrates resolved history via existing bounded list_summaries',async()=>{await page.reload();await button('View Collaboration').waitFor();assert.deepEqual(await page.evaluate(()=>fixture.requests.filter(r=>r.operation!=='csrf').map(r=>r.operation)),['list_summaries']);assert.equal((await state()).active,null);});
  await shot('resolved-rehydrated');
  await check('View Collaboration opens the same exact ID using existing lookup/read only',async()=>{await button('View Collaboration').click();await page.waitForFunction(()=>fixture.canonicalId()==='C'.repeat(22));const reads=await page.evaluate(()=>fixture.requests.filter(r=>r.operation!=='csrf'));assert.deepEqual(reads.map(r=>r.operation),['list_summaries','lookup','read']);assert.deepEqual(reads[1].sourceRef,{providerMessageId:'source-123'});assert.equal(reads[2].collaborationId,'C'.repeat(22));});
  await check('resolved Conversation, Email and Settings are readable; Reopen is available',async()=>{assert.equal(await tab('Conversation').getAttribute('aria-selected'),'true');assert.ok((await page.locator('#collaboration-panel-conversation').innerText()).includes('September release'));await tab('Email').click();await page.frameLocator('iframe').getByRole('heading',{name:'Ready for the final listen'}).waitFor();await tab('Settings').click();assert.equal(await page.locator('[data-collaboration-lifecycle-status]').innerText(),'Resolved');assert.ok(await button('Reopen collaboration').isVisible());});
  await shot('resolved-settings');
  await check('real Reopen publishes immediately and restores active Collaboration Priority with same ID',async()=>{await button('Reopen collaboration').click();await button('Resolve collaboration').waitFor();assert.equal((await state()).history,'note_only');assert.equal((await state()).active,'note_only');assert.equal(await page.evaluate(()=>fixture.canonicalId()),'C'.repeat(22));assert.equal(await page.evaluate(()=>fixture.prioritySource().source),'collaboration');assert.equal(await page.evaluate(()=>fixture.prioritySource('priority').source),'manual');});
  await check('close after Reopen shows one Open Collaboration CTA',async()=>{await button('Close collaboration').click();await button('Open Collaboration').waitFor();assert.equal(await page.locator('[data-open-collaboration]').count(),1);});
  await shot('reopened-source');
  await check('no new discovery, per-row requests or polling during resolve/close/reselect/reopen',async()=>{assert.deepEqual(await page.evaluate(()=>fixture.requests.filter(r=>r.operation!=='csrf').map(r=>r.operation)),['list_summaries','lookup','read','reopen']);});
  await check('closing during pending Resolve still publishes retained source binding',async()=>{await button('Open Collaboration').click();await page.waitForFunction(()=>fixture.canonicalId()==='C'.repeat(22));await tab('Settings').click();await page.evaluate(()=>{fixture.holdNext=true;});await button('Resolve collaboration').click();await button('Resolve').click();await page.waitForFunction(()=>fixture.release!==null);await button('Close collaboration').click();await page.evaluate(()=>fixture.release());await button('View Collaboration').waitFor();assert.equal((await state()).history,'resolved');assert.equal((await state()).modal,null);assert.equal((await state()).active,null);});
  await check('zero browser errors and external requests',async()=>{assert.deepEqual(errors,[]);assert.deepEqual(external,[]);});
  fs.writeFileSync(path.join(__dirname,'runtime-results.json'),JSON.stringify({passed:checks.length,checks,finalState:await state(),requests:await page.evaluate(()=>fixture.requests.map(({headers,...r})=>r)),errors,external},null,2)+'\n');

 }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
