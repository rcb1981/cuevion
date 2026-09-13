import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  collaborationMentionCandidates, collaborationMentionKey, collaborationMentionQuery,
  filterCollaborationMentionCandidates, insertCollaborationMention, orderCollaborationMentions,
  reconcileCollaborationMentionDraft, segmentCollaborationMentions,
  type CollaborationMentionDraft,
} from './collaborationMentions';

const emma = { userId: `usr_${'A'.repeat(22)}`, displayName: 'Emma Stone' };
const owner = { userId: `usr_${'B'.repeat(21)}A`, displayName: 'Workspace Owner' };
const span = { userId: emma.userId, start: 3, end: 14, displayText: '@Emma Stone' };
const draft = { text: 'hi @Emma Stone bye', mentions: [span] };
const shifted = (delta: number) => [{ ...span, start: span.start + delta, end: span.end + delta }];
for (const [name, text, expected] of [
  ['insert before', 'Xhi @Emma Stone bye', shifted(1)],
  ['insert after', 'hi @Emma Stone bye!', [span]],
  ['insert exactly at start', 'hi X@Emma Stone bye', shifted(1)],
  ['insert exactly at end', 'hi @Emma StoneX bye', [span]],
  ['insert inside', 'hi @EmXma Stone bye', []],
  ['delete inside', 'hi @Emm Stone bye', []],
  ['replace part', 'hi @Ella Stone bye', []],
  ['delete whole', 'hi  bye', []],
  ['replace before', 'hello @Emma Stone bye', shifted(3)],
  ['replace after', 'hi @Emma Stone goodbye', [span]],
  ['paste before', 'pasted\nhi @Emma Stone bye', shifted(7)],
  ['multi-character replacement', 'hiya @Emma Stone bye', shifted(2)],
  ['supplementary insertion before shifts two units', '😀hi @Emma Stone bye', shifted(2)],
  ['supplementary insertion inside invalidates', 'hi @Em😀ma Stone bye', []],
  ['cut across mention boundary', 'himma Stone bye', []],
  ['replace all', 'fresh text', []],
] as const) test(`span: ${name}`, () => {
  const result = reconcileCollaborationMentionDraft(draft, text);
  assert.equal(result.text, text); assert.deepEqual(result.mentions, expected);
  assert.deepEqual(draft.mentions, [span], 'input draft remains untouched');
});
const second = { ...span, start: 17, end: 28 };
const twice = { text: 'hi @Emma Stone + @Emma Stone!', mentions: [span, second] };
test('edit between repeated same-user mentions shifts only the second occurrence', () => {
  assert.deepEqual(reconcileCollaborationMentionDraft(twice, 'hi @Emma Stone and @Emma Stone!').mentions, [span, { ...second, start: 19, end: 30 }]);
});
test('edit touching first removes it and shifts second', () => {
  assert.deepEqual(reconcileCollaborationMentionDraft(twice, 'hi @Emm Stone + @Emma Stone!').mentions, [{ ...second, start: 16, end: 27 }]);
});
test('emoji before and around mention uses UTF-16 indexes', () => {
  const original = { text: '😀 hi @Emma Stone 😀', mentions: [{ ...span, start: 6, end: 17 }] };
  assert.deepEqual(reconcileCollaborationMentionDraft(original, '😀 hi @Emma Stone 😀!').mentions, original.mentions);
  assert.equal(original.text.slice(6, 17), '@Emma Stone');
});
test('manual restoration and undo-like text changes never recreate metadata', () => {
  const changed = reconcileCollaborationMentionDraft(draft, 'hi @Emma Ston bye');
  assert.deepEqual(reconcileCollaborationMentionDraft(changed, draft.text).mentions, []);
});
test('manual @Display Name has no identity', () => {
  assert.deepEqual(reconcileCollaborationMentionDraft({ text: '', mentions: [] }, '@Emma Stone').mentions, []);
});
test('spans remain ordered without deduping occurrences', () => {
  const reversed = { ...twice, mentions: [second, span] };
  assert.deepEqual(reconcileCollaborationMentionDraft(reversed, twice.text + 'a').mentions, [span, second]);
  assert.deepEqual(orderCollaborationMentions([second, span]), [span, second]);
});
test('non-overlapping prefix/suffix handles deleting repeated characters conservatively', () => {
  assert.equal(reconcileCollaborationMentionDraft(draft, '').mentions.length, 0);
  assert.deepEqual(reconcileCollaborationMentionDraft(draft, draft.text), draft);
});

