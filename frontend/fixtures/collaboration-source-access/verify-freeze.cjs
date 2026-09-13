// Exact, non-protected frontend paths only. No backend access or broad discovery.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const ts = require('../../../node_modules/typescript');
const root = path.resolve(__dirname, '../../..');
const baseline = '0982786e7781d4f00e0d6113b29346c2e679883e';
const shellPath = 'frontend/src/components/workspace/WorkspaceShell.tsx';
const previous = execFileSync('git', ['show', `${baseline}:${shellPath}`], {cwd: root, encoding:'utf8', maxBuffer:10*1024*1024});
const current = fs.readFileSync(path.join(root,shellPath),'utf8');
const results = [];
function same(label, before, after) { assert.equal(after,before,label);results.push(label); }
function declarations(source) {
 const ast=ts.createSourceFile(shellPath,source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX), found=new Map();
 function visit(node) {
  if ((ts.isFunctionDeclaration(node)||ts.isVariableDeclaration(node))&&node.name&&ts.isIdentifier(node.name)) {
   const name=node.name.text;
   if (!found.has(name)) found.set(name,[]);
   found.get(name).push(node.getText(ast));
  }
  ts.forEachChild(node,visit);
 }
 visit(ast);return found;
}
same('Entire WorkspaceShell except history getter index: C3D, mailbox polling and Priority unchanged',previous,current.replace('lookupCollaborationSummary(collaborationSummaries.historyIndex,','lookupCollaborationSummary(collaborationSummaries.index,'));
const hookPath='frontend/src/lib/useCollaborationSummaries.ts';
const oldHook=execFileSync('git',['show',baseline+':'+hookPath],{cwd:root,encoding:'utf8'});
const newHook=fs.readFileSync(path.join(root,hookPath),'utf8');
same('Hook hydration, active memoization, subscription and scope fencing unchanged',oldHook.slice(0,oldHook.indexOf('  return { scopeKey')),newHook.slice(0,newHook.indexOf('  // Source-mail history access')));
assert.ok(newHook.includes('return { scopeKey, index, historyIndex: summaries, acceptMutation };'));
results.push('History aliases canonical snapshot without scan, copy, cache or request');
const oldDeclarations=declarations(previous), newDeclarations=declarations(current);
for (const name of [
 'submitCollaborationOwnerSharedMessage','submitCollaborationOwnerInternalNote','transitionCanonicalCollaboration',
 'beginCollaborationOwnerRead','requestCloseCollaborationOverlay','applyCanonicalCollaborationAccessResult',
 'getActiveCollaborationSummary','hasActiveCollaborationPriority','isCollaborationPriorityEntrySuppressed','renderThreadMessage',
 'handleAttachmentOpen','renderAttachmentItem','EmailHtmlStage','resolveMessageBodyRenderMode','renderPlainMessageParagraph',
 'sanitizeMessageBodyHtml','sanitizeComposeGeneratedHtml','sanitizeEmailStyleContent',
 'sanitizeImportedEmailCssResources','sanitizeImportedEmailResourceAttributes','hardenImportedEmailClickOnlyLink',
]) {
 assert.equal(oldDeclarations.get(name)?.length,1,`unique baseline ${name}`);
 assert.equal(newDeclarations.get(name)?.length,1,`unique current ${name}`);
 same(name,oldDeclarations.get(name)[0],newDeclarations.get(name)[0]);
}
const effectStart='  useEffect(() => {\n    const request = serverNotificationDisplay;';
function display(source) { const start=source.indexOf(effectStart);assert.ok(start>0);return source.slice(start,source.indexOf('  // Mobile compose handoff:',start)); }
same('C3D rendered-display and mark-read ordering',display(previous),display(current));
for (const file of [
 'frontend/src/components/collaboration/CollaborationChat.tsx',
 'frontend/src/components/collaboration/CollaborationAccessPanel.tsx',
 'frontend/src/components/collaboration/ExternalCollaborationGuestView.tsx',
 'frontend/src/lib/collaborationOwnerReadApi.ts',
 'frontend/src/lib/collaborationOwnerWriteApi.ts',
 'frontend/src/lib/collaborationGuestApi.ts',
 'frontend/src/lib/collaborationGuestInviteLink.ts',
 'frontend/src/lib/notificationsApi.ts',
 'frontend/src/lib/notificationNavigation.ts',
 'frontend/src/lib/workspaceNotificationStore.ts',
 'frontend/src/lib/exactMailboxMessageApi.ts',
 'frontend/src/lib/exactMailboxMessageStore.ts',
 'frontend/src/lib/collaborationPriority.ts',
 'frontend/src/lib/collaborationSummaryApi.ts',
 'frontend/src/lib/collaborationSummaryStore.ts',
 'frontend/src/components/collaboration/CollaborationContextControls.tsx',
]) {
 same(file,execFileSync('git',['show',`${baseline}:${file}`],{cwd:root,encoding:'utf8'}),fs.readFileSync(path.join(root,file),'utf8'));
}
const oldControls=execFileSync('git',['show',`${baseline}:frontend/src/components/collaboration/CollaborationContextControls.tsx`],{cwd:root,encoding:'utf8'});
const newControls=fs.readFileSync(path.join(root,'frontend/src/components/collaboration/CollaborationContextControls.tsx'),'utf8');
const dialog=s=>s.slice(s.indexOf('    {confirmOpen ? <dialog'),s.indexOf('    </dialog> : null}')+'    </dialog> : null}'.length);
same('Resolve confirmation markup and handlers',dialog(oldControls),dialog(newControls));
fs.writeFileSync(path.join(__dirname,'freeze-results.json'),JSON.stringify({baseline,passed:results.length,checks:results},null,2)+'\n');
console.log(`PASS ${results.length} baseline freeze checks`);
