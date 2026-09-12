// Run after build.mjs with the fixture served on 127.0.0.1:4176.
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
  await p.route('**/*', route => { const url = route.request().url(); network.push(url); return url.startsWith('http://127.0.0.1:4176/') ? route.continue() : route.abort(); });
  const check = async (name, fn) => { await fn(); results.push(name); console.log('PASS', name); };
  const fresh = async (suffix = '') => { await p.goto('http://127.0.0.1:4176/' + suffix); await p.getByRole('button', { name: 'People · 3' }).waitFor(); };
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
      await p.screenshot({ path:path.join(__dirname,`conversation-${name}.png`), animations:'disabled' });
    }
    await p.setViewportSize({width:1600,height:1000});

    await check('exactly two semantic tabs; Conversation default; old source action absent',async()=>{
      assert.equal(await p.getByRole('tab').count(),2);assert.equal(await p.getByRole('tab',{name:'Conversation',exact:true}).getAttribute('aria-selected'),'true');
      assert.equal(await p.getByRole('button',{name:'View source email'}).count(),0);
      assert.equal(await p.getByRole('button',{name:'Active',exact:true}).count(),0);
    });
    await check('tabs retain Shared/Internal drafts, composer mode, scroll and DOM nodes',async()=>{
      await draft().fill('Preserved Shared');await internal();await draft().fill('Preserved Internal');
      await p.evaluate(()=>{const n=document.querySelector('[data-collaboration-scroll-body]');n.scrollTop=120;fixture.savedScroll=n.scrollTop;fixture.savedTextarea=document.querySelector('textarea');});
      await p.getByRole('tab',{name:'Email',exact:true}).click();assert.equal(await p.locator('textarea:visible').count(),0);
      await p.getByRole('tab',{name:'Conversation',exact:true}).click();assert.equal(await draft().inputValue(),'Preserved Internal');
      assert.equal(await p.evaluate(()=>fixture.savedTextarea===document.querySelector('textarea')),true);
      assert.equal(await p.evaluate(()=>fixture.savedScroll===document.querySelector('[data-collaboration-scroll-body]').scrollTop),true);
      await shared();assert.equal(await draft().inputValue(),'Preserved Shared');
    });
    await check('keyboard arrows, Home and End select tabs and move focus',async()=>{
      const conversation=p.getByRole('tab',{name:'Conversation',exact:true});await conversation.focus();await p.keyboard.press('ArrowRight');
      assert.equal(await p.getByRole('tab',{name:'Email',exact:true}).getAttribute('aria-selected'),'true');
      await p.keyboard.press('Home');assert.equal(await conversation.getAttribute('aria-selected'),'true');await p.keyboard.press('End');assert.equal(await p.getByRole('tab',{name:'Email',exact:true}).evaluate(n=>n===document.activeElement),true);
    });
    await check('exact metadata, reused HTML reader, inline image, safe links and attachments',async()=>{
      const email=p.locator('#collaboration-panel-email');const meta=await email.locator('dl').innerText();
      for(const text of ['Alex Morgan','alex@example.test','rutger@example.test','jamie@example.test','dan@example.test','September release · final master']) assert.ok(meta.includes(text));
      assert.equal(await email.locator('time').getAttribute('datetime'),'2026-09-12T12:20:00.000Z');
      const frame=p.frameLocator('iframe[title="Email content"]');await frame.getByRole('heading',{name:'Ready for the final listen'}).waitFor();
      assert.ok((await frame.locator('body').innerText()).includes('final master'));
      assert.ok((await frame.getByAltText('September artwork').getAttribute('src')).startsWith('data:image/png'));
      assert.equal(await frame.locator('script').count(),0);assert.equal(await frame.locator('[onerror]').count(),0);
      assert.equal(await email.locator('iframe').getAttribute('sandbox'),'allow-popups allow-popups-to-escape-sandbox');
      assert.equal(await frame.getByRole('link',{name:'Read the release notes'}).getAttribute('href'),'https://example.test/release-notes');
      assert.ok((await frame.getByRole('link',{name:'Read the release notes'}).getAttribute('rel')).includes('noopener'));
      assert.equal(await email.getByRole('button',{name:/September-final-master/}).count(),1);assert.equal(await email.getByRole('button',{name:/Release-notes.pdf/}).count(),1);
      await email.getByRole('button',{name:/Release-notes.pdf/}).click();assert.deepEqual(await p.evaluate(()=>fixture.attachmentOpen),{attachmentId:'notes',messageId:'mail'});
      for(const name of ['Reply','Forward','Archive','Trash']) assert.equal(await email.getByRole('button',{name,exact:true}).count(),0);
    });
    await check('Email tab switches make no request and retain the same iframe',async()=>{
      const before=await p.evaluate(()=>{fixture.savedFrame=document.querySelector('iframe');return fixture.requests.length;});
      for(let i=0;i<3;i++){await p.getByRole('tab',{name:'Conversation',exact:true}).click();await p.getByRole('tab',{name:'Email',exact:true}).click();}
      assert.equal(await p.evaluate(()=>fixture.requests.length),before);assert.equal(await p.evaluate(()=>fixture.savedFrame===document.querySelector('iframe')),true);
      assert.ok(await p.getByRole('button',{name:'Show images',exact:true}).isVisible());
    });
    for (const [w,h,name] of [[1600,1000,'desktop'],[1280,800,'laptop'],[768,1024,'tablet'],[375,812,'mobile']]) {
      await p.setViewportSize({width:w,height:h});
      await check(`Email ${w}×${h}: metadata wraps, no horizontal/page overflow; existing sandbox scrolling retained`,async()=>{
        const metrics=await p.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth,pageHeight:document.documentElement.scrollHeight,height:innerHeight,panel:document.querySelector('#collaboration-panel-email').getBoundingClientRect().width,iframe:document.querySelector('iframe').getBoundingClientRect().width,scrolls:[...document.querySelector('[data-collaboration-thread-modal]').querySelectorAll('*')].filter(n=>n.offsetParent&&n.scrollHeight>n.clientHeight+1&&/auto|scroll/.test(getComputedStyle(n).overflowY)).length}));
        assert.ok(metrics.scroll<=w);assert.ok(metrics.pageHeight<=h);assert.ok(metrics.iframe<=metrics.panel);assert.ok(metrics.scrolls<=1);
      });
      await p.locator('#collaboration-panel-email').evaluate(n=>{n.scrollTop=0;});
      await p.screenshot({path:path.join(__dirname,`email-${name}.png`),animations:'disabled'});
    }
    await fresh('?plain=1');await p.getByRole('tab',{name:'Email',exact:true}).click();
    await check('plain fallback uses the existing paragraph renderer',async()=>{assert.equal(await p.locator('iframe').count(),0);assert.ok((await p.locator('#collaboration-panel-email').innerText()).includes('Please review the final September master'));assert.equal(await p.locator('[data-render-mode="plain"]').count(),1);});
    await fresh();await p.setViewportSize({width:1600,height:1000});
    await check('overflow is keyboard accessible and Escape dismisses only the menu',async()=>{
      await p.getByRole('button',{name:'Collaboration actions',exact:true}).focus();await p.keyboard.press('ArrowDown');assert.equal(await p.getByRole('menuitem').evaluate(n=>n===document.activeElement),true);await p.keyboard.press('Escape');assert.equal(await p.getByRole('menu').count(),0);assert.ok(await p.getByRole('tab',{name:'Conversation',exact:true}).isVisible());
    });
    await check('Resolve requires confirmation, Cancel default, focus trapped, Cancel and Escape do not resolve',async()=>{
      const open=async()=>{await p.getByRole('button',{name:'Collaboration actions',exact:true}).click();await p.getByRole('menuitem',{name:'Resolve collaboration'}).click();await p.getByRole('dialog',{name:'Resolve this collaboration?'}).waitFor();};
      await open();assert.equal(await p.getByRole('button',{name:'Cancel',exact:true}).evaluate(n=>n===document.activeElement),true);
      await p.screenshot({path:path.join(__dirname,'resolve-confirmation.png'),animations:'disabled'});
      await p.keyboard.press('Shift+Tab');assert.equal(await p.getByRole('button',{name:'Resolve',exact:true}).evaluate(n=>n===document.activeElement),true);
      await p.keyboard.press('Tab');assert.equal(await p.getByRole('button',{name:'Cancel',exact:true}).evaluate(n=>n===document.activeElement),true);
      await p.getByRole('button',{name:'Cancel',exact:true}).click();assert.equal(await p.getByRole('button',{name:'Collaboration actions',exact:true}).evaluate(n=>n===document.activeElement),true);
      await open();await p.keyboard.press('Escape');assert.equal(await p.getByRole('dialog',{name:'Resolve this collaboration?'}).count(),0);assert.ok(await p.getByRole('tab',{name:'Conversation',exact:true}).isVisible());
      assert.equal(await p.evaluate(()=>fixture.requests.filter(r=>r.operation==='resolve').length),0);
    });
    for (const [w,h,label] of [[1600,1000,'desktop'],[375,812,'mobile']]) {
      await p.setViewportSize({width:w,height:h});await p.evaluate(()=>fixture.theme('dark'));
      await check(`dark ${label}: Conversation, Email, People, menu/dialog and focus visible`,async()=>{
        await p.getByRole('tab',{name:'Conversation',exact:true}).click();await p.getByRole('tab',{name:'Conversation',exact:true}).focus();await p.keyboard.press('Home');
        assert.ok(await p.getByRole('tab',{name:'Conversation',exact:true}).evaluate(n=>getComputedStyle(n).outlineStyle!=='none'));
        await p.screenshot({path:path.join(__dirname,`dark-conversation-${label}.png`),animations:'disabled'});
        await p.getByRole('tab',{name:'Email',exact:true}).click();await p.frameLocator('iframe[title="Email content"]').getByRole('heading',{name:'Ready for the final listen'}).waitFor();await p.locator('#collaboration-panel-email').evaluate(n=>{n.scrollTop=0;});await p.screenshot({path:path.join(__dirname,`dark-email-${label}.png`),animations:'disabled'});
        await p.getByRole('button',{name:'People · 3'}).click();await p.screenshot({path:path.join(__dirname,`dark-people-${label}.png`),animations:'disabled'});await p.getByRole('button',{name:'Back to email'}).click();
        await p.getByRole('button',{name:'Collaboration actions',exact:true}).click();await p.screenshot({path:path.join(__dirname,`dark-menu-${label}.png`),animations:'disabled'});
        await p.getByRole('menuitem',{name:'Resolve collaboration'}).click();await p.screenshot({path:path.join(__dirname,`dark-confirmation-${label}.png`),animations:'disabled'});await p.getByRole('button',{name:'Cancel',exact:true}).click();
      });
    }
    await fresh();await p.setViewportSize({width:1600,height:1000});
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
      await p.getByRole('tab',{name:'Email',exact:true}).click();await p.evaluate(id=>fixture.target(id.repeat(22)),id);await waitFor(()=>fixture.marks.length>0);
      assert.equal(await p.getByRole('tab',{name:'Conversation',exact:true}).getAttribute('aria-selected'),'true');
      const marks=await p.evaluate(()=>fixture.marks);assert.ok(marks.every(m=>m.displayed && m.focused===id.repeat(22)));
      assert.equal(await p.locator('[data-notification-highlighted="true"]').getAttribute('data-collaboration-activity-id'),id.repeat(22));
      assert.equal(await p.evaluate(id=>fixture.refs.current[id.repeat(22)]===document.activeElement,id),true);
    });
    await check('missing activity refuses displayed acknowledgement',async()=>{
      await p.evaluate(()=>fixture.target('missing'));await waitFor(()=>fixture.marks.length>0);assert.ok((await p.evaluate(()=>fixture.marks)).every(m=>m.displayed===false));
    });
    await fresh();

    await check('Resolve and Reopen preserve existing handlers and status',async()=>{
      await p.getByRole('button',{name:'Collaboration actions',exact:true}).click();await p.getByRole('menuitem',{name:'Resolve collaboration',exact:true}).click();await p.getByRole('button',{name:'Resolve',exact:true}).evaluate(n=>{n.click();n.click();});await p.getByRole('button',{name:'Collaboration actions',exact:true}).click();await p.getByRole('menuitem',{name:'Reopen collaboration',exact:true}).waitFor();assert.equal(await p.locator('[data-collaboration-lifecycle-status]').innerText(),'Resolved');
      await p.getByRole('menuitem',{name:'Reopen collaboration',exact:true}).click();await p.waitForFunction(()=>document.querySelector('[data-collaboration-lifecycle-status]').textContent==='Active');assert.equal(await p.locator('[data-collaboration-lifecycle-status]').innerText(),'Active');
      assert.equal(await p.evaluate(()=>fixture.requests.filter(r=>r.operation==='resolve').length),1);assert.equal(await p.evaluate(()=>fixture.requests.filter(r=>r.operation==='reopen').length),1);
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
    await check('no browser errors or external requests',async()=>{assert.deepEqual(errors,[]);assert.ok(network.every(u=>u.startsWith('http://127.0.0.1:4176/')));});
    fs.writeFileSync(path.join(__dirname,'browser-results.json'),JSON.stringify({passed:results.length,tests:results,errors,network:[...new Set(network)]},null,2)+'\n');
    console.log(`PASS ${results.length} browser checks`);
  } finally { await browser.close(); }
})().catch(e=>{console.error(e);process.exitCode=1;});
