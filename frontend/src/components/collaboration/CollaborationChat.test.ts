import assert from 'node:assert/strict';
import { test } from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import 'sucrase/register/tsx.js';
import { CollaborationChatTimeline, CollaborationChatComposer, isOwnCollaborationActivity } from './CollaborationChat';
import type { CollaborationOwnerReadMessage } from '../../lib/collaborationOwnerReadApi';

const me = `usr_${'A'.repeat(22)}`;
const entry: CollaborationOwnerReadMessage = { id: 'A'.repeat(22), authorRole: 'Cuevion user', authorUserId: me, authorDisplayName: 'Same name', visibility: 'shared', text: 'Hello', timestamp: 1_800_000_000_000 };
for (const [name, overrides, current, own] of [
  ['my Shared', {}, me, true],
  ['my Internal', { visibility: 'internal' }, me, true],
  ['other Shared', { authorUserId: `usr_${'B'.repeat(21)}A` }, me, false],
  ['other Internal', { authorUserId: `usr_${'B'.repeat(21)}A`, visibility: 'internal' }, me, false],
  ['guest', { authorRole: 'Guest reviewer', authorUserId: null }, me, false],
  ['historical same display name', { authorUserId: null }, me, false],
  ['historical same email', { authorUserId: null, authorDisplayName: 'owner@example.test' }, me, false],
  ['historical Internal', { authorUserId: null, visibility: 'internal' }, me, false],
  ['unknown viewer', {}, null, false],
  ['both IDs null', { authorUserId: null }, null, false],
  ['non-user role cannot align right even with matching ID', { authorRole: 'System' }, me, false],
] as const) test(name, () => assert.equal(isOwnCollaborationActivity({ ...entry, ...overrides }, current), own));

test('chronological DOM order, canonical IDs, sender, exact highlight and machine-readable time', () => {
  const messages = [entry, { ...entry, id: 'B'.repeat(22), timestamp: entry.timestamp + 1000, authorUserId: null }];
  const html = renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages, currentCanonicalUserId: me, activityRefs: { current: {} }, highlightedId: messages[1].id, formatTimestamp: t => String(t) }));
  assert.ok(html.indexOf(messages[0].id) < html.indexOf(messages[1].id));
  assert.equal((html.match(/data-collaboration-activity-id=/g) ?? []).length, 2);
  assert.equal((html.match(/data-notification-highlighted="true"/g) ?? []).length, 1);
  assert.ok(html.includes('dateTime="2027-01-15T08:00:00.000Z"'));
  assert.ok(html.includes('aria-label="Same name"'));
  assert.equal((html.match(/tabindex="-1"/g) ?? []).length, 2);
});
test('internal notes and guests have readable context without technical role strings', () => {
  const html = renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages: [{ ...entry, visibility: 'internal' }, { ...entry, id: 'G'.repeat(22), authorRole: 'Guest reviewer', authorUserId: null }], currentCanonicalUserId: me, activityRefs: { current: {} }, highlightedId: null, formatTimestamp: String }));
  assert.ok(html.includes('Internal note')); assert.ok(html.includes('<svg')); assert.ok(html.includes('>Guest</span>'));
  for (const jargon of ['Cuevion user', 'Guest reviewer', 'Team only', 'Never visible']) assert.equal(html.includes(jargon), false);
});
test('one composer defaults Shared with no internal draft or privacy cue in markup', () => {
  const props = { sending: false, canRetry: false, error: null, onChange() {}, onSend() {} };
  const html = renderToStaticMarkup(React.createElement(CollaborationChatComposer, { shared: { ...props, draft: 'Public draft' }, internal: { ...props, draft: 'Secret draft' } }));
  assert.equal((html.match(/<textarea/g) ?? []).length, 1); assert.ok(html.includes('Public draft')); assert.ok(!html.includes('Secret draft')); assert.ok(!html.includes('Only your Cuevion team'));
  assert.ok(html.includes('data-collaboration-composer-mode="shared"'));
});
test('plain empty state', () => {
  const html = renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages: [], currentCanonicalUserId: me, activityRefs: { current: {} }, highlightedId: null, formatTimestamp: String }));
  assert.ok(html.includes('No messages yet.')); assert.ok(!html.includes('role="article"'));
});

test('an out-of-Date-range legacy timestamp does not crash the timeline', () => {
  assert.doesNotThrow(() => renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages: [{ ...entry, timestamp: Number.MAX_SAFE_INTEGER }], currentCanonicalUserId: me, activityRefs: { current: {} }, highlightedId: null, formatTimestamp: String })));
});
