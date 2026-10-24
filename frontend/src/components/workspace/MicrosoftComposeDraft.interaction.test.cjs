const assert=require('node:assert/strict');
const {test}=require('node:test');
const React=require('react');
const {act,create}=require('react-test-renderer');
require('sucrase/register/ts');require('sucrase/register/tsx');
const api=require('../../lib/microsoftComposeDraftApi');
const attachmentPolicy=require('../../lib/microsoftDraftAttachmentPolicy');
assert.equal(api.MICROSOFT_COMPOSE_DRAFTS_RELEASED,false);
assert.equal(attachmentPolicy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED,false);
api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=true;attachmentPolicy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED=true;
globalThis.crypto??=require('node:crypto').webcrypto;
const {MicrosoftComposeDraftPanel}=require('./MicrosoftComposeDraftPanel');
const BOX='microsoft-box';
const original={subject:'Saved subject',body:'Saved body',to:['artist@example.com'],cc:['manager@example.com'],bcc:['hidden@example.com']};
const metadata=(id,name)=>({id,name,size:4,contentType:'text/plain',kind:'file',isInline:false,contentId:null,removable:true});
const file=(name='one.txt',size=4,type='text/plain')=>({name,size,type,lastModified:1,arrayBuffer:async()=>new Uint8Array(size).fill(120).buffer});
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const shown=t=>text(t.toJSON());
const button=(t,label)=>t.root.findAllByType('button').find(n=>text(n)===label);
const label=(t,name)=>t.root.findByProps({'aria-label':name});
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
function fixture(options={}){
 const calls=[],operations=new Map(),attachments=new Map(),drafts=new Map();
 if(options.existing)drafts.set('draft-1',{edit:{...original},version:1,rows:[metadata('existing','existing.txt')]});
 let pendingSave,pendingUpload,created=0;
 function current(id){const d=drafts.get(id);assert.ok(d,'known exact draft');return {providerMessageId:id,etag:'W/"v'+d.version+'"',snapshotHash:'a'.repeat(64),edit:{subject:d.edit.subject,to:d.edit.to,cc:d.edit.cc,body:d.edit.body,bcc:d.edit.bcc}};}
 function finishSave(outcome='confirmed_performed'){
  const {p,wait}=pendingSave;let id=p.action==='update'?p.providerMessageId:null;
  if(outcome==='confirmed_performed'){
   if(!id)id='created-'+(++created);
   const prior=drafts.get(id);drafts.set(id,{edit:structuredClone(p.edit),version:(prior?.version??0)+1,rows:prior?.rows??[]});
  }
  const receipt={requestId:p.requestId,action:p.action,providerMessageId:id,outcome,blocked:outcome==='uncertain'};
  operations.set(p.requestId,receipt);wait.resolve(response({ok:true,mailboxId:p.mailboxId,receipt}));
 }
 function finishUpload(outcome='confirmed_performed',completed){
  const {p,wait}=pendingUpload,d=drafts.get(p.providerMessageId);
  completed??=outcome==='confirmed_performed'?p.files.length:0;
  for(let i=0;i<completed;i++)d.rows.push(metadata('new-'+i+'-'+p.requestId,p.files[i].name));
  d.version++;
  const receipt={ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requestId:p.requestId,outcome,count:p.files.length,completed,blocked:outcome==='uncertain'};
  attachments.set(p.requestId,receipt);wait.resolve(response(receipt));
 }
 const fetch=async(url,init)=>{
  const p=JSON.parse(init.body);calls.push({url,p});assert.equal(init.method,'POST');assert.equal(init.credentials,'include');
  assert.ok(!url.includes('send-microsoft'));
  const message=(id,full)=>{const d=drafts.get(id);return {id,providerMessageId:id,providerThreadId:id+'thread',serverMailboxId:p.mailboxId,providerFolder:'DRAFTS',labelIds:['DRAFTS'],sender:'To: '+d.edit.to.join(', '),from:'',to:d.edit.to.join(', '),cc:d.edit.cc.join(', '),subject:d.edit.subject,snippet:'',timestamp:'October 24 at 12:00',createdAt:'2026-10-24T12:00:00+00:00',body:full?[d.edit.body]:[],unread:false,flagged:false,attachments:[],hasAttachments:d.rows.length>0};};
  if(url.endsWith('/fetch-microsoft'))return response({ok:true,messages:[],inboxUidSet:[],uidValidity:'microsoft-graph-immutable',hasMore:false});
  if(url.endsWith('/fetch-microsoft-drafts'))return response({ok:true,messages:[...drafts.keys()].map(id=>message(id,false)),draftMessageIds:[...drafts.keys()],hasMore:false});
  if(url.endsWith('/fetch-microsoft-draft-message'))return response({ok:true,message:message(p.providerMessageId,true)});
  if(url.endsWith('/fetch-microsoft-attachments'))return response({ok:true,attachments:drafts.get(p.providerMessageId).rows.map(({removable,...r})=>r),hasMore:false});
  if(url.endsWith('microsoft-compose-draft')){
   if(p.action==='status'){
    if(options.statusError)return response({ok:false,error:{code:options.statusError,message:'private provider text'}},409);
    return response({ok:true,mailboxId:p.mailboxId,operations:[...operations.values()]});
   }
   if(p.action==='load'){
    if(options.holdLoad)return options.holdLoad.promise;
    return response({ok:true,mailboxId:p.mailboxId,...current(p.providerMessageId)});
   }
   assert.ok(['create','update'].includes(p.action));
   const wait=deferred();pendingSave={p,wait};
   operations.set(p.requestId,{requestId:p.requestId,action:p.action,providerMessageId:p.providerMessageId??null,outcome:'uncertain',blocked:true});
   if(options.conflict)return response({ok:false,error:{code:'microsoft_compose_conflict'}},409);
   if(options.lostGraph){created++;return response({ok:true,mailboxId:p.mailboxId,receipt:operations.get(p.requestId)});}
   if(options.lostHTTP){finishSave();throw new Error('lost browser response');}
   if(options.autoSave)finishSave();
   return wait.promise;
  }
  if(url.endsWith('mutate-microsoft-draft-attachments')){
   const d=drafts.get(p.providerMessageId);assert.ok(d);
   if(p.action==='draft_status')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requests:[...attachments.values()].filter(r=>r.providerMessageId===p.providerMessageId).map(({ok,mailboxId,...r})=>r)});
   if(p.action==='load')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,snapshotHash:'a'.repeat(64),complete:true,attachments:d.rows});
   if(p.action==='status')return response(attachments.get(p.requestId));
   if(p.action==='remove'){
    d.rows=d.rows.filter(r=>r.id!==p.attachmentId);d.version++;
    const receipt={ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requestId:p.requestId,outcome:'confirmed_performed',count:1,completed:1,blocked:false};attachments.set(p.requestId,receipt);return response(receipt);
   }
   assert.equal(p.action,'add');const wait=deferred();pendingUpload={p,wait};
   attachments.set(p.requestId,{ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requestId:p.requestId,outcome:'uncertain',count:p.files.length,completed:0,blocked:true});
   if(options.lostUpload)throw new Error('lost upload response');
   if(options.autoUpload)finishUpload();return wait.promise;
  }
  assert.fail('Unexpected URL '+url);
 };
 return {fetch,calls,drafts,operations,attachments,finishSave,finishUpload,current,get created(){return created;},saves:()=>calls.filter(c=>c.url.endsWith('microsoft-compose-draft')&&['create','update'].includes(c.p.action)),uploads:()=>calls.filter(c=>c.p.action==='add')};
}
async function setup(options={},props={}){
 const f=fixture(options),oldFetch=globalThis.fetch;globalThis.fetch=f.fetch;
 const p={mailboxId:BOX,mailboxEmail:'owner@example.com',connected:true,onClose(){f.closed=true;},onSaved(id){f.saved=id;},onOpenDrafts(){f.openDrafts=true;},...props};
 let tree;const mount=async(newProps=p)=>{await act(async()=>{tree=create(React.createElement(MicrosoftComposeDraftPanel,newProps),{createNodeMock:()=>({click(){f.picker=true;}})});});};
 await mount();return {f,props:p,get tree(){return tree;},mount,async close(){await act(async()=>tree.unmount());globalThis.fetch=oldFetch;}};
}
async function change(t,field,value){await act(async()=>label(t,field).props.onChange({target:{value}}));}
async function choose(t,files){await act(async()=>label(t,'Choose Compose attachments').props.onChange({currentTarget:{files,value:'selected'}}));}
async function save(t){await act(async()=>button(t,'Save draft').props.onClick());}
async function fill(t){await change(t,'Compose To','Artist@Example.com');await act(async()=>button(t,'Cc').props.onClick());await change(t,'Compose Cc','manager@example.com');await act(async()=>button(t,'Bcc').props.onClick());await change(t,'Compose Bcc','hidden@example.com');await change(t,'Compose subject','Subject');await change(t,'Compose message body','Text\n<literal> body');}