for (const text of ['@', ' @', '(@', '[@', '{@', '\n@']) test(`picker trigger ${JSON.stringify(text)}`, () => {
  assert.deepEqual(collaborationMentionQuery(text, text.length), { start: text.length - 1, end: text.length, query: '' });
});
for (const text of ['emma@example.test', 'hi@em', '@@em', '@em ', '@em\n', 'just text']) test(`picker rejects ${JSON.stringify(text)}`, () => {
  assert.equal(collaborationMentionQuery(text, text.length), null);
});
test('query filters @em and selected text does not open a picker', () => {
  assert.equal(collaborationMentionQuery('@em', 3)?.query, 'em');
  assert.deepEqual(filterCollaborationMentionCandidates([owner, emma], 'EM'), [emma]);
  assert.equal(collaborationMentionQuery('@em', 1, 3), null);
  assert.equal(collaborationMentionQuery('@em', 99), null);
});
test('only projected owner and explicit Team participant become candidates, including self', () => {
  const projection = { participants: [{ ...owner, access: 'owner' as const }, { ...emma, access: 'participant' as const }],
    externalGuests: [{ userId: 'guest', displayName: 'Guest' }], teamRoster: [{ userId: 'unrelated', displayName: 'Other' }] };
  const candidates = collaborationMentionCandidates(projection.participants);
  assert.deepEqual(candidates, [owner, emma]);
  assert.ok(candidates.some(candidate => candidate.userId === owner.userId), 'self owner stays eligible');
  assert.deepEqual(collaborationMentionCandidates(projection.participants.slice(0, 1)), [owner], 'removed Team projection disappears');
  assert.deepEqual(collaborationMentionCandidates([{ userId: 'guest', displayName: 'Guest', access: 'guest' } as never]), []);
});
test('suggestions are max eight, prefix first, contains second, name then ID', () => {
  const candidates = [{ userId: 'z', displayName: 'Gemma' }, { userId: 'b', displayName: 'Emma' }, { userId: 'a', displayName: 'Emma' },
    ...Array.from({ length: 10 }, (_, i) => ({ userId: String(i), displayName: `Emily ${i}` }))];
  const filtered = filterCollaborationMentionCandidates(candidates.reverse(), 'em');
  assert.equal(filtered.length, 8); assert.equal(filtered[0].displayName, 'Emily 0');
  assert.deepEqual(filterCollaborationMentionCandidates(candidates.slice(-3), 'em'), [
    { userId: 'a', displayName: 'Emma' }, { userId: 'b', displayName: 'Emma' }, { userId: 'z', displayName: 'Gemma' },
  ]);
});
for (const [key, action, index] of [['ArrowDown', 'move', 1], ['ArrowUp', 'move', 2], ['Enter', 'select', 0], ['Tab', 'select', 0], ['Escape', 'close', 0]] as const) test(`picker key ${key}`, () => {
  assert.deepEqual(collaborationMentionKey(key, 0, 3), { action, index });
  assert.equal(collaborationMentionKey(key, 0, 0), null);
});
test('normal Enter and unrelated keys pass through when picker is closed', () => {
  assert.equal(collaborationMentionKey('Enter', 0, 0), null);
  assert.equal(collaborationMentionKey('a', 0, 3), null);
});
for (const suffix of ['', ' ', '.', ')', '\n', 'word']) test(`selection exact replacement and trailing space with ${JSON.stringify(suffix)}`, () => {
  const input = { text: `😀 hi @em${suffix}`, mentions: [] };
  const query = collaborationMentionQuery(input.text, 9)!;
  const result = insertCollaborationMention(input, query, emma)!;
  const needsSpace = !suffix || suffix === 'word';
  assert.equal(result.draft.text, `😀 hi @Emma Stone${needsSpace ? ' ' : ''}${suffix}`);
  assert.deepEqual(result.draft.mentions, [{ ...span, start: 6, end: 17 }]);
  assert.equal(result.caret, needsSpace ? 18 : 17);
});
test('selection preserves other spans and repeated same-user occurrences', () => {
  const input = { text: '@Emma Stone @em', mentions: [{ ...span, start: 0, end: 11 }] };
  assert.deepEqual(insertCollaborationMention(input, collaborationMentionQuery(input.text, input.text.length)!, emma)?.draft.mentions,
    [{ ...span, start: 0, end: 11 }, { ...span, start: 12, end: 23 }]);
});
test('stale query cannot insert; draft cannot exceed 32 structured occurrences', () => {
  assert.equal(insertCollaborationMention(draft, { start: 0, end: 3, query: 'em' }, emma), null);
  const text = '@Emma Stone '.repeat(32) + '@em';
  const mentions = Array.from({ length: 32 }, (_, i) => ({ ...span, start: i * 12, end: i * 12 + 11 }));
  assert.equal(insertCollaborationMention({ text, mentions }, collaborationMentionQuery(text, text.length)!, emma), null);
});

