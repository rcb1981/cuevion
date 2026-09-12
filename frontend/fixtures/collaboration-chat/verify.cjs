// Run after build.mjs with the fixture served on 127.0.0.1:4175.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { chromium } = require(process.env.CUEVION_PLAYWRIGHT || '/Users/rutger/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true, executablePath: process.env.CUEVION_CHROME || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' });
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } });
  const errors = [], network = [], results = [];
  const p = await context.newPage();
  p.on('pageerror', e => errors.push(e.message));
  p.on('console', e => { if (e.type() === 'error') errors.push(e.text()); });
  await p.route('**/*', route => { const url = route.request().url(); network.push(url); return url.startsWith('http://127.0.0.1:4175/') ? route.continue() : route.abort(); });
  const check = async (name, fn) => { await fn(); results.push(name); console.log('PASS', name); };
  const fresh = async () => { await p.goto('http://127.0.0.1:4175'); await p.getByRole('button', { name: 'People · 3' }).waitFor(); };
  const waitFor = async fn => p.waitForFunction(fn);
  const shared = () => p.getByRole('button', { name: 'Shared', exact: true }).click();
  const internal = () => p.getByRole('button', { name: 'Internal', exact: true }).click();
  const draft = () => p.locator('textarea');
  try {
    await fresh();
    await check('page renders with one Shared composer and People closed', async () => {
      assert.equal(await p.locator('textarea:visible').count(), 1);
      assert.equal(await draft().getAttribute('placeholder'), 'Write a message…');
      assert.equal(await p.locator('[data-collaboration-access-panel]').isVisible(), false);
      assert.equal(await p.getByRole('button', { name: 'Close collaboration', exact: true }).count(), 1);
    });
    await check('strict identity alignment and chronological DOM order', async () => {
      const rows = await p.locator('[data-collaboration-activity-id]').evaluateAll(nodes => nodes.map(n => [n.dataset.collaborationActivityId, n.dataset.collaborationAlignment]));
      assert.deepEqual(rows, ['A','B','D','E','F','J','K'].map((id,i) => [id.repeat(22), [1,3].includes(i) ? 'right' : 'left']));
    });
    for (const [w,h,name] of [[1600,1000,'desktop'],[1280,800,'laptop'],[768,1024,'tablet'],[375,812,'mobile']]) {
      await p.setViewportSize({width:w,height:h});
      await check(`${w}×${h}: no overflow, one content scroll, reachable composer, aligned bubbles, People`, async () => {
        const metrics = await p.evaluate(() => {
          const rect = n => { const r=n.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:r.width}; };
          const dialog=document.querySelector('[role="dialog"]'), scroll=document.querySelector('[data-collaboration-scroll-body]');
          return { viewport:innerWidth, horizontal:document.documentElement.scrollWidth, vertical:document.documentElement.scrollHeight, height:innerHeight, dialog:rect(dialog), send:rect([...dialog.querySelectorAll('button')].find(n=>n.textContent==='Send')), scroll:rect(scroll), right:rect(document.querySelector('[data-collaboration-alignment="right"]')), left:rect(document.querySelector('[data-collaboration-alignment="left"]')), scrolls:[...dialog.querySelectorAll('*')].filter(n=>n.offsetParent && n.scrollHeight>n.clientHeight+1 && /auto|scroll/.test(getComputedStyle(n).overflowY) && n.tagName!=='TEXTAREA').length };
        });
        assert.ok(metrics.horizontal <= w); assert.ok(metrics.vertical <= h); assert.ok(metrics.send.bottom <= h); assert.ok(metrics.dialog.left >= 0 && metrics.dialog.right <= w);
        assert.equal(metrics.scrolls, 1); assert.ok(metrics.right.right > metrics.left.right); assert.ok(metrics.right.width <= 544); assert.ok(metrics.left.width <= 544);
        await p.getByRole('button',{name:'People · 3'}).click();
        assert.ok(await p.getByRole('heading',{name:'Cuevion Team'}).isVisible());
        await p.getByRole('button',{name:'Back to conversation'}).click();
        assert.equal(await p.getByRole('button',{name:'People · 3'}).evaluate(n=>document.activeElement===n),true);
      });
      await p.screenshot({ path:path.join(__dirname,`${name}.png`) });
    }
    await p.setViewportSize({width:1600,height:1000});
    await check('independent drafts and Internal-only privacy cue', async () => {
      await draft().fill('Shared draft'); await internal(); assert.equal(await draft().inputValue(),'');
      assert.ok(await p.getByText('Only your Cuevion team can see this.',{exact:true}).isVisible());
      await draft().fill('Private draft'); await shared(); assert.equal(await draft().inputValue(),'Shared draft');
      assert.equal(await p.getByText('Only your Cuevion team can see this.',{exact:true}).count(),0);
      await internal(); assert.equal(await draft().inputValue(),'Private draft'); await shared();
    });
    await check('Shared send uses existing handler and clears only Shared', async () => {
      await p.getByRole('button',{name:'Send',exact:true}).click();
      await waitFor(() => document.querySelector('textarea').value === '');
      const calls=await p.evaluate(()=>fixture.requests.filter(r=>r.operation.startsWith('append_')));
      assert.equal(calls.length,1);assert.equal(calls[0].operation,'append_shared');assert.equal(calls[0].text,'Shared draft');
      await internal(); assert.equal(await draft().inputValue(),'Private draft');
    });
    await check('Internal send uses existing handler and clears only Internal', async () => {
      await shared();await draft().fill('Unsent Shared');await internal();await p.getByRole('button',{name:'Send',exact:true}).click();
      await waitFor(() => document.querySelector('textarea').value === '');await shared();assert.equal(await draft().inputValue(),'Unsent Shared');
      assert.equal(await p.evaluate(()=>fixture.requests.filter(r=>r.operation==='append_internal').length),1);
    });
    await check('failure retains draft, safe message, explicit retry keeps idempotency key', async () => {
      await p.evaluate(()=>fixture.failNext=true);await p.getByRole('button',{name:'Send',exact:true}).click();await p.getByRole('button',{name:'Retry',exact:true}).waitFor();
      assert.equal(await draft().inputValue(),'Unsent Shared');
      await p.getByRole('button',{name:'Retry',exact:true}).click();await waitFor(()=>document.querySelector('textarea').value==='');
      const calls=await p.evaluate(()=>fixture.requests.filter(r=>r.operation==='append_shared'));
      assert.deepEqual(calls.at(-1).headers,calls.at(-2).headers);
    });
    await check('in-flight guards survive mode switch, drafts remain independent', async () => {
      await draft().fill('Pending Shared');await p.evaluate(()=>fixture.holdNext=true);await p.getByRole('button',{name:'Send',exact:true}).click();
      await p.getByRole('button',{name:'Sending…'}).waitFor();assert.equal(await draft().isDisabled(),true);
      await internal();await draft().fill('Private during send');assert.equal(await p.getByRole('button',{name:'Send',exact:true}).isDisabled(),true);
      await p.evaluate(()=>fixture.release());await waitFor(()=>!document.querySelector('button.bg-pine')?.disabled);
      assert.equal(await draft().inputValue(),'Private during send');await shared();assert.equal(await draft().inputValue(),'');
    });
    await check('Enter inserts a newline without sending; selector is keyboard accessible',async()=>{
      const before=await p.evaluate(()=>fixture.requests.length);await draft().fill('Line one');await draft().press('Enter');assert.equal(await p.evaluate(()=>fixture.requests.length),before);
      await p.getByRole('button',{name:'Internal',exact:true}).focus();await p.keyboard.press('Space');assert.equal(await draft().getAttribute('placeholder'),'Write an internal note…');
      assert.ok(await p.getByRole('button',{name:'Internal',exact:true}).evaluate(n=>getComputedStyle(n).outlineStyle!=='none'));
    });
    for (const [id,name] of [['B','Shared'],['E','Internal'],['D','Guest']]) await check(`${name} C3D exact ref, focus, highlight before displayed acknowledgement`,async()=>{
      await p.evaluate(id=>fixture.target(id.repeat(22)),id);await waitFor(()=>fixture.marks.length>0);
      const marks=await p.evaluate(()=>fixture.marks);assert.ok(marks.every(m=>m.displayed && m.focused===id.repeat(22)));
      assert.equal(await p.locator('[data-notification-highlighted="true"]').getAttribute('data-collaboration-activity-id'),id.repeat(22));
      assert.equal(await p.evaluate(id=>fixture.refs.current[id.repeat(22)]===document.activeElement,id),true);
    });
    await check('missing activity refuses displayed acknowledgement',async()=>{
      await p.evaluate(()=>fixture.target('missing'));await waitFor(()=>fixture.marks.length>0);assert.ok((await p.evaluate(()=>fixture.marks)).every(m=>m.displayed===false));
    });
    await fresh();
    await check('source context uses existing subject/sender/time data',async()=>{
      await p.getByRole('button',{name:'View source email'}).click();assert.ok(await p.locator('#collaboration-source-email').isVisible());assert.ok((await p.locator('#collaboration-source-email').innerText()).includes('alex@example.test'));
    });
    await check('Resolve and Reopen preserve existing handlers and status',async()=>{
      await p.getByRole('button',{name:'Resolve collaboration',exact:true}).click();await p.getByRole('button',{name:'Reopen collaboration',exact:true}).waitFor();assert.equal(await p.locator('[data-collaboration-lifecycle-status]').innerText(),'Resolved');
      await p.getByRole('button',{name:'Reopen collaboration',exact:true}).click();await p.getByRole('button',{name:'Resolve collaboration',exact:true}).waitFor();assert.equal(await p.locator('[data-collaboration-lifecycle-status]').innerText(),'Active');
    });
    await check('People exposes historical guests and adds an eligible Team member',async()=>{
      await p.getByRole('button',{name:'People · 3'}).click();assert.ok(await p.getByText('Previous reviewer',{exact:true}).isVisible());assert.ok(await p.getByText('Revoked',{exact:true}).isVisible());
      await p.getByRole('button',{name:'Add Team member',exact:true}).click();await p.getByRole('combobox',{name:'Team member'}).selectOption({label:'Dan — dan@example.test'});await p.getByRole('button',{name:'Add Team member',exact:true}).last().click();await p.getByRole('button',{name:'People · 4'}).waitFor();
    });
    await check('guest invitation produces transient secure link; Copy works',async()=>{
      await p.getByRole('button',{name:'Invite external guest',exact:true}).click();await p.getByRole('button',{name:'Create secure link',exact:true}).click();await p.locator('[data-collaboration-secure-link]').waitFor();
      await p.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async text=>{fixture.copied=text;}}}));
      await p.getByRole('button',{name:'Copy secure link',exact:true}).click();await p.getByText('Link copied',{exact:true}).waitFor();assert.ok((await p.evaluate(()=>fixture.copied)).includes('#'));
    });
    await check('People toggling preserves link and overlay close protection',async()=>{
      const link=await p.locator('[data-collaboration-secure-link] input').inputValue();await p.getByRole('button',{name:'Back to conversation'}).click();
      await p.getByRole('button',{name:'Close collaboration',exact:true}).click();assert.ok(await p.getByRole('alertdialog').isVisible());await p.getByRole('button',{name:'Back',exact:true}).click();
      await p.getByRole('button',{name:'People · 5'}).click();assert.equal(await p.locator('[data-collaboration-secure-link] input').inputValue(),link);
      assert.deepEqual(await p.evaluate(()=>[localStorage.length,sessionStorage.length]),[0,0]);
    });
    await check('guest revoke retains history and clears matching transient link',async()=>{
      const guest=p.locator('[data-collaboration-access-panel]').getByRole('button',{name:'Revoke access',exact:true}).last();await guest.click();await p.getByRole('button',{name:'Revoke access',exact:true}).last().click();
      await waitFor(()=>!document.querySelector('[data-collaboration-secure-link]'));assert.equal(await p.getByText('Revoked',{exact:true}).count(),2);
    });
    await check('no browser errors or external requests',async()=>{assert.deepEqual(errors,[]);assert.ok(network.every(u=>u.startsWith('http://127.0.0.1:4175/')));});
    fs.writeFileSync(path.join(__dirname,'browser-results.json'),JSON.stringify({passed:results.length,tests:results,errors,network:[...new Set(network)]},null,2)+'\n');
    console.log(`PASS ${results.length} browser checks`);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
