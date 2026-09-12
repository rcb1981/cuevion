const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {chromium}=require('/Users/rutger/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
 const context=await browser.newContext({viewport:{width:1400,height:1050}}),page=await context.newPage(),results=[],errors=[],network=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/*',route=>{const url=route.request().url();network.push(url);return /^http:\/\/127\.0\.0\.1:(4180|4176)\//.test(url)?route.continue():route.abort();});
 const check=async(name,fn)=>{await fn();results.push(name);console.log('PASS '+name);};
 function contrast(node) {
  const parse=s=>{const a=s.match(/[\d.]+/g).map(Number);return [a[0],a[1],a[2],a[3]??1];};
  const blend=(a,b)=>a.slice(0,3).map((v,i)=>v*a[3]+b[i]*(1-a[3]));
  const chain=[];for(let n=node;n;n=n.parentElement)chain.unshift(n);
  let bg=[255,255,255];for(const n of chain)bg=blend(parse(getComputedStyle(n).backgroundColor),bg);
  const fg=blend(parse(getComputedStyle(node).color),bg);
  const lum=c=>c.map(v=>v/255).map(v=>v<=0.04045?v/12.92:((v+0.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[0.2126,0.7152,0.0722][i],0);
  const a=lum(fg),b=lum(bg);return (Math.max(a,b)+0.05)/(Math.min(a,b)+0.05);
 }
 const ratios={};
 try {
  for(const theme of ['light','dark']) {
   await page.goto('http://127.0.0.1:4180/?theme='+theme);await page.locator('[data-case="ready-dashboard"] button').waitFor();
   await check(theme+': Dashboard/page Refresh, Retry and disabled Refresh contrast',async()=>{
    ratios[theme]=[];
    for(const mode of ['ready','loading','retry'])for(const view of ['dashboard','page']){
     const box=page.locator(`[data-case="${mode}-${view}"]`),refresh=box.getByRole('button',{name:'Refresh',exact:true});
     assert.equal(await refresh.isDisabled(),mode==='loading');
     const ratio=await refresh.evaluate(contrast);ratios[theme].push({mode,view,ratio});assert.ok(ratio>=4.5,`${theme} ${mode} ${view}: ${ratio}`);
     if(mode==='retry')assert.ok(await box.getByRole('button',{name:'Retry',exact:true}).evaluate(contrast)>=4.5);
    }
   });
   await check(theme+': secondary hover and keyboard focus visible',async()=>{
    const button=page.locator('[data-case="ready-dashboard"]').getByRole('button',{name:'Refresh'});
    await page.mouse.move(0,0);const before=await button.evaluate(n=>getComputedStyle(n).backgroundColor);
    await button.hover();assert.notEqual(await button.evaluate(n=>getComputedStyle(n).backgroundColor),before);
    await button.focus();await page.keyboard.press('Tab');await page.keyboard.press('Shift+Tab');
    assert.ok(await button.evaluate(n=>n===document.activeElement&&getComputedStyle(n).outlineStyle!=='none'));
   });
   await page.screenshot({path:path.join(__dirname,`notifications-${theme}.png`),animations:'disabled'});
   await check(theme+': Refresh and Retry keep existing single-call behavior',async()=>{
    const before=await page.evaluate(()=>fixture.calls.filter(c=>c[0]==='refresh').length);
    await page.locator('[data-case="ready-dashboard"]').getByRole('button',{name:'Refresh'}).click();
    await page.locator('[data-case="ready-page"]').getByRole('button',{name:'Refresh'}).click();
    await page.locator('[data-case="retry-page"]').getByRole('button',{name:'Retry'}).click();
    assert.equal(await page.evaluate(()=>fixture.calls.filter(c=>c[0]==='refresh').length),before+3);
    assert.deepEqual(await page.evaluate(()=>fixture.calls.filter(c=>c[0]==='ensure').map(c=>c[1])),['dashboard','workbench','dashboard','workbench','dashboard','workbench']);
   });
  }
  await page.goto('http://127.0.0.1:4176/?formatted-sender=1');
  await check('Actual Collaboration Email uses clean From and preserves tabs/body',async()=>{
   assert.equal(await page.getByRole('tab',{name:'Conversation',exact:true}).getAttribute('aria-selected'),'true');
   await page.getByRole('tab',{name:'Email',exact:true}).click();
   assert.equal(await page.locator('#collaboration-panel-email dd').first().innerText(),'Alex Morgan <alex@example.test>');
   await page.frameLocator('iframe').getByRole('heading',{name:'Ready for the final listen'}).waitFor();
   assert.equal(await page.locator('iframe').getAttribute('sandbox'),'allow-popups allow-popups-to-escape-sandbox');
   await page.screenshot({path:path.join(__dirname,'email-sender.png'),animations:'disabled'});
  });
  await check('No browser errors or external network requests',async()=>{assert.deepEqual(errors,[]);assert.ok(network.every(u=>/^http:\/\/127\.0\.0\.1:(4180|4176)\//.test(u)));});
  fs.writeFileSync(path.join(__dirname,'browser-results.json'),JSON.stringify({passed:results.length,results,ratios,errors,network:[...new Set(network)]},null,2)+'\n');
 } finally {await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
