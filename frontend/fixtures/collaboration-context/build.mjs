// Local-only fixture: render the actual modal JSX and existing owner send/lifecycle handlers.
// No application entry point, credentials, backend process, or production requests.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { build } from '../../../node_modules/esbuild/lib/main.js';
import { execFileSync } from 'node:child_process';
const here = path.dirname(fileURLToPath(import.meta.url));
const frontend = path.resolve(here, '../..');
const shellPath = path.join(frontend, 'src/components/workspace/WorkspaceShell.tsx');
const shell = fs.readFileSync(shellPath, 'utf8');
function between(start, end) {
  const a = shell.indexOf(start), b = shell.indexOf(end, a);
  if (a < 0 || b < 0) throw new Error(`Missing fixture marker: ${start}`);
  return shell.slice(a, b);
}
const modal = between('              <WorkspaceModalLayer compact>', '\n              document.body,').trim().replace(/,\s*$/, '');
let template = fs.readFileSync(path.join(here, 'harness.tsx'), 'utf8');
const chunks = {
  MODAL: modal,
  READER: between('  const renderThreadMessage = (', '  const renderThreadTimeline = ('),
  ATTACHMENT_TYPES: between('  const getAttachmentType = (', '  const saveAttachmentBlob = ('),
  ATTACHMENTS: between('  const renderAttachmentItem = (', '  const renderMessageActions = ('),
  SEND: between('  const submitCollaborationOwnerSharedMessage = () =>', '  const sendCollaborationReply = ('),
  LIFECYCLE: between('  const transitionCanonicalCollaboration = async () =>', '  const syncCollaborationMentionState = ('),
  CLOSE: between('  const requestCloseCollaborationOverlay = () =>', '  const applyCanonicalCollaborationAccessResult = ('),
  DISPLAY: between('  useEffect(() => {\n    const request = serverNotificationDisplay;', '  // Mobile compose handoff:'),
  COPY: between('function getCollaborationOwnerStateLabel(', 'const primaryNavigationItems = ['),
  TIME: between('function formatCollaborationStatusTimestamp(', 'function getCollaborationMessageVisibility('),
};
for (const [name, text] of Object.entries(chunks)) template = template.replace(`/* ${name} */`, text);
const out = '/private/tmp/cuevion-c3p2b';
fs.mkdirSync(out, { recursive: true });
const readerExports = ['resolveMessageBodyRenderMode','EmailHtmlStage','renderPlainMessageParagraph','getQuotedParagraphStartIndex','parseQuotedSections','hasReliableQuotedContent','shouldShowInAttachmentList','shouldRenderKnownProviderHtmlOnLightCanvas','isComposeGeneratedHtml','resolveDesktopThreadTimestamp','normalizeSenderLearningKey','SignatureBlock'];
await build({ plugins: [{name: 'existing-reader', setup(api) {
  api.onResolve({filter: /^cuevion-fixture-reader$/}, () => ({path: shellPath, namespace: 'reader'}));
  api.onLoad({filter: /.*/, namespace: 'reader'}, () => ({contents: shell + '\nexport { ' + readerExports.join(',') + ' };', loader: 'tsx', resolveDir: path.dirname(shellPath)}));
}}], stdin: { contents: template, resolveDir: here, sourcefile: 'fixture.tsx', loader: 'tsx' }, bundle: true, outfile: path.join(out, 'fixture.js'), jsx: 'automatic', define: { 'process.env.NODE_ENV': '"development"' } });
// Exact content files only; avoid the application's broad content discovery.
execFileSync(process.execPath, [path.join(frontend, 'node_modules/tailwindcss/lib/cli.js'), '-i', path.join(frontend, 'src/index.css'), '-o', path.join(out, 'fixture.css'), '--config', path.join(frontend, 'tailwind.config.js'), '--content', [shellPath, path.join(frontend, 'src/components/collaboration/CollaborationChat.tsx'), path.join(frontend, 'src/components/collaboration/CollaborationAccessPanel.tsx'), path.join(frontend, 'src/components/collaboration/CollaborationContextControls.tsx'), path.join(here, 'harness.tsx')].join(',')], { cwd: frontend, stdio: 'inherit' });
fs.writeFileSync(path.join(out, 'index.html'), '<!doctype html><html lang="en"><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Cuevion — Collaboration local fixture</title><link rel="icon" href="data:,"><link rel="stylesheet" href="/fixture.css"><body><div id="root"></div><script src="/fixture.js"></script></body></html>');
console.log(out);
