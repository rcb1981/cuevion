import assert from 'node:assert/strict';
import { afterEach, beforeEach, test } from 'node:test';
import { __resetCollaborationOwnerReadApiForTests } from './collaborationOwnerReadApi';
import { prepareSharedCollaborationMessageForOwner, prepareInternalCollaborationMessageForOwner } from './collaborationOwnerWriteApi';
import { reconcileCollaborationMentionDraft } from './collaborationMentions';

const collaborationId = 'A'.repeat(22);
const userId = `usr_${'B'.repeat(21)}A`;
const span = { userId, start: 0, end: 11, displayText: '@Emma Stone' };
const originalFetch = globalThis.fetch;
let calls: { body: any; key: string | null }[];
let appendResponse: (body: any) => Response | Promise<Response>;
const response = (status: number, value: unknown) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
const success = (body: any) => response(200, { ok: true, data: { message: {
  id: 'C'.repeat(22), authorDisplayName: 'Author', authorRole: 'Cuevion user', authorUserId: userId,
  text: body.text, visibility: body.operation === 'append_shared' ? 'shared' : 'internal', timestamp: 1_800_000_000_000,
  ...(body.mentions ? { mentions: body.mentions } : {}),
}, updatedAt: 1_800_000_000_000 } });
beforeEach(() => {
  __resetCollaborationOwnerReadApiForTests(); calls = []; appendResponse = success;
  globalThis.fetch = (async (_input, init) => {
    const body = JSON.parse(String(init?.body));
    if (body.operation === 'csrf') return response(200, { ok: true, data: { csrfToken: 'test-token', expiresAt: Math.floor(Date.now() / 1000) + 300 } });
    calls.push({ body, key: new Headers(init?.headers).get('X-Cuevion-Idempotency-Key') });
    return await appendResponse(body);
  }) as typeof fetch;
});
afterEach(() => { globalThis.fetch = originalFetch; __resetCollaborationOwnerReadApiForTests(); });

for (const [visibility, prepare] of [['shared', prepareSharedCollaborationMessageForOwner], ['internal', prepareInternalCollaborationMessageForOwner]] as const) {
  for (const [name, text, mentions] of [
    ['ordinary', 'Hello', undefined], ['manual @text', '@Emma Stone', undefined], ['empty metadata', 'Hello', []],
    ['one mention', '@Emma Stone', [span]],
    ['repeated user in canonical order', '@Emma Stone + @Emma Stone', [{ ...span, start: 14, end: 25 }, span]],
  ] as const) test(`${visibility} request: ${name}`, async () => {
    const prepared = prepare(collaborationId, text, mentions);
    assert.equal(prepared.status, 'ready'); if (prepared.status !== 'ready') return;
    assert.equal((await prepared.operation.execute()).status, 'success');
    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].body, { operation: `append_${visibility}`, collaborationId, text,
      ...(mentions?.length ? { mentions: [...mentions].sort((a, b) => a.start - b.start) } : {}) });
    assert.ok(calls[0].key);
  });
}
test('invalidated span is absent from payload with edited visible text preserved', async () => {
  const draft = reconcileCollaborationMentionDraft({ text: '@Emma Stone', mentions: [span] }, '@Emma Ston');
  const prepared = prepareSharedCollaborationMessageForOwner(collaborationId, draft.text, draft.mentions);
  assert.equal(prepared.status, 'ready'); if (prepared.status !== 'ready') return;
  await prepared.operation.execute();
  assert.deepEqual(calls[0].body, { operation: 'append_shared', collaborationId, text: '@Emma Ston' });
});
test('multiple different users retain canonical IDs and order', async () => {
  const other = { userId: `usr_${'D'.repeat(21)}A`, displayText: '@David Cole', start: 14, end: 25 };
  const prepared = prepareSharedCollaborationMessageForOwner(collaborationId, '@Emma Stone + @David Cole', [other, span]);
  assert.equal(prepared.status, 'ready'); if (prepared.status !== 'ready') return;
  await prepared.operation.execute(); assert.deepEqual(calls[0].body.mentions, [span, other]);
});
test('snapshot deep-copies spans before async bootstrap and explicit retries use original payload/key', async () => {
  const mentions = [{ ...span }];
  const prepared = prepareSharedCollaborationMessageForOwner(collaborationId, '@Emma Stone', mentions);
  assert.equal(prepared.status, 'ready'); if (prepared.status !== 'ready') return;
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  appendResponse = async body => { await gate; return success(body); };
  const pending = prepared.operation.execute();
  mentions[0].userId = 'changed'; mentions[0].start = 20; mentions.push({ ...span, end: 500 });
  release(); assert.equal((await pending).status, 'success');
  assert.equal((await prepared.operation.execute()).status, 'success');
  assert.deepEqual(calls[0].body.mentions, [span]); assert.deepEqual(calls[1], calls[0]);
});
test('stale-target rejection never strips metadata or starts a plain resend', async () => {
  appendResponse = () => response(409, { ok: false });
  const prepared = prepareInternalCollaborationMessageForOwner(collaborationId, '@Emma Stone', [span]);
  assert.equal(prepared.status, 'ready'); if (prepared.status !== 'ready') return;
  assert.equal((await prepared.operation.execute()).status, 'conflict');
  assert.equal(calls.length, 1); assert.deepEqual(calls[0].body.mentions, [span]);
  assert.equal((await prepared.operation.execute()).status, 'conflict');
  assert.deepEqual(calls[1], calls[0]);
});