test('new Compose supports To Cc Bcc subject body and saves exactly one draft; Send has no handler',async()=>{
 const s=await setup();try{
  await fill(s.tree);const send=button(s.tree,'Send');assert.equal(send.props.disabled,true);assert.equal(send.props.onClick,undefined);
  const click=button(s.tree,'Save draft').props.onClick;
  await act(async()=>{click();click();});assert.match(shown(s.tree),/Saving draft and verifying/);assert.equal(s.f.saves().length,1);
  assert.deepEqual(s.f.saves()[0].p.edit,{subject:'Subject',body:'Text\n<literal> body',to:['artist@example.com'],cc:['manager@example.com'],bcc:['hidden@example.com']});
  await act(async()=>s.f.finishSave());assert.match(shown(s.tree),/Saved to Microsoft Drafts/);assert.equal(s.f.saved,'created-1');assert.equal(s.f.created,1);
  assert.equal(label(s.tree,'Compose Bcc').props.value,'hidden@example.com');
 }finally{await s.close();}
});
test('picker, multi-file drop, local removal and duplicate selection do not upload before Save',async()=>{
 const s=await setup();try{
  await act(async()=>label(s.tree,'Add Compose attachments').props.onClick());assert.equal(s.f.picker,true);
  await choose(s.tree,[file('one.txt'),file('two.pdf',1024,'application/pdf')]);
  assert.match(shown(s.tree),/one.txt.*Text • 4 B • Not uploaded.*two.pdf.*PDF • 1 KB • Not uploaded/s);
  await act(async()=>label(s.tree,'Remove local one.txt').props.onClick());
  await act(async()=>label(s.tree,'Microsoft draft composer').props.onDrop({preventDefault(){},dataTransfer:{files:[file('three.txt'),file('four.txt')]}}));
  await choose(s.tree,[file('three.txt')]);assert.match(shown(s.tree),/already selected/);
  assert.equal(s.f.uploads().length,0);assert.equal(s.f.saves().length,0);
 }finally{await s.close();}
});
test('Save first creates the draft then uploads multiple files once, and switches local cards to saved Graph attachments',async()=>{
 const s=await setup({autoSave:true});try{
  await fill(s.tree);await choose(s.tree,[file('one.txt'),file('two.txt')]);const click=button(s.tree,'Save draft').props.onClick;
  await act(async()=>{click();click();});assert.equal(s.f.created,1);assert.equal(s.f.uploads().length,1);
  assert.match(shown(s.tree),/Uploading attachments and verifying/);assert.doesNotMatch(shown(s.tree),/Not uploaded/);
  assert.equal(s.f.uploads()[0].p.providerMessageId,'created-1');assert.equal(s.f.uploads()[0].p.files.length,2);
  await act(async()=>s.f.finishUpload());assert.match(shown(s.tree),/Saved Graph attachments.*one.txt.*two.txt/s);assert.equal(s.f.saved,'created-1');
  await act(async()=>label(s.tree,'Remove one.txt').props.onClick());assert.doesNotMatch(shown(s.tree),/one.txt/);assert.match(shown(s.tree),/two.txt/);
 }finally{await s.close();}
});
test('invalid recipients, Bcc duplicates and attachment limits fail without any mutation',async()=>{
 const s=await setup();try{
  await fill(s.tree);await change(s.tree,'Compose Bcc','artist@example.com');await save(s.tree);assert.match(shown(s.tree),/Check To, Cc and Bcc/);
  assert.equal(s.f.saves().length,0);
  await change(s.tree,'Compose Bcc','hidden@example.com');
  for(const files of [[file('large',2*1024*1024+1)],Array.from({length:11},(_,i)=>file('n'+i)),Array.from({length:5},(_,i)=>file('big'+i,2*1024*1024))]){
   await choose(s.tree,files);assert.match(shown(s.tree),/at most 10 files/);
  }
  assert.equal(s.f.uploads().length,0);
 }finally{await s.close();}
});
test('file preparation failure occurs before reserving or creating any draft',async()=>{
 const s=await setup();try{await choose(s.tree,[{...file(),arrayBuffer:async()=>{throw Error('unreadable');}}]);await save(s.tree);assert.equal(s.f.saves().length,0);assert.match(shown(s.tree),/at most 10 files/);}finally{await s.close();}
});
test('reopens saved draft including Bcc and attachments and continues editing using its ETag',async()=>{
 const s=await setup({existing:true},{providerMessageId:'draft-1'});try{
  assert.equal(label(s.tree,'Compose Bcc').props.value,'hidden@example.com');assert.match(shown(s.tree),/existing.txt/);
  await change(s.tree,'Compose message body','Continued text');await change(s.tree,'Compose Bcc','other@example.com');await save(s.tree);
  const p=s.f.saves()[0].p;assert.equal(p.action,'update');assert.equal(p.providerMessageId,'draft-1');assert.equal(p.etag,'W/"v1"');assert.equal('attachments' in p.edit,false);
  await act(async()=>s.f.finishSave());assert.equal(s.f.created,0);assert.equal(s.f.drafts.get('draft-1').rows.length,1);
  assert.equal(label(s.tree,'Compose message body').props.value,'Continued text');assert.match(shown(s.tree),/existing.txt/);
 }finally{await s.close();}
});
test('concurrent Outlook conflict blocks Save and never silently retries',async()=>{
 const s=await setup({existing:true,conflict:true},{providerMessageId:'draft-1'});try{
  await change(s.tree,'Compose subject','Changed');const click=button(s.tree,'Save draft').props.onClick;await act(async()=>{click();click();});
  assert.match(shown(s.tree),/changed in Outlook/);assert.equal(button(s.tree,'Save draft').props.disabled,true);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.equal(s.f.saves().length,1);
 }finally{await s.close();}
});
test('lost Graph create response blocks even a new mounted composer, without automatic duplicate creation',async()=>{
 const s=await setup({lostGraph:true});try{
  await fill(s.tree);await save(s.tree);assert.match(shown(s.tree),/uncertain/);assert.equal(s.f.created,1);
  await act(async()=>s.tree.unmount());await s.mount();assert.match(shown(s.tree),/uncertain/);assert.equal(button(s.tree,'Save draft').props.disabled,true);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.equal(s.f.created,1);assert.equal(s.f.saves().length,1);
 }finally{await s.close();}
});
test('lost HTTP receipt recovers the confirmed saved draft by reading the ledger; it never recreates or uploads automatically',async()=>{
 const s=await setup({lostHTTP:true});try{
  await fill(s.tree);await choose(s.tree,[file()]);await save(s.tree);assert.match(shown(s.tree),/uncertain/);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.equal(s.f.created,1);assert.equal(s.f.saved,'created-1');assert.equal(s.f.uploads().length,0);
  assert.equal(label(s.tree,'Compose subject').props.value,'Subject');assert.match(shown(s.tree),/Not uploaded/);
  await act(async()=>s.tree.unmount());await s.mount();assert.ok(button(s.tree,'Open saved draft'));
  await act(async()=>button(s.tree,'Open saved draft').props.onClick());assert.equal(label(s.tree,'Compose Bcc').props.value,'hidden@example.com');assert.equal(s.f.created,1);
 }finally{await s.close();}
});
test('reload during pending create ignores the old callback and restores the durable create barrier',async()=>{
 const s=await setup();try{
  await save(s.tree);await act(async()=>s.tree.unmount());await s.mount();assert.match(shown(s.tree),/uncertain/);
  await act(async()=>s.f.finishSave());assert.equal(s.f.saved,undefined);assert.equal(button(s.tree,'Save draft').props.disabled,true);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.ok(button(s.tree,'Open saved draft'));assert.equal(s.f.created,1);
 }finally{await s.close();}
});
test('partial/lost attachment result remains blocked and is never uploaded twice across reload',async()=>{
 const s=await setup({autoSave:true});try{
  await choose(s.tree,[file('one.txt'),file('two.txt')]);await save(s.tree);
  await act(async()=>s.f.finishUpload('uncertain',1));assert.match(shown(s.tree),/1\/2 confirmed/);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.equal(s.f.uploads().length,1);
  await act(async()=>s.tree.unmount());await s.mount({...s.props,providerMessageId:'created-1'});
  assert.match(shown(s.tree),/Uncertain operation: 1\/2/);assert.equal(button(s.tree,'Save draft').props.disabled,true);assert.equal(s.f.uploads().length,1);
 }finally{await s.close();}
});
test('mailbox switch ignores late save and load results and never targets the new mailbox with old data',async()=>{
 const s=await setup();try{
  await fill(s.tree);await save(s.tree);
  await act(async()=>s.tree.update(React.createElement(MicrosoftComposeDraftPanel,{...s.props,mailboxId:'microsoft-other',mailboxEmail:'other@example.com'})));
  assert.equal(label(s.tree,'Compose subject').props.value,'');
  await act(async()=>s.f.finishSave());assert.equal(s.f.saved,undefined);assert.equal(label(s.tree,'Compose subject').props.value,'');
  assert.equal(s.f.saves()[0].p.mailboxId,BOX);
 }finally{await s.close();}
 const hold=deferred(),h=await setup({existing:true,holdLoad:hold},{providerMessageId:'draft-1'});try{
  await act(async()=>h.tree.update(React.createElement(MicrosoftComposeDraftPanel,{...h.props,providerMessageId:undefined,mailboxId:'microsoft-other'})));
  await act(async()=>hold.resolve(response({ok:true,mailboxId:BOX,...h.f.current('draft-1')})));
  assert.equal(label(h.tree,'Compose subject').props.value,'');
 }finally{await h.close();}
});
for(const code of ['microsoft_reconnect_required','microsoft_unauthorized','microsoft_compose_store_unavailable'])test('safe error and disabled controls for '+code,async()=>{
 const s=await setup({statusError:code});try{assert.equal(button(s.tree,'Save draft').props.disabled,true);assert.doesNotMatch(shown(s.tree),/private provider text/);assert.equal(s.f.saves().length,0);}finally{await s.close();}
});
test('source gates remain disabled on disk; disabled component issues no requests and no browser persistence',async()=>{
 const {readFileSync}=require('node:fs'),path=require('node:path');
 const src=readFileSync(path.join(__dirname,'MicrosoftComposeDraftPanel.tsx'),'utf8');assert.doesNotMatch(src,/localStorage|sessionStorage|sendMicrosoftMessage|checkMicrosoftSentStatus/);
 assert.match(readFileSync(path.join(__dirname,'../../lib/microsoftComposeDraftApi.ts'),'utf8'),/MICROSOFT_COMPOSE_DRAFTS_RELEASED = false/);
 api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=false;const s=await setup();try{assert.equal(s.tree.toJSON(),null);assert.equal(s.f.calls.length,0);}finally{await s.close();api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=true;}
});
test('draft clients reject cross-mailbox IDs, private receipts and malformed bodies',async()=>{
 const oldFetch=globalThis.fetch;try{
  for(const operations of [[{requestId:'a'.repeat(43),action:'create',providerMessageId:null,outcome:'uncertain',blocked:true,claim:'secret'}],[{requestId:'a'.repeat(43),action:'update',providerMessageId:null,outcome:'uncertain',blocked:true}]]){
   globalThis.fetch=async()=>response({ok:true,mailboxId:BOX,operations});assert.equal((await api.inspectMicrosoftComposeDrafts({mailboxId:BOX})).ok,false);
  }
  globalThis.fetch=async()=>response({ok:true,mailboxId:'other',...{providerMessageId:'draft-1',etag:'W/"v1"',snapshotHash:'a'.repeat(64),edit:original}});
  assert.equal((await api.loadMicrosoftComposeDraft({mailboxId:BOX,providerMessageId:'draft-1'})).ok,false);
  assert.equal(api.validMicrosoftComposeDraftEdit({...original,html:'<b>bad</b>'}),false);
 }finally{globalThis.fetch=oldFetch;}
});

