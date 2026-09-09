// Local fixture only. Run from frontend with PLAYWRIGHT_MODULE pointing to installed Playwright.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import assert from 'node:assert/strict';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import postcss from 'postcss';
import tailwind from 'tailwindcss';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const directory = fileURLToPath(new URL('.', import.meta.url));
const fixture = String.raw`import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ServerNotifications } from './ServerNotifications';
import { useWorkspaceNotifications } from '../../lib/useWorkspaceNotifications';
import { notification, scope, time } from '../../lib/notificationsTestFixtures.test';
const calls: any[] = [];
let failList = false;
(window as any).__errors = [];
const originalError = console.error;
console.error = (...args) => { (window as any).__errors.push(args.join(' ')); originalError(...args); };
window.addEventListener('error', e => (window as any).__errors.push(e.message));
(window as any).__calls = calls;
(window as any).__failNextList = () => { failList = true; };
window.fetch = async (url, init) => {
  if (url !== '/api/notifications') throw Error('Non-fixture request blocked');
  const body = JSON.parse(init!.body as string); calls.push(body);
  if (body.operation === 'summary') return new Response(JSON.stringify({ v: 1, unreadCount: 87 }));
  if (body.operation === 'list') {
    if (failList) { failList = false; return new Response('{}', { status: 503 }); }
    const begin = body.cursor ? 51 : 1;
    return new Response(JSON.stringify({ v: 1, unreadCount: 87, notifications: Array.from({ length: body.cursor ? 4 : 50 }, (_, i) => notification(begin+i, i === 0 ? { actor: { type: 'external_guest', displayName: 'Alex Rivera' } } : {})), nextCursor: body.cursor ? null : 'opaque+/=' }));
  }
  return new Response(JSON.stringify({ v: 1, unreadCount: 86, notification: notification(1, { notificationId: body.notificationId, readAt: time }) }));
};
function Harness() {
  const [consumer, setConsumer] = useState('none');
  const [currentScope, setScope] = useState(scope);
  const value = useWorkspaceNotifications(currentScope);
  const [opened, setOpened] = useState('');
  (window as any).__state = value.state;
  return <main className="mx-auto max-w-4xl space-y-5 p-4 md:p-8"><nav className="flex flex-wrap gap-4"><button onClick={() => setConsumer('none')}>Hide records</button><button onClick={() => setConsumer('both')}>Dashboard + Notifications</button><button onClick={() => setConsumer('workbench')}>Notifications only</button><button onClick={() => setScope({ ...scope, workspaceId: 'wsp_' + 'Z'.repeat(22) })}>Switch workspace</button></nav><p data-badge>Unread count: {value.state.unreadCount}</p><p data-opened className="break-all">{opened}</p>{consumer === 'both' ? <section className="rounded-3xl bg-[var(--workspace-card)] p-6"><ServerNotifications {...value} preview onOpen={row => setOpened(row.notificationId)} /></section> : null}{consumer !== 'none' ? <section className="rounded-3xl bg-[var(--workspace-card)] p-6"><h1 className="mb-4 text-2xl">Notifications</h1><ServerNotifications {...value} onOpen={row => setOpened(row.notificationId)} /></section> : null}</main>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><Harness /></React.StrictMode>);
`;
const bundle = await build({ stdin: { contents: fixture, loader: 'tsx', resolveDir: directory }, bundle: true, write: false, jsx: 'automatic' });
const styles = await postcss([tailwind({ content: [
  { raw: fixture, extension: 'tsx' },
  { raw: fs.readFileSync(path.join(directory, 'ServerNotifications.tsx'), 'utf8'), extension: 'tsx' },
], theme: { extend: {} }, plugins: [] })]).process(fs.readFileSync(path.join(directory, '../../index.css'), 'utf8'), { from: undefined });
const assets = {
 '/': ['text/html', '<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>C3D local fixture</title><link rel="stylesheet" href="/style.css"></head><body><div id="root"></div><script src="/app.js"></script></body></html>'],
 '/app.js': ['text/javascript', bundle.outputFiles[0].text], '/style.css': ['text/css', styles.css],
};
const server = http.createServer((req, res) => { const asset = assets[req.url]; if (!asset) { res.writeHead(404).end(); return; } res.setHeader('Content-Type', asset[0]); res.end(asset[1]); });
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = 'http://127.0.0.1:' + server.address().port;
const output = fs.mkdtempSync(path.join(os.tmpdir(), 'c3d-browser-'));
let browser;
try {

  browser=await chromium.launch({headless:true,channel:"chrome"});const page=await browser.newPage({viewport:{width:1280,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.route('**/*',route=>route.request().url().startsWith(origin + "/")?route.continue():route.abort());
  await page.goto(origin + "/");await page.waitForFunction(()=>window.__state?.unreadCount===87);
  assert.deepEqual(await page.evaluate(()=>window.__calls),[{operation:'summary'}]);
  console.log('PASS StrictMode startup: exactly one summary, zero lists');
  await page.getByRole('button',{name:'Dashboard + Notifications',exact:true}).click();await page.waitForSelector('[data-notification-id]');
  assert.equal(await page.locator('[data-server-notifications="dashboard"] [data-notification-id]').count(),5);
  assert.equal(await page.locator('[data-server-notifications="workbench"] [data-notification-id]').count(),50);
  assert.equal((await page.evaluate(()=>window.__calls)).filter(c=>c.operation==='list').length,1);
  console.log('PASS concurrent Dashboard + workbench share one page, 5/50 rows, count 87');
  await page.screenshot({path:output + "/desktop.png"});
  const row=page.locator('[data-server-notifications="workbench"] [data-notification-id]').first();await row.focus();await page.keyboard.press('Enter');assert.ok((await page.locator('[data-opened]').textContent()).includes('ntf_'));
  assert.equal((await page.evaluate(()=>window.__calls)).filter(c=>c.operation==='mark_read').length,0);
  console.log('PASS keyboard row activation delegates handler, no click-time mark-read');
  await page.getByRole('button',{name:'Load more',exact:true}).click();await page.waitForFunction(()=>window.__state.orderedIds.length===54);
  assert.equal(await page.getByRole('button',{name:'Load more',exact:true}).count(),0);
  assert.deepEqual((await page.evaluate(()=>window.__calls)).filter(c=>c.operation==='list').map(c=>c.cursor),[null,'opaque+/=']);
  console.log('PASS Load more one unchanged cursor, no automatic drain');
  await page.getByRole('button',{name:'Notifications only',exact:true}).click();await page.setViewportSize({width:375,height:812});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth));await page.screenshot({path:output + "/mobile.png"});
  console.log('PASS responsive 375px layout without horizontal overflow');
  await page.evaluate(()=>window.__failNextList());await page.getByRole('button',{name:'Refresh',exact:true}).click();await page.getByRole('button',{name:'Retry',exact:true}).waitFor();
  assert.equal(await page.locator('[data-notification-id]').count(),54);await page.getByRole('button',{name:'Retry',exact:true}).click();await page.waitForFunction(()=>!window.__state.listError&&window.__state.orderedIds.length===50);
  console.log('PASS error preserves rows; explicit Retry recovers');
  await page.getByRole('button',{name:'Hide records',exact:true}).click();await page.getByRole('button',{name:'Switch workspace',exact:true}).click();await page.waitForFunction(()=>window.__state.scope.workspaceId.endsWith('ZZZZ'));
  assert.equal(await page.evaluate(()=>window.__state.orderedIds.length),0);assert.equal((await page.evaluate(()=>window.__calls)).filter(c=>c.operation==='summary').length,2);
  console.log('PASS workspace switch clears records and requests one new summary');
  assert.deepEqual(errors,[]);assert.deepEqual(await page.evaluate(()=>window.__errors),[]);console.log('PASS no browser or React console errors; nonlocal traffic blocked');

  console.log("Browser artifacts: " + output);
} finally { await browser?.close(); server.close(); }
