const {spawnSync} = require('node:child_process');
const fs = require('node:fs');
const path = require('node:path');
const frontend = path.resolve(__dirname,'../..');
const suites = [
 'src/components/collaboration/CollaborationChat.test.ts',
 'src/components/collaboration/CollaborationAccessPanel.test.tsx',
 'src/components/workspace/WorkspaceShell.collaborationOwnerRead.test.ts',
 'src/lib/collaborationOwnerSourceLocator.test.ts',
 'src/lib/collaborationOwnerReadApi.test.ts',
 'src/lib/collaborationOwnerWriteApi.test.ts',
 'src/lib/collaborationGuestApi.test.ts',
 'src/App.collaborationGuestRoute.test.ts',
 'src/lib/collaborationSummaryApi.test.ts',
 'src/lib/collaborationSummaryStore.test.ts',
 'src/components/workspace/WorkspaceShell.collaborationIdentity.test.ts',
 'src/lib/notificationsApi.test.ts',
 'src/lib/notificationNavigation.test.ts',
 'src/lib/workspaceNotificationStore.test.ts',
 'src/components/workspace/WorkspaceShell.serverNotifications.test.ts',
 'src/lib/collaborationGuestInviteLink.test.ts',
 'src/components/collaboration/ExternalCollaborationGuestView.test.tsx',
 'src/lib/exactMailboxMessageApi.test.ts',
 'src/lib/exactMailboxMessageStore.test.ts',
 'src/components/workspace/WorkspaceShell.performance.test.ts',
];
const results = suites.map(file=>{
 const r=spawnSync(process.execPath,['-e',`global.React=require('react');require('./node_modules/sucrase/register/ts.js');require('./node_modules/sucrase/register/tsx.js');require('./${file}')`],{cwd:frontend,encoding:'utf8',env:{...process.env,PYTHONDONTWRITEBYTECODE:'1'}});
 const output=r.stdout+r.stderr;console.log(`${r.status===0?'PASS':'FAIL'} ${file}`);if(r.status!==0) console.log(output.slice(-4000));
 return {file,status:r.status,output};
});
fs.writeFileSync(path.join(__dirname,'regression-results.json'),JSON.stringify(results,null,2)+'\n');
process.exitCode=results.some(r=>r.status!==0)?1:0;
