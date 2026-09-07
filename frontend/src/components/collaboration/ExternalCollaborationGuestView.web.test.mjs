// From frontend: PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node
// src/components/collaboration/ExternalCollaborationGuestView.web.test.mjs
// Requires installed esbuild, Playwright and Chrome. All API traffic is fixture-only.
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { build } from 'esbuild';
import { fileURLToPath } from 'node:url';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const bundle = await build({ stdin: {
  contents: `import React from 'react'; import { createRoot } from 'react-dom/client';
    import { ExternalCollaborationGuestView } from './ExternalCollaborationGuestView';
    import { parseCollaborationGuestEntryRoute } from '../../lib/collaborationGuestInviteLink';
    const route = parseCollaborationGuestEntryRoute(location.hash, location.search);
    createRoot(document.getElementById('root')).render(<React.StrictMode>
      <ExternalCollaborationGuestView initialInviteToken={route.token} /></React.StrictMode>);`,
  loader: 'tsx', resolveDir: fileURLToPath(new URL('.', import.meta.url)),
}, bundle: true, write: false, jsx: 'automatic', format: 'iife' });
const server = createServer((req, res) => {
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Content-Type', req.url === '/app.js' ? 'text/javascript' : 'text/html');
  res.end(req.url === '/app.js' ? bundle.outputFiles[0].text :
    '<!doctype html><html><head><meta charset="UTF-8"><title>Guest fixture</title></head><body><div id="root"></div><script src="/app.js"></script></body></html>');
});
await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
const origin = `http://127.0.0.1:${server.address().port}`;
const session = { collaborationId: 'D'.repeat(22), guestDisplayName: 'Reviewer',
  allowedActions: ['read', 'reply'], identityAssurance: 'link_possession', expiresAt: 1900000000 };
const collaboration = { collaborationId: session.collaborationId, state: 'needs_review',
  updatedAt: 1800000000000, allowedActions: ['read', 'reply'], sharedSource: {
    subject: 'Shared fixture email', senderDisplay: 'Owner', fromDisplay: 'owner@example.test',
    timestamp: '2027-01-15', bodyText: 'Allowed Shared content',
  }, messages: [{ id: 'M'.repeat(22), authorDisplayName: 'Owner', authorRole: 'Cuevion user',
    text: 'Shared fixture reply', timestamp: 1800000000000 }] };
