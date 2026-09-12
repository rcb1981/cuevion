// Build replaces markers with exact WorkspaceShell source. This file is not an app route.
import React, { useEffect, useRef, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { getReturnedReplySenderAddress } from '../../src/lib/returnedReplyEvidence';
import { CollaborationAccessPanel } from '../../src/components/collaboration/CollaborationAccessPanel';
import { CollaborationChatTimeline, CollaborationChatComposer } from '../../src/components/collaboration/CollaborationChat';
import { prepareSharedCollaborationMessageForOwner, prepareInternalCollaborationMessageForOwner, resolveCollaborationForOwner, reopenCollaborationForOwner } from '../../src/lib/collaborationOwnerWriteApi';
import { CollaborationContextTabs, CollaborationLifecycleMenu } from '../../src/components/collaboration/CollaborationContextControls';
import { resolveMessageBodyRenderMode, EmailHtmlStage, renderPlainMessageParagraph, getQuotedParagraphStartIndex, parseQuotedSections, hasReliableQuotedContent, shouldShowInAttachmentList, shouldRenderKnownProviderHtmlOnLightCanvas, isComposeGeneratedHtml, resolveDesktopThreadTimestamp, normalizeSenderLearningKey, SignatureBlock } from 'cuevion-fixture-reader';
/* COPY */
/* TIME */
const me = `usr_${'A'.repeat(22)}`, alex = `usr_${'B'.repeat(21)}A`, dan = `usr_${'C'.repeat(21)}A`;
const now = Date.parse('2026-09-12T12:20:00Z');
const activity = (id, authorDisplayName, authorUserId, text, visibility = 'shared', authorRole = 'Cuevion user', offset = 0) => ({ id: id.repeat(22), authorDisplayName, authorRole, authorUserId, text, visibility, timestamp: now + offset * 60000 });
let canonical = {
  collaborationId: 'C'.repeat(22), mailboxId: 'main', state: 'needs_review', createdAt: now - 1000, updatedAt: now + 6 * 60000, viewerAccess: 'owner',
  source: { subject: 'September release · final master', senderDisplay: 'Alex Morgan', fromDisplay: 'alex@example.test', timestamp: new Date(now).toISOString(), bodyText: 'Can we confirm the final master for Friday?' },
  participants: [{ userId: me, displayName: 'Rutger', access: 'owner' }, { userId: alex, displayName: 'Alex Morgan', access: 'participant' }],
  externalGuests: [{ inviteId: 'G'.repeat(22), status: 'active', displayName: 'Jamie', expiresAt: 1900000000 }, { inviteId: 'H'.repeat(22), status: 'revoked', displayName: 'Previous reviewer', expiresAt: 1900000000 }],
  messages: [
    activity('A', 'Alex Morgan', alex, 'Can we use this master for the September release?'),
    activity('B', 'Rutger', me, 'Yes, this sounds good. Let’s use it.', 'shared', 'Cuevion user', 1),
    activity('D', 'Jamie', null, 'Thanks! Could you send the WAV version too?', 'shared', 'Guest reviewer', 2),
    activity('E', 'Rutger', me, 'Ask Dan about the final artwork before we confirm.', 'internal', 'Cuevion user', 3),
    activity('F', 'Alex Morgan', alex, 'I’ll check with Dan this afternoon.', 'internal', 'Cuevion user', 4),
    activity('J', 'Rutger', null, 'Earlier discussion imported from the original collaboration.', 'shared', 'Cuevion user', 5),
    activity('K', 'rutger@example.test', null, 'Historical internal context.', 'internal', 'Cuevion user', 6),
  ],
};
const fixture = window.fixture = { requests: [], failNext: false, holdNext: false, release: null, marks: [], copied: '', refs: null, projection: null };
const sourceMail = {
  id: 'mail', sender: 'Alex Morgan', from: new URLSearchParams(location.search).has('formatted-sender') ? 'Alex Morgan <alex@example.test>' : 'alex@example.test', to: 'Rutger <rutger@example.test>, Jamie <jamie@example.test>', cc: 'Dan <dan@example.test>', subject: canonical.source.subject,
  createdAt: new Date(now).toISOString(), timestamp: new Date(now).toISOString(), body: ['Hi Rutger,', 'Please review the final September master and artwork before Friday.', 'The WAV and release notes are attached.\nThanks, Alex'],
  bodyHtml: new URLSearchParams(location.search).has('plain') ? undefined : `<html><head><style>.release{max-width:600px;margin:auto;font-family:Arial,sans-serif;color:#243b30;background:#f7f5ed;padding:24px;}h1{font-size:26px;}p{line-height:1.6;}</style></head><body><div class="release"><p>SEPTEMBER RELEASE</p><h1>Ready for the final listen</h1><p>Hi Rutger,</p><p>Here’s the final master for our September release. Please check the transition into the second chorus and confirm the artwork with Dan.</p><img src="cid:artwork" alt="September artwork" width="80" height="80"><p>The WAV and release notes are attached. We’re aiming to deliver everything by Friday.</p><p><a href="https://example.test/release-notes">Read the release notes</a></p><p>Thanks,<br>Alex Morgan</p><script>window.fixtureUnsafe=true</script><img src="https://blocked.example.test/tracker.png" onerror="alert('unsafe')"></div></body></html>`,
  attachments: [{id:'cover',name:'artwork.png',contentId:'artwork',disposition:'inline',mimeType:'image/png',inlineSrc:'data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg=='},{id:'master',name:'September-final-master.wav',mimeType:'audio/wav',size:24500000},{id:'notes',name:'Release-notes.pdf',mimeType:'application/pdf',size:138000}],
};
const committed = new Map();
window.fetch = async (url, init) => {
  if (url !== '/api/collaboration/owner') throw new Error(`Fixture blocked request: ${url}`);
  const body = JSON.parse(init.body);
  fixture.requests.push({ ...body, headers: init.headers });
  if (fixture.holdNext && body.operation !== 'csrf') { fixture.holdNext = false; await new Promise(resolve => { fixture.release = resolve; }); }
  if (fixture.failNext && body.operation !== 'csrf') { fixture.failNext = false; throw new Error('Local simulated lost response'); }
  const ok = (data, status = 200) => new Response(JSON.stringify({ ok: true, data }), { status });
  if (body.operation === 'csrf') return ok({ csrfToken: 'fixture-csrf', expiresAt: Math.floor(Date.now() / 1000) + 600 });
  if (body.operation.startsWith('append_')) {
    const key = init.headers['X-Cuevion-Idempotency-Key'];
    let message = committed.get(key);
    if (!message) {
      message = { ...activity(String.fromCharCode(76 + committed.size), 'Rutger', me, body.text, body.operation === 'append_internal' ? 'internal' : 'shared'), timestamp: now + (7 + committed.size) * 60000 };
      committed.set(key, message); canonical = { ...canonical, updatedAt: message.timestamp, messages: [...canonical.messages, message] };
    }
    return ok({ message, updatedAt: canonical.updatedAt });
  }
  if (body.operation === 'add_participant') canonical = { ...canonical, participants: [...canonical.participants, { userId: body.participantUserId, displayName: 'Dan', access: 'participant' }] };
  else if (body.operation === 'resolve' || body.operation === 'reopen') { canonical = { ...canonical, updatedAt: canonical.updatedAt + 1, state: body.operation === 'resolve' ? 'resolved' : 'note_only' }; return ok({changed: true, collaboration: canonical}); }
  else if (body.operation === 'issue_guest_invite') {
    const invitation = { inviteId: 'Z'.repeat(22), collaborationId: canonical.collaborationId, status: 'active', allowedActions: ['read', 'reply'], identityAssurance: 'link_possession', expiresAt: 1900000000, ...(body.invitedEmail ? { invitedEmail: body.invitedEmail } : {}) };
    canonical = { ...canonical, externalGuests: [...canonical.externalGuests, { inviteId: invitation.inviteId, status: 'pending', expiresAt: invitation.expiresAt, ...(body.invitedEmail ? { invitedEmail: body.invitedEmail } : {}) }] };
    return ok({ invitationCreated: true, collaboration: canonical, invitation, token: 'S'.repeat(42) + 'A' }, 201);
  } else if (body.operation === 'revoke_guest_invite') {
    canonical = { ...canonical, externalGuests: canonical.externalGuests.map(g => g.inviteId === body.inviteId ? { ...g, status: 'revoked' } : g) };
    const g = canonical.externalGuests.find(g => g.inviteId === body.inviteId);
    return ok({ collaboration: canonical, invitation: g });
  } else throw new Error(`Unsupported fixture operation ${body.operation}`);
  return ok({ collaboration: canonical });
};
function WorkspaceModalLayer({ children }) { return <div className="fixed inset-0 flex items-center justify-center bg-[var(--workspace-bg)] p-3 sm:p-6">{children}</div>; }
function Harness() {
  const [open, setOpen] = useState(true);
  const [collaborationOwnerProjection, setCollaborationOwnerProjection] = useState({ status: 'success', identityKey: 'fixture', requestId: 1, collaboration: canonical });
  const activeCollaborationOwnerProjection = collaborationOwnerProjection.collaboration;
  const [isCollaborationPeopleOpen, setIsCollaborationPeopleOpen] = useState(false);
  const [collaborationContextTab, setCollaborationContextTab] = useState('conversation');
  const [hasCollaborationEmailOpened, setHasCollaborationEmailOpened] = useState(false);
  const selectCollaborationContextTab = tab => { setCollaborationContextTab(tab); if (tab === 'email') setHasCollaborationEmailOpened(true); setIsCollaborationPeopleOpen(false); };
  const [themeMode, setThemeMode] = useState(new URLSearchParams(location.search).get('theme') || 'light');
  fixture.theme = setThemeMode;
  useEffect(() => { document.documentElement.dataset.theme = themeMode; document.documentElement.classList.toggle('dark',themeMode==='dark'); },[themeMode]);
  const [isCollaborationSecureLinkVisible, setIsCollaborationSecureLinkVisible] = useState(false);
  const [isCollaborationSecureLinkClosePending, setIsCollaborationSecureLinkClosePending] = useState(false);
  const collaborationPeopleTriggerRef = useRef(null), exactNotificationProjectionRef = useRef(null), collaborationMessageRefs = useRef({});
  const [highlightedCollaborationMessageId, setHighlightedCollaborationMessageId] = useState(null);
  const [collaborationLifecycleStatus, setCollaborationLifecycleStatus] = useState('idle');
  const collaborationLifecycleRequestRef = useRef(null);
  const activeCollaborationMessageId = 'mail', activeCollaborationSourceMailboxId = 'main', activeCollaborationMessage = sourceMail;
  const activeCollaborationOwnerContextKey = 'fixture', currentViewerPersistenceKey = 'fixture-viewer', currentMemberUserId = me, currentUserEmail = 'rutger@example.test';
  const activeCollaborationOwnerLocator = null;
  const collaborationOwnerProjectionRequestRef = useRef({ identityKey: 'fixture', requestId: 1, inFlight: false, messageId: 'mail', sourceMailboxId: 'main' });
  const collaborationOwnerProjectionGenerationRef = useRef(1), collaborationOwnerSharedMessageGenerationRef = useRef(1);
  const collaborationOwnerSharedMessageRequestRef = useRef(null), collaborationOwnerInternalNoteRequestRef = useRef(null);
  const [collaborationOwnerSharedMessageDraft, setCollaborationOwnerSharedMessageDraft] = useState('');
  const [collaborationOwnerInternalNoteDraft, setCollaborationOwnerInternalNoteDraft] = useState('');
  const [collaborationOwnerSharedMessageState, setCollaborationOwnerSharedMessageState] = useState({ status: 'idle' });
  const [collaborationOwnerInternalNoteState, setCollaborationOwnerInternalNoteState] = useState({ status: 'idle' });
  const hasActiveCollaborationOwnerProjection = true, hasActiveCollaborationOwnerLifecycle = true, collaborationAccessPanelMode = 'access';
  const teamMemberEntries = [{ memberUserId: dan, name: 'Dan', email: 'dan@example.test', status: 'Active' }];
  const onCanonicalCollaborationMutation = () => {};
  const applyCanonicalCollaborationAccessResult = collaboration => setCollaborationOwnerProjection(p => ({ ...p, collaboration }));
  const closeCollaborationOverlay = () => { setIsCollaborationSecureLinkClosePending(false); setOpen(false); };
  useEffect(() => { if (isCollaborationSecureLinkVisible) setIsCollaborationPeopleOpen(true); }, [isCollaborationSecureLinkVisible]);
  /* CLOSE */
  useEffect(()=>{const escape=event=>{if(event.key==='Escape') requestCloseCollaborationOverlay();};window.addEventListener('keydown',escape);return()=>window.removeEventListener('keydown',escape);},[isCollaborationSecureLinkVisible]);
  const mailbox = {email: 'rutger@example.test'};
  const [revealedRemoteImageMessageIds, setRevealedRemoteImageMessageIds] = useState([]);
  const canShowRemoteImagesForMessage = id => revealedRemoteImageMessageIds.includes(id);
  const revealRemoteImagesForMessage = id => setRevealedRemoteImageMessageIds(ids=>[...ids,id]);
  const compactMessageParagraphs = paragraphs => paragraphs.map(p=>p.trim()).filter(Boolean);
  const [desktopThreadDisclosureState, setDesktopThreadDisclosureState] = useState({expandedQuoteMessageIds:[],expandedMemberIds:[]});
  const activeDesktopThreadDisclosureState = desktopThreadDisclosureState;
  const primaryMessageSelection = null;
  const reconcileDesktopThreadDisclosureState = current=>current;
  const toggleDisclosureId = (ids,id) => ids.includes(id)?ids.filter(i=>i!==id):[...ids,id];
  const handleAttachmentOpen = (attachment,message) => {fixture.attachmentOpen = {attachmentId:attachment.id,messageId:message.id};};
  /* READER */
  /* ATTACHMENT_TYPES */
  /* ATTACHMENTS */
  /* SEND */
  /* LIFECYCLE */
  const [serverNotificationDisplay, setServerNotificationDisplay] = useState(null);
  const fullMessageModalMessage = null, fullMessageModalDialogRef = useRef(null);
  const matchesExactMailboxMessageIdentity = () => true; // exact-mail navigation is covered by the existing navigator suite
  fixture.refs = collaborationMessageRefs;
  fixture.projection = exactNotificationProjectionRef;
  fixture.target = id => {
    fixture.marks = [];
    setIsCollaborationPeopleOpen(false); setCollaborationContextTab('conversation'); setHighlightedCollaborationMessageId(id);
    setServerNotificationDisplay({ collaboration: true, target: { message: { id: 'mail' } }, ticket: { isCurrent: () => true, notification: { activityId: id, mailboxId: 'main', collaborationId: canonical.collaborationId, sourceRef: {} } }, complete: displayed => fixture.marks.push({ displayed, focused: document.activeElement?.getAttribute('data-collaboration-activity-id') }) });
  };
  /* DISPLAY */
  if (!open) return <p>Collaboration closed.</p>;
  return <>{/* MODAL */}{isCollaborationSecureLinkClosePending ? <div role="alertdialog" aria-label="Close the one-time secure link?" className="fixed inset-0 z-[400] flex items-center justify-center bg-black/30"><div className="rounded-xl bg-white p-6"><p>Cuevion doesn’t store this link. Make sure you’ve copied it before closing.</p><button onClick={() => setIsCollaborationSecureLinkClosePending(false)}>Back</button><button onClick={closeCollaborationOverlay}>Close anyway</button></div></div> : null}</>;
}
createRoot(document.getElementById('root')).render(<Harness />);