for (const [name, input, count] of [
  ['one canonical mention', draft, 1], ['two repeated mentions', twice, 2],
  ['historical @text', { text: draft.text, mentions: [] }, 0],
  ['edited and restored plain text', { text: '@Emma Stone', mentions: [] }, 0],
  ['emoji before and after', { text: '😀@Emma Stone😀', mentions: [{ ...span, start: 2, end: 13 }] }, 1],
] as const) test(`render segments: ${name}`, () => {
  const segments = segmentCollaborationMentions(input.text, input.mentions);
  assert.equal(segments.filter(segment => segment.mention).length, count);
  assert.equal(segments.map(segment => segment.text).join(''), input.text);
});
for (const [name, mentions] of [
  ['negative', [{ ...span, start: -1 }]], ['fractional', [{ ...span, start: 3.5 }]], ['beyond text', [{ ...span, end: 999 }]],
  ['wrong slice', [{ ...span, displayText: '@Someone Else' }]], ['overlap', [span, span]],
  ['null', [null]], ['missing fields', [{}]], ['reversed', [{ ...span, end: 2 }]],
] as const) test(`malformed ${name} falls back for entire text`, () => {
  assert.deepEqual(segmentCollaborationMentions(draft.text, mentions as never), [{ text: draft.text, mention: null }]);
});
test('surrogate-splitting span falls back without crashing', () => {
  const text = '@Emma😀';
  assert.deepEqual(segmentCollaborationMentions(text, [{ ...span, start: 0, end: 6, displayText: text.slice(0, 6) }]), [{ text, mention: null }]);
});
test('sparse or partially unusable metadata falls back for the whole message', () => {
  for (const mentions of [new Array(2), [span, { ...span, start: 100, end: 111 }]]) {
    assert.deepEqual(segmentCollaborationMentions(draft.text, mentions), [{ text: draft.text, mention: null }]);
  }
});
test('draft operations never mutate another mode', () => {
  const shared: CollaborationMentionDraft = draft;
  const internal: CollaborationMentionDraft = twice;
  const result = reconcileCollaborationMentionDraft(shared, 'prefix ' + shared.text);
  assert.notDeepEqual(result, shared); assert.deepEqual(internal, twice); assert.deepEqual(shared, draft);
});