const ok = data => ({ status: 200, body: { ok: true, data } });
const failure = (code, status = 401) => ({ status, body: { ok: false, error: { code } } });
const sessionOk = () => ok({ session, csrfToken: 'C'.repeat(43) });
const readOk = () => ok({ collaboration });
function deferred() { let resolve; const promise = new Promise(done => { resolve = done; }); return { promise, resolve }; }
let browser;
let passed = 0;
try {
  browser = await chromium.launch({ channel: 'chrome', headless: true });
  async function test(name, run) {
    const context = await browser.newContext();
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const calls = [], errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let authority = 'active', override = null;
    await page.addInitScript(() => {
      window.guestIntervals = 0;
      const original = window.setInterval;
      window.setInterval = (...args) => { window.guestIntervals++; return original(...args); };
    });
    await page.route('**/*', async route => {
      const request = route.request();
      assert.equal(new URL(request.url()).origin, origin, 'only local fixture traffic allowed');
      if (new URL(request.url()).pathname !== '/api/collaboration/guest') return route.continue();
      const op = request.method() === 'GET' ? 'read' : request.postDataJSON().operation;
      calls.push(op);
      let response = override ? await override(op) : null;
      if (!response) {
        if (op === 'exchange') {
          response = authority === 'revoked' ? failure('invitation_revoked', 410) : sessionOk();
          if (response.status === 200) authority = 'active';
        } else if (authority !== 'active') response = failure(authority === 'missing' ? 'session_missing' : 'session_revoked');
        else response = op === 'bootstrap' ? sessionOk() : op === 'logout' ? ok({ loggedOut: true }) : readOk();
      }
      if (response.abort) return route.abort('failed');
      await route.fulfill({ status: response.status, contentType: 'application/json',
        headers: { 'Cache-Control': 'no-store' }, body: JSON.stringify(response.body) });
    });
    const ready = () => page.getByRole('heading', { name: 'Shared activity', exact: true }).waitFor();
    const ended = async () => {
      await page.getByRole('heading', { name: 'Session ended', exact: true }).waitFor();
      assert.equal(await page.getByText('Access to this collaboration is no longer active.', { exact: true }).count(), 1);
      for (const text of ['Shared activity', 'Shared fixture email', 'Shared fixture reply', 'Send reply', 'Leave collaboration'])
        assert.equal(await page.getByText(text, { exact: true }).count(), 0, `${text} removed`);
      assert.equal(await page.locator('textarea').count(), 0);
      assert.equal(await page.getByRole('button', { name: 'Try again' }).count(), 0);
    };
    const submit = async () => {
      await page.locator('textarea').fill('Guest fixture draft');
      await page.getByRole('button', { name: 'Send reply', exact: true }).click();
    };
    // Deterministic document events avoid headless/OS focus assumptions.
    const visibility = value => page.evaluate(value => {
      Object.defineProperty(document, 'visibilityState', { configurable: true, value });
      document.dispatchEvent(new Event('visibilitychange'));
    }, value);
    try {
      await run({ page, calls, ready, ended, submit, visibility,
        boot: () => page.goto(`${origin}/#collab_guest`),
        setAuthority: value => { authority = value; }, setOverride: value => { override = value; } });
      assert.deepEqual(errors, []);
      assert.equal(await page.evaluate(() => window.guestIntervals), 0, 'no intervals');
      passed++; console.log(`PASS ${name}`);
    } finally { await context.close(); }
  }
  await test('valid invitation -> exchange -> Shared UI', async t => {
    t.setAuthority('missing'); await t.page.goto(`${origin}/#collab_guest=${'A'.repeat(43)}`);
    await t.page.getByLabel('Your name').fill('Reviewer');
    assert.equal(new URL(t.page.url()).hash, '#collab_guest');
    await t.page.getByRole('button', { name: 'Open collaboration' }).click(); await t.ready();
    assert.deepEqual(t.calls, ['bootstrap', 'exchange', 'read']);
    assert.equal(await t.page.getByText('Allowed Shared content', { exact: true }).count(), 1);
  });
  await test('revoked invitation remains fail-closed', async t => {
    t.setAuthority('revoked'); await t.page.goto(`${origin}/#collab_guest=${'A'.repeat(43)}`);
    await t.page.getByLabel('Your name').fill('Reviewer');
    await t.page.getByRole('button', { name: 'Open collaboration' }).click();
    await t.page.getByRole('heading', { name: 'Invitation inactive', exact: true }).waitFor();
    assert.deepEqual(t.calls, ['bootstrap', 'exchange']);
  });
  await test('bootstrap and ordinary active/revoked reload', async t => {
    await t.boot(); await t.ready(); assert.deepEqual(t.calls, ['bootstrap', 'read']);
    await t.page.reload(); await t.ready();
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'bootstrap', 'read']);
    t.setAuthority('revoked'); await t.page.reload(); await t.ended();
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'bootstrap', 'read', 'bootstrap']);
  });
  for (const code of ['session_missing', 'session_expired', 'session_revoked']) {
    for (const operation of ['bootstrap', 'read', 'reply', 'logout']) {
      await test(`${operation}: ${code} immediately ends access`, async t => {
        if (['bootstrap', 'read'].includes(operation)) {
          t.setOverride(op => op === operation ? failure(code) : null); await t.boot();
        } else {
          await t.boot(); await t.ready(); t.setOverride(op => op === operation ? failure(code) : null);
          if (operation === 'reply') await t.submit();
          else await t.page.getByRole('button', { name: 'Leave collaboration' }).click();
        }
        await t.ended();
        assert.equal(t.calls.filter(op => op === 'bootstrap').length, 1, 'no recovery after terminal failure');
      });
    }
  }
  for (const [label, response, title] of [
    ['503', failure('service_unavailable', 503), 'Temporarily unavailable'],
    ['500', failure('internal_error', 500), 'Temporarily unavailable'],
    ['network', { abort: true }, 'Couldn’t complete that request'],
    ['400', failure('invalid_request', 400), 'Link unavailable'],
    ['403', failure('origin_rejected', 403), 'Temporarily unavailable'],
    ['409', failure('conflict', 409), 'Couldn’t complete that request'],
    ['429', failure('rate_limited', 429), 'Please wait'],
    ['malformed 401', { status: 401, body: { unexpected: true } }, 'Temporarily unavailable'],
    ['mismatched status', failure('session_revoked', 500), 'Temporarily unavailable'],
  ]) {
    await test(`${label} is not terminal access`, async t => {
      await t.boot(); await t.ready(); t.setOverride(op => op === 'reply' ? response : null);
      await t.submit(); await t.page.getByRole('heading', { name: title, exact: true }).waitFor();
      assert.equal(await t.page.getByText('Session ended', { exact: true }).count(), 0);
      if (['network', '409', '429'].includes(label)) {
        t.setOverride(null); await t.page.getByRole('button', { name: 'Try again' }).click(); await t.ready();
        assert.equal(await t.page.locator('textarea').inputValue(), 'Guest fixture draft');
      }
    });
  }
  await test('CSRF recovery keeps draft without resending', async t => {
    await t.boot(); await t.ready(); t.setOverride(op => op === 'reply' ? failure('csrf_failed', 403) : null);
    await t.submit(); await t.page.getByText('Your session was refreshed.', { exact: false }).waitFor();
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'reply', 'bootstrap']);
    assert.equal(await t.page.locator('textarea').inputValue(), 'Guest fixture draft');
  });
  await test('foreground revoke, hidden and visible deduplication', async t => {
    await t.boot(); await t.ready();
    await t.visibility('visible'); await t.visibility('hidden'); await t.visibility('hidden');
    assert.deepEqual(t.calls, ['bootstrap', 'read']);
    const pending = deferred(), started = deferred();
    t.setOverride(op => { if (op === 'read') { started.resolve(); return pending.promise; } return null; });
    await t.visibility('visible'); await started.promise;
    await t.visibility('visible'); await t.visibility('hidden'); await t.visibility('visible');
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'read']);
    pending.resolve(failure('session_revoked')); await t.ended();
    await t.visibility('hidden'); await t.visibility('visible');
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'read']);
  });
  await test('foreground reuses reply in flight', async t => {
    await t.boot(); await t.ready(); const pending = deferred(), started = deferred();
    t.setOverride(op => { if (op === 'reply') { started.resolve(); return pending.promise; } return null; });
    await t.submit(); await started.promise; await t.visibility('hidden'); await t.visibility('visible');
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'reply']);
    pending.resolve(failure('session_revoked')); await t.ended();
  });
  await test('valid foreground read may settle hidden without follow-up traffic', async t => {
    await t.boot(); await t.ready();
    const pending = deferred(), started = deferred();
    t.setOverride(op => { if (op === 'read') { started.resolve(); return pending.promise; } return null; });
    await t.visibility('hidden'); await t.visibility('visible'); await started.promise;
    await t.visibility('hidden'); pending.resolve(readOk());
    await t.page.waitForLoadState('networkidle'); await t.ready();
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'read']);
    await t.visibility('hidden');
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'read']);
  });
  await test('CSRF recovery terminal bootstrap ends access', async t => {
    await t.boot(); await t.ready();
    t.setOverride(op => op === 'reply' ? failure('csrf_failed', 403) : failure('session_revoked'));
    await t.submit(); await t.ended();
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'reply', 'bootstrap']);
  });
  for (const lateOperation of ['read', 'reply', 'logout', 'recovery']) {
    await test(`late ${lateOperation} cannot resurrect ended access`, async t => {
      await t.boot(); await t.ready();
      const read = deferred(), mutation = deferred(), readStarted = deferred(), mutationStarted = deferred();
      t.setOverride(op => {
        if (op === 'read') { readStarted.resolve(); return read.promise; }
        if (op === 'reply' && lateOperation === 'recovery') return failure('csrf_failed', 403);
        mutationStarted.resolve(); return mutation.promise;
      });
      await t.visibility('hidden'); await t.visibility('visible'); await readStarted.promise;
      if (lateOperation === 'logout') await t.page.getByRole('button', { name: 'Leave collaboration' }).click();
      else await t.submit();
      await mutationStarted.promise;
      if (lateOperation === 'read') mutation.resolve(failure('session_revoked'));
      else read.resolve(failure('session_revoked'));
      await t.ended();
      if (lateOperation === 'read') read.resolve(readOk());
      else mutation.resolve(lateOperation === 'logout' ? ok({ loggedOut: true }) : lateOperation === 'recovery' ? sessionOk() : readOk());
      await t.page.waitForLoadState('networkidle'); await t.ended();
    });
  }
  await test('successful logout remains compatible', async t => {
    await t.boot(); await t.ready(); await t.page.getByRole('button', { name: 'Leave collaboration' }).click();
    await t.page.getByRole('heading', { name: 'Collaboration closed' }).waitFor();
    await t.visibility('hidden'); await t.visibility('visible');
    assert.deepEqual(t.calls, ['bootstrap', 'read', 'logout']);
  });
  console.log(`${passed} guest browser scenarios passed`);
} finally { await browser?.close(); await new Promise(resolve => server.close(resolve)); }
