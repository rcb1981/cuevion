const assert = require('node:assert/strict');
const { test } = require('node:test');
const React = require('react');
const { act, create } = require('react-test-renderer');
require('sucrase/register/ts');
require('sucrase/register/tsx');
const policy = require('../../lib/microsoftDraftAttachmentPolicy');
assert.equal(policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED, false);
policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED = true; // Isolated test process only.
const { MicrosoftDraftAttachments } = require('./MicrosoftDraftAttachments');
const { MicrosoftDraftEditor } = require('./MicrosoftDraftEditor');
const { MicrosoftRichDraftMetadataEditor } = require('./MicrosoftRichDraftMetadataEditor');
const api = require('../../lib/microsoftDraftAttachmentApi');
const { webcrypto } = require('node:crypto');
globalThis.crypto ??= webcrypto;
const BOX = 'microsoft-box', ID = 'draft-1';
const text = n => typeof n === 'string' ? n : (n?.children ?? []).map(text).join('');
const shown = t => text(t.toJSON());
const button = (t, label) => t.root.findAllByType('button').find(n => text(n) === label);
const byLabel = (t, label) => t.root.findByProps({ 'aria-label': label });
const deferred = () => { let resolve; const promise = new Promise(r => resolve = r); return { resolve, promise }; };
const response = (data, status = 200) => ({ok: status === 200, status, json: async () => data});
const file = (name = 'new.txt', size = 4, type = 'text/plain') => ({name, size, type, arrayBuffer: async () => new Uint8Array(size).fill(120).buffer});
const ordinary = {id: 'existing', name: 'existing.pdf', contentType: 'application/pdf', size: 1024, kind: 'file', isInline: false, contentId: null, removable: true};
const inline = {...ordinary, id: 'inline', name: 'logo.png', contentType: 'image/png', isInline: true, contentId: 'logo', removable: false};
function fixture(options = {}) {
  const calls = [], requests = new Map(), mutation = deferred();
  const rows = [ordinary, inline, {...ordinary, id:'item', name:'mail.msg', kind:'item', removable:false}];
  let pending, changed = false;
  const detail = deferred();
  const message = (id, full) => ({
    id, providerMessageId:id, providerThreadId:id+'thread', serverMailboxId:BOX,
    providerFolder:'DRAFTS', labelIds:['DRAFTS'], sender:'To: artist@example.com',
    from:'', to:'artist@example.com', cc:'', subject:'Original subject', snippet:'',
    timestamp:'October 24 at 12:00', createdAt:'2026-10-24T12:00:00+00:00',
    body:full ? [changed ? 'Fresh draft body' : 'Original draft body'] : [],
    unread:false, flagged:false, attachments:[], hasAttachments:true,
  });
  const fetch = async (url, init) => {
    const p = JSON.parse(init.body); calls.push(p);
    assert.equal(init.credentials, 'include'); assert.equal(init.method, 'POST');
    if (url.endsWith('/fetch-microsoft')) return response({ok:true,messages:[],inboxUidSet:[],uidValidity:'microsoft-graph-immutable',hasMore:false});
    if (url.endsWith('/fetch-microsoft-drafts')) return response({ok:true,messages:[message(ID,false)],draftMessageIds:[ID],hasMore:false});
    if (url.endsWith('/fetch-microsoft-draft-message')) return changed ? detail.promise : response({ok:true,message:message(p.providerMessageId,true)});
    if (url.endsWith('/fetch-microsoft-attachments')) return response({ok:true,attachments:rows.map(({removable,...r})=>r),hasMore:false});
    if (url.endsWith('edit-microsoft-draft')) {
      if (p.action.startsWith('load')) return response({ok:true, providerMessageId:p.providerMessageId, etag:'W/"v1"', ...(p.action === 'load_rich' ? {metadata:{subject:'Original', to:[], cc:[]}, bodyPreview:'Original HTML'} : {edit:{subject:'Original', to:[], cc:[], body:'Original text'}})});
      return response({ok:true, providerMessageId:p.providerMessageId, etag:'W/"v2"', changed:true, ...(p.action === 'save_rich' ? {metadata:p.edit, bodyPreview:'Original HTML'} : {edit:p.edit})});
    }
    assert.ok(url.endsWith('mutate-microsoft-draft-attachments'));
    const context = {ok:true, mailboxId:p.mailboxId, providerMessageId:p.providerMessageId};
    if (p.action === 'draft_status') {
      if (options.statusError) return response({ok:false,error:{code:options.statusError}},409);
      return response({...context, requests:[...requests.values()].filter(r => r.providerMessageId === p.providerMessageId).map(({ok,mailboxId,...r}) => r)});
    }
    if (p.action === 'status') return requests.has(p.requestId) ? response(requests.get(p.requestId)) : response({ok:false,error:{code:'missing'}},409);
    if (p.action === 'load') {
      if (options.holdLoad && p.providerMessageId === ID) return options.holdLoad.promise;
      return response({...context, snapshotHash:'a'.repeat(64), complete:true, attachments:p.providerMessageId === ID ? rows : []});
    }
    assert.ok(['add','remove'].includes(p.action));
    pending = {...context, requestId:p.requestId, count:p.action === 'add' ? p.files.length : 1, completed:0, outcome:'uncertain', blocked:true};
    requests.set(p.requestId, pending);
    if (options.lost) throw new Error('offline');
    return mutation.promise;
  };
  return {calls, requests, fetch, rows,
    releaseDetail(){detail.resolve(response({ok:true,message:message(ID,true)}));},
    complete(outcome = 'confirmed_performed', completed) {
      pending = {...pending, outcome, completed: completed ?? (outcome === 'confirmed_performed' ? pending.count : 0), blocked:outcome === 'uncertain'};
      requests.set(pending.requestId, pending);
      if(outcome === 'confirmed_performed') changed = true;
      mutation.resolve(response(pending));
    },
    resolveStatus(outcome = 'confirmed_performed') {
      for (const [id,r] of requests) requests.set(id,{...r,outcome,completed:outcome === 'confirmed_performed' ? r.count : 0,blocked:outcome === 'uncertain'});
    },
    writes() { return calls.filter(p => ['add','remove'].includes(p.action)); },
  };
}
async function setup(options = {}, Component = MicrosoftDraftAttachments) {
  const f = fixture(options), previous = globalThis.fetch;
  globalThis.fetch = f.fetch;
  const props = {mailboxId:BOX, providerMessageId:ID, disabled:false, editorPending:{current:false}, onBlockedChange() {}, onConfirmed(){f.confirmed = (f.confirmed ?? 0)+1;}, onSaved(){f.confirmed = (f.confirmed ?? 0)+1;}, onClose(){}};
  let tree;
  await act(async () => {tree = create(React.createElement(Component, props), {createNodeMock: () => ({click(){f.picker = true;}})});});
  return {f, props, get tree(){return tree;}, async mount(p = props){await act(async () => {tree = create(React.createElement(Component,p));});}, async close(){await act(async () => tree.unmount());globalThis.fetch = previous;}};
}
async function choose(t, files) {await act(async () => byLabel(t,'Choose draft attachments').props.onChange({currentTarget:{files,value:'selected'}}));}
async function start(t) {await act(async () => {button(t, t.root.findAllByType('button').map(text).find(s => s.startsWith('Upload '))).props.onClick();});}