test('the actual mailbox Compose button saves a draft with Bcc and files, then Drafts reopens the same composer for further editing',async()=>{
 require('./useMicrosoftFilteredLearning').useMicrosoftFilteredLearning=()=>({learned:{},placements:{},rules:{},ready:true,error:null});
 const {MicrosoftMailboxReadOnlyView}=require('./MicrosoftMailboxReadOnlyView');
 const {MicrosoftMessageListRow}=require('./MicrosoftMessageListRow');
 const f=fixture({autoSave:true,autoUpload:true}),oldFetch=globalThis.fetch,oldWindow=globalThis.window;
 globalThis.fetch=f.fetch;globalThis.window={setInterval:()=>1,clearInterval(){}};
 let tree;
 try{
  await act(async()=>{tree=create(React.createElement(MicrosoftMailboxReadOnlyView,{mailbox:{id:BOX,title:'Test mailbox',email:'owner@example.com'},connected:true,onBack(){},onOpenConnectionSettings(){}}));});
  await act(async()=>label(tree,'Compose message').props.onClick());await fill(tree);await choose(tree,[file('mailbox.txt')]);await save(tree);
  assert.match(shown(tree),/Saved to Microsoft Drafts/);assert.equal(f.created,1);
  await act(async()=>button(tree,'View Drafts').props.onClick());assert.equal(tree.root.findAllByType(MicrosoftComposeDraftPanel).length,0);
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenMessage('created-1'));
  await act(async()=>button(tree,'Edit draft').props.onClick());assert.equal(label(tree,'Compose Bcc').props.value,'hidden@example.com');assert.match(shown(tree),/Saved Graph attachments.*mailbox.txt/s);
  await change(tree,'Compose subject','Continued subject');await save(tree);
  assert.equal(f.created,1);assert.equal(f.saves().at(-1).p.action,'update');assert.equal(f.drafts.get('created-1').rows.length,1);
 }finally{if(tree)await act(async()=>tree.unmount());globalThis.fetch=oldFetch;globalThis.window=oldWindow;}
});

test('lost upload response does not turn local files back into a new selection or repeat the upload',async()=>{
 const s=await setup({autoSave:true,lostUpload:true});try{
  await choose(s.tree,[file()]);await save(s.tree);assert.match(shown(s.tree),/could not be confirmed/);
  assert.doesNotMatch(shown(s.tree),/Not uploaded/);assert.equal(button(s.tree,'Save draft').props.disabled,true);
  await act(async()=>button(s.tree,'Check draft status').props.onClick());assert.equal(s.f.created,1);assert.equal(s.f.uploads().length,1);
 }finally{await s.close();}
});
