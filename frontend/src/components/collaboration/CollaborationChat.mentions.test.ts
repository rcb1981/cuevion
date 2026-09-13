import assert from 'node:assert/strict';
import { test } from 'node:test';
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import ts from 'typescript';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { CollaborationChatTimeline } from './CollaborationChat';
import type { CollaborationMentionDraft } from '../../lib/collaborationMentions';

const emma = { userId: `usr_${'A'.repeat(22)}`, displayName: 'Emma Stone' };
const david = { userId: `usr_${'B'.repeat(21)}A`, displayName: 'David Cole' };
const mention = { userId: emma.userId, start: 0, end: 11, displayText: '@Emma Stone' };
const empty = () => ({ text: '', mentions: [] });
const source = fs.readFileSync(path.join(__dirname, 'CollaborationChat.tsx'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;

// Execute the actual component's event handlers with a small hook/textarea host.
// No application graph, DOM dependency, server or protected files are loaded.
function host(sharedDraft: CollaborationMentionDraft = empty(), internalDraft: CollaborationMentionDraft = empty()) {
  let slots: any[] = [], cursor = 0, effects: (() => void)[] = [], tree: any;
  const input = { value: '', selectionStart: 0, selectionEnd: 0, focused: false,
    focus() { this.focused = true; }, setSelectionRange(start: number, end: number) { this.selectionStart = start; this.selectionEnd = end; } };
  const hookReact = { useState(initial: any) { const slot = cursor++; if (!(slot in slots)) slots[slot] = initial;
    return [slots[slot], (value: any) => { slots[slot] = typeof value === 'function' ? value(slots[slot]) : value; }]; },
    useRef(initial: any) { const slot = cursor++; return slots[slot] ??= { current: initial }; },
    useId() { return 'test-mentions'; }, useLayoutEffect(effect: () => void) { effects.push(effect); } };
  const exports: any = {};
  vm.runInNewContext(compiled, { exports, require: (id: string) => id === 'react' ? hookReact : require(id) });
  let sends = 0, reopens = 0;
  const channel = (draft: CollaborationMentionDraft) => ({ draft, sending: false, error: null as string | null, canRetry: false,
    onChange(next: CollaborationMentionDraft) { this.draft = next; }, onSend() { sends++; } });
  const props: any = { shared: channel(sharedDraft), internal: channel(internalDraft), mentionCandidates: [emma, david] };
  function nodes(node = tree): any[] { if (!node || typeof node !== 'object') return []; if (Array.isArray(node)) return node.flatMap(child => nodes(child)); return [node, ...nodes(node.props?.children ?? null)]; }
  function render() {
    cursor = 0; effects = []; tree = exports.CollaborationChatComposer(props);
    for (const node of nodes()) {
      if (node.type === 'textarea') { input.value = node.props.value; node.ref.current = input; }
      else if (node.ref) node.ref.current = { scrollIntoView() {} };
    }
    effects.forEach(effect => effect()); return tree;
  }
  const textarea = () => nodes().find(node => node.type === 'textarea');
  const options = () => nodes().filter(node => node.props?.role === 'option');
  function edit(value: string, caret = value.length) { input.value = value; input.selectionStart = input.selectionEnd = caret; textarea().props.onChange({ currentTarget: input }); render(); }
  function key(key: string, composing = false) { let prevented = false; textarea().props.onKeyDown({ key, keyCode: 0, nativeEvent: { isComposing: composing }, preventDefault() { prevented = true; } }); render(); return prevented; }
  function mode(value: 'shared' | 'internal') { nodes().find(node => node.type === 'button' && (node.props.children === value || node.props.children?.includes(value === 'shared' ? 'Shared' : 'Internal')))?.props.onClick(); render(); }
  function clickOption(index = 0, pointerType = 'mouse') { let prevented = false; const option = options()[index]; option.props.onPointerDown({ pointerType, preventDefault() { prevented = true; } }); assert.equal(prevented, true); option.props.onClick(); render(); }
  render();
  return { props, input, nodes, render, textarea, options, edit, key, mode, clickOption, sends: () => sends,
    resolve() { props.resolved = { canReopen: true, pending: false, error: null, onReopen() { reopens++; } }; render(); }, reopens: () => reopens };
}

test('typing @ opens an accessible bounded list and @em filters it', () => {
  const h = host(); h.edit('@'); assert.equal(h.options().length, 2);
  assert.equal(h.textarea().props['aria-expanded'], true); assert.equal(h.options()[0].props['aria-selected'], true);
  assert.equal(h.textarea().props['aria-activedescendant'], h.options()[0].props.id);
  h.edit('@em'); assert.equal(h.options().length, 1); assert.equal(h.options()[0].props.children, emma.displayName);
  h.edit('emma@example.test'); assert.equal(h.options().length, 0);
});
test('ArrowDown/Up move highlight, Enter inserts exact candidate and restores focus/caret', () => {
  const h = host(); h.edit('@'); assert.equal(h.key('ArrowDown'), true); assert.equal(h.options()[1].props['aria-selected'], true);
  assert.equal(h.key('ArrowUp'), true); assert.equal(h.options()[0].props['aria-selected'], true);
  h.key('ArrowDown'); assert.equal(h.key('Enter'), true);
  assert.deepEqual(h.props.shared.draft, { text: '@Emma Stone ', mentions: [mention] });
  assert.equal(h.input.focused, true); assert.equal(h.input.selectionStart, 12); assert.equal(h.input.selectionEnd, 12);
  assert.equal(h.options().length, 0); assert.equal(h.sends(), 0);
});
test('Tab selects only when active and ordinary Enter/Tab remain unhandled when closed', () => {
  const h = host(); assert.equal(h.key('Enter'), false); assert.equal(h.key('Tab'), false);
  h.edit('@em'); assert.equal(h.key('Tab'), true); assert.equal(h.props.shared.draft.mentions[0].userId, emma.userId);
  assert.equal(h.key('Enter'), false); assert.equal(h.key('Tab'), false);
});
test('Escape closes without changing text or existing spans and does not trap focus', () => {
  const draft = { text: '@Emma Stone @', mentions: [mention] }; const h = host(draft);
  h.edit(draft.text); assert.equal(h.options().length, 2); assert.equal(h.key('Escape'), true);
  assert.equal(h.options().length, 0); assert.deepEqual(h.props.shared.draft, draft); assert.equal(h.key('Tab'), false);
});
for (const pointer of ['mouse', 'touch']) test(`${pointer} click preserves replacement location and metadata`, () => {
  const h = host(); h.edit('😀 hi @em later', 9); h.clickOption(0, pointer);
  assert.deepEqual(h.props.shared.draft, { text: '😀 hi @Emma Stone later', mentions: [{ ...mention, start: 6, end: 17 }] });
  assert.equal(h.input.selectionStart, 17); assert.equal(h.input.focused, true);
});
test('Shared/Internal switches and edits preserve independent text and spans', () => {
  const h = host(); h.edit('@em'); h.key('Tab'); const shared = h.props.shared.draft;
  h.mode('internal'); h.edit('@da'); h.key('Enter'); const internal = h.props.internal.draft;
  assert.equal(internal.text, '@David Cole '); assert.equal(internal.mentions[0].userId, david.userId);
  assert.deepEqual(h.props.shared.draft, shared); h.mode('shared'); assert.equal(h.textarea().props.value, shared.text);
  h.edit('Changed @Emma Stone '); assert.deepEqual(h.props.internal.draft, internal);
  h.mode('internal'); assert.deepEqual(h.props.internal.draft, internal);
});
test('composition Enter never selects; committed text delta invalidates touched span', () => {
  const h = host({ text: '@Emma Stone', mentions: [mention] });
  h.textarea().props.onCompositionStart(); h.render(); h.edit('@Em😀ma Stone');
  assert.deepEqual(h.props.shared.draft.mentions, []); assert.equal(h.key('Enter', true), false);
  h.textarea().props.onCompositionEnd({ currentTarget: h.input }); h.render();
  h.edit('@em'); assert.equal(h.options().length, 1);
});
test('blur and moving caret away close picker, resolving removes controls and Reopen works', () => {
  const h = host(); h.edit('@em'); h.textarea().props.onBlur(); h.render(); assert.equal(h.options().length, 0);
  h.edit('@em'); h.input.selectionStart = h.input.selectionEnd = 0; h.textarea().props.onSelect({ currentTarget: h.input }); h.render(); assert.equal(h.options().length, 0);
  h.edit('@em'); h.resolve(); assert.equal(h.textarea(), undefined); assert.equal(h.options().length, 0);
  h.nodes().find(node => node.type === 'button' && node.props.children === 'Reopen collaboration').props.onClick(); assert.equal(h.reopens(), 1);
  delete h.props.resolved; h.render(); assert.ok(h.textarea()); assert.equal(h.textarea().props.value, '@em');
});

const base = { id: 'A'.repeat(22), authorUserId: emma.userId, authorRole: 'Cuevion user' as const, authorDisplayName: 'Emma', timestamp: 1_800_000_000_000, visibility: 'shared' as const };
for (const [name, text, mentions, visibility, count] of [
  ['persisted mention', '@Emma Stone', [mention], 'shared', 1],
  ['two users', '@Emma Stone + @David Cole', [mention, { userId: david.userId, start: 14, end: 25, displayText: '@David Cole' }], 'shared', 2],
  ['repeated same user', '@Emma Stone @Emma Stone', [mention, { ...mention, start: 12, end: 23 }], 'shared', 2],
  ['historical @text', '@Emma Stone', undefined, 'shared', 0],
  ['edited plain @text', '@Emma Stone', [], 'shared', 0],
  ['supplementary Unicode', '😀@Emma Stone😀', [{ ...mention, start: 2, end: 13 }], 'shared', 1],
  ['unusable span', '@Emma Stone', [{ ...mention, end: 100 }], 'shared', 0],
  ['Internal Note', '@Emma Stone', [mention], 'internal', 1],
  ['guest-visible text with no metadata', '@Emma Stone', undefined, 'shared', 0],
] as const) test(`timeline markup: ${name}`, () => {
  const html = renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages: [{ ...base, text, mentions: mentions as any, visibility }], currentCanonicalUserId: emma.userId, activityRefs: { current: {} }, highlightedId: null, formatTimestamp: String }));
  assert.equal((html.match(/data-collaboration-mention=/g) ?? []).length, count);
  assert.ok(html.includes('data-collaboration-alignment="right"'));
  if (visibility === 'internal') assert.ok(html.includes('Internal note'));
});
test('React safely escapes HTML-shaped labels and body text', () => {
  const text = '@<script>alert(1)</script>';
  const html = renderToStaticMarkup(React.createElement(CollaborationChatTimeline, { messages: [{ ...base, text, mentions: [{ ...mention, end: text.length, displayText: text }] }], currentCanonicalUserId: null, activityRefs: { current: {} }, highlightedId: null, formatTimestamp: String }));
  assert.ok(html.includes('&lt;script&gt;')); assert.ok(!html.includes('<script>'));
});