test('picker accepts multiple files, shows cards and removes a pending selection without HTTP writes', async () => {
 const s=await setup();try {
  await act(async()=>button(s.tree,'Add attachments').props.onClick());assert.equal(s.f.picker,true);
  await choose(s.tree,[file('one.txt'),file('two.pdf',2048,'application/pdf')]);
  assert.match(shown(s.tree),/one.txt.*Text • 4 B • Not uploaded.*two.pdf.*PDF • 2 KB/s);
  await act(async()=>byLabel(s.tree,'Remove selected one.txt').props.onClick());
  assert.doesNotMatch(shown(s.tree),/one.txt/);assert.match(shown(s.tree),/two.pdf/);assert.equal(s.f.writes().length,0);
 }finally{await s.close();}
});
test('drop adds multiple files; every limit rejects before upload',async()=>{
 const s=await setup();try {
  let prevented=0;
  await act(async()=>byLabel(s.tree,'Microsoft draft attachments').props.onDrop({preventDefault(){prevented++;},dataTransfer:{files:[file('drop.txt'),file('second.txt')]}}));
  assert.equal(prevented,1);assert.match(shown(s.tree),/Upload 2/);
  for(const bad of [[file('large',2*1024*1024+1)],Array.from({length:11},()=>file()),Array.from({length:5},()=>file('x',2*1024*1024)),[file('../bad')]]){
   await choose(s.tree,bad);assert.match(shown(s.tree),/Select at most 10 files/);assert.equal(s.f.writes().length,0);
  }
 }finally{await s.close();}
});
test('upload status blocks double clicks during preparation and writes, then refreshes on confirmation',async()=>{
 const s=await setup();try {
  const bytes=deferred();await choose(s.tree,[{...file(),arrayBuffer:()=>bytes.promise},file('two.txt')]);
  const uploadHandler=button(s.tree,'Upload 2 attachment(s)').props.onClick;
  await act(async()=>{uploadHandler();uploadHandler();});assert.match(shown(s.tree),/Preparing/);assert.equal(s.f.writes().length,0);
  await act(async()=>bytes.resolve(new Uint8Array(4).buffer));
  assert.match(shown(s.tree),/Uploading attachments and verifying/);assert.doesNotMatch(shown(s.tree),/existing.pdf/);
  await act(async()=>uploadHandler());assert.equal(s.f.writes().length,1);
  assert.equal(s.f.writes()[0].files.length,2);
  await act(async()=>s.f.complete());assert.equal(s.f.confirmed,1);
 }finally{await s.close();}
});
test('safe existing remove is one delete request; CID and unsupported types never get a remove action',async()=>{
 const s=await setup();try {
  assert.match(shown(s.tree),/PDF • 1 KB/);assert.match(shown(s.tree),/inline\/CID attachment\(s\) preserved/);
  assert.equal(s.tree.root.findAllByProps({'aria-label':'Remove logo.png'}).length,0);
  assert.equal(s.tree.root.findAllByProps({'aria-label':'Remove mail.msg'}).length,0);
  const remove=byLabel(s.tree,'Remove existing.pdf');await act(async()=>{remove.props.onClick();remove.props.onClick();});
  assert.match(shown(s.tree),/Removing attachment and verifying/);assert.equal(s.f.writes().length,1);assert.equal(s.f.writes()[0].attachmentId,'existing');
  await act(async()=>s.f.complete());assert.equal(s.f.confirmed,1);
 }finally{await s.close();}
});
for(const lost of [false,true])test('uncertain/lost HTTP response stays blocked across remount, explicit checks only read: '+lost,async()=>{
 const s=await setup({lost});try {
  await choose(s.tree,[file()]);await start(s.tree);if(!lost)await act(async()=>s.f.complete('uncertain'));
  assert.match(shown(s.tree),/uncertain/i);assert.equal(button(s.tree,'Add attachments').props.disabled,true);
  await act(async()=>button(s.tree,'Check attachment status').props.onClick());assert.equal(s.f.writes().length,1);
  await act(async()=>s.tree.unmount());await s.mount();
  assert.match(shown(s.tree),/Uncertain operation/);assert.equal(button(s.tree,'Add attachments').props.disabled,true);assert.equal(s.f.writes().length,1);
  await act(async()=>button(s.tree,'Check attachment status').props.onClick());assert.equal(s.f.writes().length,1);
 }finally{await s.close();}
});
test('reload during a pending operation recovers the durable barrier and ignores the old response',async()=>{
 const s=await setup();try {
  await choose(s.tree,[file()]);await start(s.tree);await act(async()=>s.tree.unmount());await s.mount();
  assert.match(shown(s.tree),/Uncertain operation/);await act(async()=>s.f.complete());assert.equal(s.f.confirmed,undefined);
  assert.match(shown(s.tree),/Uncertain operation/);
  await act(async()=>button(s.tree,'Check attachment status').props.onClick());
  assert.equal(button(s.tree,'Add attachments').props.disabled,false);assert.equal(s.f.writes().length,1);
 }finally{await s.close();}
});
test('switching drafts ignores late mutation and load responses and recovers original draft status',async()=>{
 const s=await setup();try {
  await choose(s.tree,[file()]);await start(s.tree);
  await act(async()=>s.tree.update(React.createElement(MicrosoftDraftAttachments,{...s.props,providerMessageId:'draft-2'})));
  assert.doesNotMatch(shown(s.tree),/existing.pdf|Uncertain operation/);
  await act(async()=>s.f.complete('uncertain'));assert.equal(s.f.confirmed,undefined);
  await act(async()=>s.tree.update(React.createElement(MicrosoftDraftAttachments,s.props)));
  assert.match(shown(s.tree),/Uncertain operation/);assert.equal(s.f.writes().length,1);
 }finally{await s.close();}
 const held=deferred(), h=await setup({holdLoad:held});try{
  await act(async()=>h.tree.update(React.createElement(MicrosoftDraftAttachments,{...h.props,providerMessageId:'draft-2'})));
  await act(async()=>held.resolve(response({ok:true,mailboxId:BOX,providerMessageId:ID,complete:true,snapshotHash:'a'.repeat(64),attachments:[ordinary]})));
  assert.doesNotMatch(shown(h.tree),/existing.pdf/);
 }finally{await h.close();}
});
test('confirmed non-performance requires explicit reload and a fresh selection',async()=>{
 const s=await setup();try{
  await choose(s.tree,[file()]);await start(s.tree);await act(async()=>s.f.complete('confirmed_not_performed'));
  assert.match(shown(s.tree),/no attachment was changed/);assert.equal(button(s.tree,'Add attachments').props.disabled,true);
  await act(async()=>button(s.tree,'Check attachment status').props.onClick());
  assert.equal(button(s.tree,'Add attachments').props.disabled,false);assert.doesNotMatch(shown(s.tree),/Upload 1/);assert.equal(s.f.writes().length,1);
 }finally{await s.close();}
});
for(const code of ['microsoft_reconnect_required','microsoft_unauthorized'])test('expired connection or session blocks writes: '+code,async()=>{
 const s=await setup({statusError:code});try{assert.equal(button(s.tree,'Add attachments').props.disabled,true);assert.match(shown(s.tree),code.includes('reconnect')?/Reconnect/:/Sign in/);assert.equal(s.f.writes().length,0);}finally{await s.close();}
});
for(const rich of [false,true])test('editor shares operation guard and save payload never includes existing attachments/HTML: '+rich,async()=>{
 const s=await setup({},rich?MicrosoftRichDraftMetadataEditor:MicrosoftDraftEditor);try{
  const field=byLabel(s.tree,rich?'HTML draft subject':'Draft subject');
  await act(async()=>field.props.onChange({target:{value:'Updated'}}));
  const save=button(s.tree,rich?'Save draft details':'Save draft');
  await act(async()=>save.props.onClick());
  const payload=s.f.calls.find(p=>p.action === (rich?'save_rich':'save'));
  assert.ok(payload);assert.equal(payload.edit.subject,'Updated');assert.equal('attachments' in payload.edit,false);assert.equal('bcc' in payload.edit,false);
  if(rich){assert.equal('body' in payload.edit,false);assert.equal(s.tree.root.findAllByType('textarea').length,0);}
  assert.equal(button(s.tree,'Add attachments').props.disabled,true);
 }finally{await s.close();}
 const u=await setup({},rich?MicrosoftRichDraftMetadataEditor:MicrosoftDraftEditor);try{
  await choose(u.tree,[file()]);await start(u.tree);
  const save=button(u.tree,rich?'Reopen to verify save':'Save draft') ?? button(u.tree,rich?'Save draft details':'Save draft');
  await act(async()=>save.props.onClick());assert.equal(u.f.calls.filter(p=>p.action.startsWith('save')).length,0);
  await act(async()=>u.f.complete('uncertain'));assert.equal(byLabel(u.tree,rich?'HTML draft subject':'Draft subject').props.disabled,true);
 }finally{await u.close();}
});
test('status client rejects cross-draft, malformed, duplicate and private ledger responses',async()=>{
 const prev=globalThis.fetch;
 const r={requestId:'a'.repeat(43),providerMessageId:ID,outcome:'uncertain',count:1,completed:0,blocked:true};
 try{for(const requests of [[{...r,providerMessageId:'other'}],[{...r,claim:'secret'}],[r,r],[{...r,blocked:false}],[{...r,ok:false}],[{...r,mailboxId:'other'}]]){
  globalThis.fetch=async()=>response({ok:true,mailboxId:BOX,providerMessageId:ID,requests});
  assert.equal((await api.inspectMicrosoftDraftAttachmentStatus({mailboxId:BOX,providerMessageId:ID})).ok,false);
 }}finally{globalThis.fetch=prev;}
});
test('disabled source gate renders no attachment controls or HTTP activity',async()=>{
 policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED=false;const s=await setup();try{assert.equal(s.tree.toJSON(),null);assert.equal(s.f.calls.length,0);}finally{await s.close();policy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED=true;}
});


