import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import { EMPTY_COLLABORATION_MENTION_DRAFT } from '../../lib/collaborationMentions';

const source = fs.readFileSync(path.join(__dirname, 'WorkspaceShell.tsx'), 'utf8');
const start = source.indexOf('  const submitCollaborationOwnerSharedMessage = () =>');
const end = source.indexOf('  const sendCollaborationReply = (', start);
assert.ok(start > 0 && end > start);
const handlers = ts.transpileModule(source.slice(start, end), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.None } }).outputText;
const userId = `usr_${'A'.repeat(22)}`;
const sharedDraft = { text: '@Emma Stone', mentions: [{ userId, start: 0, end: 11, displayText: '@Emma Stone' }] };
const internalDraft = { text: 'ask @Emma Stone', mentions: [{ userId, start: 4, end: 15, displayText: '@Emma Stone' }] };
const tick = async () => { for (let i = 0; i < 10; i++) await Promise.resolve(); };

function host() {
  const prepared: any[] = [], executions: any[] = [];
  let nextResult: any = { status: 'conflict' }, release: ((value: any) => void) | null = null;
  const state: any = {
    EMPTY_COLLABORATION_MENTION_DRAFT,
    activeCollaborationMessageId: 'mail-1', activeCollaborationSourceMailboxId: 'box-1',
    collaborationOwnerProjection: { status: 'success', identityKey: 'identity', collaboration: { collaborationId: 'A'.repeat(22), mailboxId: 'box-1', messages: [] } },
    collaborationOwnerProjectionRequestRef: { current: { identityKey: 'identity', requestId: 1, inFlight: false, messageId: 'mail-1', sourceMailboxId: 'box-1' } },
    collaborationOwnerProjectionGenerationRef: { current: 1 }, collaborationOwnerSharedMessageGenerationRef: { current: 1 },
  };
  for (const mode of ['SharedMessage', 'InternalNote']) {
    state[`collaborationOwner${mode}Draft`] = mode === 'SharedMessage' ? sharedDraft : internalDraft;
    state[`collaborationOwner${mode}RequestRef`] = { current: null };
    state[`collaborationOwner${mode}State`] = { status: 'idle' };
    state[`setCollaborationOwner${mode}Draft`] = (value: any) => { state[`collaborationOwner${mode}Draft`] = value; };
    state[`setCollaborationOwner${mode}State`] = (value: any) => { state[`collaborationOwner${mode}State`] = value; };
    state[`prepare${mode === 'SharedMessage' ? 'Shared' : 'Internal'}CollaborationMessageForOwner`] = (id: string, text: string, mentions: any) => {
      const snapshot = { id, text, mentions: JSON.parse(JSON.stringify(mentions)), mode };
      prepared.push(snapshot);
      return { status: 'ready', operation: { execute() { executions.push(snapshot); return nextResult === 'pending' ? new Promise(resolve => { release = resolve; }) : Promise.resolve(nextResult); } } };
    };
  }
  state.setCollaborationOwnerProjection = (update: (current: any) => any) => { state.collaborationOwnerProjection = update(state.collaborationOwnerProjection); };
  // Only these two extracted owner handlers execute; the workspace import graph is never evaluated.
  const api = vm.runInNewContext(`with (state) { ${handlers}\n ({ shared: submitCollaborationOwnerSharedMessage, internal: submitCollaborationOwnerInternalNote }); }`, { state });
  return { state, api, prepared, executions, result(value: any) { nextResult = value; }, finish(value: any) { assert.ok(release); release(value); } };
}
for (const [mode, name, other] of [['shared', 'SharedMessage', 'InternalNote'], ['internal', 'InternalNote', 'SharedMessage']] as const) {
  test(`${mode} success clears only its text/spans and appends authoritative activity`, async () => {
    const h = host(); const originalOther = h.state[`collaborationOwner${other}Draft`];
    const activity = { id: 'server-message', text: 'server response', mentions: [sharedDraft.mentions[0]] };
    h.result({ status: 'success', message: activity, updatedAt: 123 }); h.api[mode](); await tick();
    assert.equal(h.prepared.length, 1); assert.equal(h.executions.length, 1);
    assert.deepEqual(h.state[`collaborationOwner${name}Draft`], EMPTY_COLLABORATION_MENTION_DRAFT);
    assert.equal(h.state[`collaborationOwner${other}Draft`], originalOther);
    assert.equal(h.state.collaborationOwnerProjection.collaboration.messages[0], activity);
    assert.equal(h.state.collaborationOwnerProjection.collaboration.updatedAt, 123);
  });
  test(`${mode} stale target rejection preserves exact pair; explicit retry reuses operation`, async () => {
    const h = host(); const original = h.state[`collaborationOwner${name}Draft`];
    h.api[mode](); await tick(); assert.equal(h.state[`collaborationOwner${name}Draft`], original);
    assert.equal(h.state[`collaborationOwner${name}State`].status, 'failure'); assert.equal(h.executions.length, 1);
    h.api[mode](); await tick(); assert.equal(h.prepared.length, 1); assert.equal(h.executions.length, 2);
    assert.equal(h.executions[0], h.executions[1]); assert.deepEqual(h.prepared[0].mentions, original.mentions);
  });
  test(`${mode} in-flight request is fenced against double-send and subsequent draft replacement`, async () => {
    const h = host(); h.result('pending'); const original = h.state[`collaborationOwner${name}Draft`];
    h.api[mode](); h.state[`collaborationOwner${name}Draft`] = { text: 'changed', mentions: [] }; h.api[mode]();
    assert.equal(h.prepared.length, 1); assert.equal(h.executions.length, 1); assert.equal(h.executions[0].text, original.text);
    assert.deepEqual(h.executions[0].mentions, original.mentions); h.finish({ status: 'conflict' }); await tick();
    assert.equal(h.state[`collaborationOwner${name}Draft`].text, 'changed');
  });
}
test('live picker projection has no roster/guest input and live sends never use extraction', () => {
  assert.ok(source.includes('mentionCandidates={projectedCollaborationMentionCandidates(activeCollaborationOwnerProjection.participants)}'));
  for (const forbidden of ['extractCollaborationMentions', 'getCollaborationMentionTargets', 'currentUserEmail', 'setServerNotifications', 'notificationsApi']) assert.equal(handlers.includes(forbidden), false);
  assert.ok(source.includes('const mentions = workspaceDataMode === "demo" ? extractCollaborationMentions('));
  assert.ok(source.includes('const mentionCandidates = isDemoWorkspace ? getCollaborationMentionTargets(inviteParticipants, []) : [];'));
});
