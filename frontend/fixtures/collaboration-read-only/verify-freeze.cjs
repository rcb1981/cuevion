// Exact, non-protected frontend paths only. No backend access or broad discovery.
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const { execFileSync } = require('node:child_process');
const ts = require('../../../node_modules/typescript');
const root = path.resolve(__dirname, '../../..');
const baseline = '070796b877b8e4e1bc8ac75b6b92373af04163b4';
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
const presentationOnly=current
 .replace(/\n[^\n]*data-collaboration-header-status[^\n]*/, '')
 .replace(/                    resolved=\{activeCollaborationOwnerProjection.state === "resolved" \? \{[\s\S]*?                    \} : undefined\}\n/, '');
same('Entire WorkspaceShell except resolved header and composer presentation props',previous,presentationOnly);
const oldDeclarations=declarations(previous), newDeclarations=declarations(current);
for (const name of [
 'submitCollaborationOwnerSharedMessage','submitCollaborationOwnerInternalNote','transitionCanonicalCollaboration',
 'beginCollaborationOwnerRead','requestCloseCollaborationOverlay','applyCanonicalCollaborationAccessResult',
 'getCollaborationSummary','renderMessageCollaboration','getCollaborationOpenBinding','getActiveCollaborationSummary','hasActiveCollaborationPriority','isCollaborationPriorityEntrySuppressed','renderThreadMessage',
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
 'frontend/src/lib/useCollaborationSummaries.ts',
 'frontend/src/components/collaboration/CollaborationContextControls.tsx',
]) {
 same(file,execFileSync('git',['show',`${baseline}:${file}`],{cwd:root,encoding:'utf8'}),fs.readFileSync(path.join(root,file),'utf8'));
}
const oldControls=execFileSync('git',['show',`${baseline}:frontend/src/components/collaboration/CollaborationContextControls.tsx`],{cwd:root,encoding:'utf8'});
const newControls=fs.readFileSync(path.join(root,'frontend/src/components/collaboration/CollaborationContextControls.tsx'),'utf8');
const dialog=s=>s.slice(s.indexOf('    {confirmOpen ? <dialog'),s.indexOf('    </dialog> : null}')+'    </dialog> : null}'.length);
same('Resolve confirmation markup and handlers',dialog(oldControls),dialog(newControls));
for(const [file,names] of [
 ['frontend/src/components/collaboration/CollaborationChat.tsx',['isOwnCollaborationActivity','CollaborationChatTimeline','CollaborationLock']],
 ['frontend/src/components/collaboration/CollaborationAccessPanel.tsx',['confirmRevoke','copySecureLink','clearSecureLink','secureLinkPanel','applyNewInvitation','submitStart','getEligibleCollaborationTeamMembers']],
]) {
 const before=declarations(execFileSync('git',['show',baseline+':'+file],{cwd:root,encoding:'utf8'}));
 const after=declarations(fs.readFileSync(path.join(root,file),'utf8'));
 for(const name of names){assert.equal(before.get(name)?.length,1);assert.equal(after.get(name)?.length,1);same(name,before.get(name)[0],after.get(name)[0]);}
}
fs.writeFileSync(path.join(__dirname,'freeze-results.json'),JSON.stringify({baseline,passed:results.length,checks:results},null,2)+'\n');
console.log(`PASS ${results.length} baseline freeze checks`);