// Exercise the real mailbox integration with both source gates enabled only in memory.
const fs = require('node:fs'), path = require('node:path'), Module = require('node:module');
const {transform} = require('sucrase');
require('./useMicrosoftFilteredLearning').useMicrosoftFilteredLearning = () => ({learned:{},placements:{},rules:{},ready:true,error:null});
const filename = path.join(__dirname,'MicrosoftMailboxReadOnlyView.tsx');
const source = fs.readFileSync(filename,'utf8');
assert.equal(source.split('const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = false;').length,2);
const enabled = new Module(filename,module); enabled.filename=filename; enabled.paths=Module._nodeModulePaths(__dirname);
enabled._compile(transform(source.replace('const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = false;','const MICROSOFT_DRAFT_EDIT_ACTIONS_RELEASED = true;'),{transforms:['typescript','jsx','imports'],filePath:filename}).code,filename);
const View = enabled.exports.MicrosoftMailboxReadOnlyView;
const Rail = require('./MicrosoftMailboxFolderRail').MicrosoftMailboxFolderRail;
const Row = require('./MicrosoftMessageListRow').MicrosoftMessageListRow;
for(const remove of [false,true])test('mailbox clears old detail and awaits a fresh exact draft after confirmed attachment '+(remove?'removal':'upload'),async()=>{
 const f=fixture(), oldFetch=globalThis.fetch, oldWindow=globalThis.window;
 globalThis.fetch=f.fetch;globalThis.window={setInterval:()=>1,clearInterval(){}};
 let tree;
 try{
  await act(async()=>{tree=create(React.createElement(View,{mailbox:{id:BOX,title:'Test mailbox',email:'owner@example.com'},connected:true,onBack(){},onOpenConnectionSettings(){}}));});
  await act(async()=>tree.root.findAllByType(Rail)[0].props.onSelectFolder('Drafts'));
  await act(async()=>tree.root.findAllByType(Row)[0].props.onOpenMessage(ID));
  assert.match(shown(tree),/Original draft body/);
  await act(async()=>button(tree,'Edit draft').props.onClick());
  if(remove)await act(async()=>byLabel(tree,'Remove existing.pdf').props.onClick());
  else{await choose(tree,[file()]);await start(tree);}
  assert.doesNotMatch(shown(tree),/Original draft body/);
  await act(async()=>f.complete());
  assert.equal(tree.root.findAllByType(MicrosoftDraftEditor).length,0);
  assert.match(shown(tree),/Opening message/);assert.doesNotMatch(shown(tree),/Original draft body/);
  await act(async()=>f.releaseDetail());assert.match(shown(tree),/Fresh draft body/);assert.equal(f.writes().length,1);
 }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=oldFetch;globalThis.window=oldWindow;}
});
test('unsaved editor changes prevent attachment mutation and remain in the editor',async()=>{
 const s=await setup({},MicrosoftDraftEditor);try{
  await act(async()=>byLabel(s.tree,'Draft subject').props.onChange({target:{value:'Unsaved subject'}}));
  assert.equal(button(s.tree,'Add attachments').props.disabled,true);
  assert.match(shown(s.tree),/Save your draft changes before changing attachments/);
  await act(async()=>byLabel(s.tree,'Remove existing.pdf').props.onClick());
  assert.equal(s.f.writes().length,0);assert.equal(byLabel(s.tree,'Draft subject').props.value,'Unsaved subject');
 }finally{await s.close();}
});

test('partially confirmed selections remain visibly blocked without resuming remaining files',async()=>{
 const s=await setup();try{
  await choose(s.tree,[file('one.txt'),file('two.txt')]);await start(s.tree);
  await act(async()=>s.f.complete('uncertain',1));
  assert.match(shown(s.tree),/Uncertain operation: 1\/2 attachment/);
  await act(async()=>button(s.tree,'Check attachment status').props.onClick());assert.equal(s.f.writes().length,1);
  assert.equal(button(s.tree,'Add attachments').props.disabled,true);
 }finally{await s.close();}
});
