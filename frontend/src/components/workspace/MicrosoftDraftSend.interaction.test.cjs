const assert=require('node:assert/strict');
const {test}=require('node:test');
const React=require('react');
const {act,create}=require('react-test-renderer');
require('sucrase/register/ts');require('sucrase/register/tsx');
const composeApi=require('../../lib/microsoftComposeDraftApi');
const sendApi=require('../../lib/microsoftDraftSendApi');
const attachments=require('../../lib/microsoftDraftAttachmentPolicy');
assert.equal(sendApi.MICROSOFT_DRAFT_SEND_RELEASED,false);
composeApi.MICROSOFT_COMPOSE_DRAFTS_RELEASED=true;sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;attachments.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED=true;
globalThis.crypto??=require('node:crypto').webcrypto;
const {MicrosoftComposeDraftPanel}=require('./MicrosoftComposeDraftPanel');
const BOX='microsoft-box',ID='draft-1';
const edit={subject:'Ready subject',body:'Saved message',to:['a@example.com','b@example.com'],cc:['c@example.com'],bcc:['hidden@example.com']};
const row={id:'existing',name:'existing.txt',size:4,contentType:'text/plain',kind:'file',isInline:false,contentId:null,removable:true};
const deferred=()=>{let resolve;const promise=new Promise(r=>resolve=r);return {resolve,promise};};
const response=(body,status=200)=>({ok:status===200,status,json:async()=>body});
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const shown=t=>text(t.toJSON());
const button=(t,name)=>t.root.findAllByType('button').find(n=>text(n)===name);
const field=(t,name)=>t.root.findByProps({'aria-label':name});
function fixture(options={}){
 const calls=[],attempts=new Map(options.initial??[]),wait=deferred();let current,proof=false;
 const fetch=async(url,init)=>{
  const p=JSON.parse(init.body);calls.push({url,p});assert.equal(init.method,'POST');assert.equal(init.credentials,'include');
  if(url.endsWith('/send-microsoft-draft')){
   if(p.action==='history'){
    if(options.statusError)return response({ok:false,error:{code:options.statusError,message:'private token'}},409);
    const all=[...attempts.values()].map((receipt,i)=>({receipt,sequence:i+1,createdAt:'2026-10-26T12:00:00+00:00'}));
    const available=all.filter(e=>p.before===null||e.sequence<p.before).reverse(),entries=available.slice(0,32);
    const currentDraft=all.filter(e=>e.receipt.providerMessageId===p.providerMessageId).at(-1)??null;
    return response({ok:true,mailboxId:p.mailboxId,entries,currentDraft,nextCursor:available.length>32?entries.at(-1).sequence:null});
   }
   if(p.action==='status'){
    if(options.statusError)return response({ok:false,error:{code:options.statusError,message:'private token'}},409);
    return response({ok:true,mailboxId:p.mailboxId,attempts:[...attempts.values()]});
   }
   if(p.action==='check'){
    let receipt=attempts.get(p.attemptId);assert.ok(receipt);
    if(proof){receipt={...receipt,outcome:'confirmed_performed',providerAccepted:true,sentCopyConfirmed:true};attempts.set(p.attemptId,receipt);}
    return response({ok:true,mailboxId:p.mailboxId,receipt});
   }
   assert.equal(p.action,'send');assert.equal(p.providerMessageId,ID);assert.equal(p.etag,'W/"v1"');assert.equal(p.snapshotHash,'a'.repeat(64));
   current={attemptId:p.attemptId,providerMessageId:ID,outcome:'uncertain',providerAccepted:false,sentCopyConfirmed:false,deliveryConfirmed:false,reason:null};attempts.set(p.attemptId,current);
   if(options.lost)throw Error('lost HTTP response');
   if(options.notStarted){current={...current,outcome:'confirmed_not_started',reason:options.notStarted};attempts.set(p.attemptId,current);return response({ok:true,mailboxId:p.mailboxId,receipt:current});}
   return wait.promise;
  }
  if(url.endsWith('/microsoft-compose-draft')){
   if(p.action==='status')return response({ok:true,mailboxId:p.mailboxId,operations:[]});
   if(p.action==='load')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,etag:'W/"v1"',snapshotHash:'a'.repeat(64),edit:options.edit??edit});
   assert.fail('Send must not create or rewrite a draft');
  }
  if(url.endsWith('/mutate-microsoft-draft-attachments')){
   if(p.action==='draft_status')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requests:options.attachmentUncertain?[{requestId:'a'.repeat(43),providerMessageId:ID,outcome:'uncertain',count:1,completed:0,blocked:true}]:[]});
   if(p.action==='load')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,snapshotHash:'a'.repeat(64),complete:true,attachments:[row]});
   assert.fail('No attachment writes while sending');
  }
  assert.fail('Unexpected endpoint '+url);
 };
 return {fetch,calls,attempts,options,
  sends:()=>calls.filter(c=>c.url.endsWith('/send-microsoft-draft')&&c.p.action==='send'),
  finish(outcome='confirmed_performed',accepted=true){current={...current,outcome,providerAccepted:accepted,sentCopyConfirmed:outcome==='confirmed_performed'};attempts.set(current.attemptId,current);wait.resolve(response({ok:true,mailboxId:BOX,receipt:current}));},
  prove(){proof=true;},
 };
}
async function setup(options={},props={}){
 const f=fixture(options),previous=globalThis.fetch;globalThis.fetch=f.fetch;
 const p={mailboxId:BOX,mailboxEmail:'owner@example.com',providerMessageId:ID,connected:true,onClose(){},onSaved(){},onOpenDrafts(){},onSent(id){f.sent=id;},...props};
 let tree;const mount=async(newProps=p)=>{await act(async()=>{tree=create(React.createElement(MicrosoftComposeDraftPanel,newProps));});};
 await mount();return {f,props:p,mount,get tree(){return tree;},async close(){await act(async()=>tree.unmount());globalThis.fetch=previous;}};
}
async function send(t){await act(async()=>button(t,'Send').props.onClick());}

test('explicit Send uses exact saved ID/version/hash and never submits recipient/body/attachment replacements',async()=>{
 const s=await setup();try{
  assert.equal(field(s.tree,'Compose To').props.value,'a@example.com, b@example.com');assert.equal(field(s.tree,'Compose Bcc').props.value,'hidden@example.com');assert.match(shown(s.tree),/existing.txt/);
  const click=button(s.tree,'Send').props.onClick;await act(async()=>{click();click();});assert.equal(s.f.sends().length,1);
  assert.deepEqual(Object.keys(s.f.sends()[0].p).sort(),['action','attemptId','etag','mailboxId','providerMessageId','snapshotHash']);
  assert.equal(field(s.tree,'Compose subject').props.disabled,true);assert.match(shown(s.tree),/Send outcome uncertain/);
  await act(async()=>s.f.finish());assert.match(shown(s.tree),/Sending content confirmed by comparison with the approved manifest and exact Sent Items copy/);assert.match(shown(s.tree),/Recipient delivery is not confirmed/);assert.equal(s.f.sent,ID);
  await act(async()=>click());assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
test('dirty fields and locally selected files block sending without silently saving or uploading',async()=>{
 const s=await setup();try{
  await act(async()=>field(s.tree,'Compose subject').props.onChange({target:{value:'Unsaved subject'}}));
  assert.equal(button(s.tree,'Send').props.disabled,true);await send(s.tree);assert.equal(s.f.sends().length,0);
  await act(async()=>field(s.tree,'Compose subject').props.onChange({target:{value:edit.subject}}));
  const file={name:'local.txt',type:'text/plain',size:4,arrayBuffer:async()=>new Uint8Array(4).buffer};
  await act(async()=>field(s.tree,'Choose Compose attachments').props.onChange({currentTarget:{files:[file],value:'selected'}}));
  assert.match(shown(s.tree),/Not uploaded/);assert.match(shown(s.tree),/Save selected attachments before sending/);await send(s.tree);assert.equal(s.f.sends().length,0);
 }finally{await s.close();}
});
test('uncertain attachment ledger blocks sending and draft edits',async()=>{
 const s=await setup({attachmentUncertain:true});try{assert.equal(button(s.tree,'Send').props.disabled,true);await send(s.tree);assert.equal(s.f.sends().length,0);assert.match(shown(s.tree),/Uncertain operation/);}finally{await s.close();}
});
for(const change of [{to:[],cc:[],bcc:[]},{subject:''},{body:''}])test('invalid saved send content is refused '+JSON.stringify(change),async()=>{
 const s=await setup({edit:{...edit,...change}});try{assert.equal(button(s.tree,'Send').props.disabled,true);await send(s.tree);assert.equal(s.f.sends().length,0);}finally{await s.close();}
});
test('202 acceptance alone remains uncertain; explicit exact Sent check confirms without resending',async()=>{
 const s=await setup();try{
  await send(s.tree);await act(async()=>s.f.finish('uncertain'));assert.match(shown(s.tree),/Microsoft accepted the request; the Sent copy is not yet confirmed/);assert.equal(s.f.sent,undefined);
  await act(async()=>button(s.tree,'Check Sent status').props.onClick());assert.equal(s.f.sends().length,1);
  s.f.prove();await act(async()=>button(s.tree,'Check Sent status').props.onClick());assert.equal(s.f.sent,ID);assert.match(shown(s.tree),/Recipient delivery is not confirmed/);assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
test('lost send response stays blocked across remount and can only be resolved with status reads',async()=>{
 const s=await setup({lost:true});try{
  await send(s.tree);assert.match(shown(s.tree),/could not be verified/);assert.equal(button(s.tree,'Send').props.disabled,true);
  await act(async()=>s.tree.unmount());await s.mount();assert.match(shown(s.tree),/Send outcome uncertain/);assert.equal(button(s.tree,'Send').props.disabled,true);
  await act(async()=>button(s.tree,'Check Sent status').props.onClick());assert.equal(s.f.sends().length,1);
  s.f.prove();await act(async()=>button(s.tree,'Check Sent status').props.onClick());assert.equal(s.f.sends().length,1);assert.equal(s.f.sent,ID);
 }finally{await s.close();}
});
test('reload during pending send ignores old UI response and preserves server-side attempt status',async()=>{
 const s=await setup();try{
  await send(s.tree);await act(async()=>s.tree.unmount());await s.mount();assert.match(shown(s.tree),/Send outcome uncertain/);
  await act(async()=>s.f.finish('uncertain'));assert.equal(s.f.sent,undefined);assert.equal(button(s.tree,'Send').props.disabled,true);
  await act(async()=>button(s.tree,'Check Sent status').props.onClick());assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
test('send history remains visible when a sent draft is no longer available to reopen',async()=>{
 const s=await setup({lost:true});try{
  await send(s.tree);await act(async()=>s.tree.unmount());await s.mount({...s.props,providerMessageId:undefined});
  assert.match(shown(s.tree),/New message/);assert.match(shown(s.tree),/Send outcome uncertain/);assert.ok(button(s.tree,'Check Sent status'));assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
for(const code of ['microsoft_send_permission_required','microsoft_reconnect_required','microsoft_draft_send_conflict','microsoft_compose_body_unverifiable'])test('definite not-started status is distinct from uncertainty: '+code,async()=>{
 const s=await setup({notStarted:code});try{
  await send(s.tree);assert.match(shown(s.tree),/Sending confirmed not started/);assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(s.f.sent,undefined);
  if(code==='microsoft_compose_body_unverifiable')assert.match(shown(s.tree),/original message body cannot be verified as plain text/);
  if(code==='microsoft_send_permission_required')assert.match(shown(s.tree),/Mail.Send authorization/);
  const handler=button(s.tree,'Send').props.onClick;await act(async()=>handler());assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
test('mailbox switches discard late send callbacks and keep the old attempt bound to its mailbox',async()=>{
 const s=await setup();try{
  await send(s.tree);
  await act(async()=>s.tree.update(React.createElement(MicrosoftComposeDraftPanel,{...s.props,mailboxId:'microsoft-other',mailboxEmail:'other@example.com',providerMessageId:undefined})));
  await act(async()=>s.f.finish());assert.equal(s.f.sent,undefined);assert.equal(s.f.sends()[0].p.mailboxId,BOX);assert.equal(s.f.sends().length,1);
 }finally{await s.close();}
});
test('status failures fail closed and can be retried as read-only checks',async()=>{
 const s=await setup({statusError:'microsoft_reconnect_required'});try{
  assert.equal(button(s.tree,'Send').props.disabled,true);assert.match(shown(s.tree),/Reconnect this Microsoft mailbox/);assert.doesNotMatch(shown(s.tree),/private token/);
  s.f.options.statusError=null;await act(async()=>button(s.tree,'Reload send status').props.onClick());assert.equal(button(s.tree,'Send').props.disabled,false);assert.equal(s.f.sends().length,0);
 }finally{await s.close();}
});
test('receipt parser rejects delivery claims, cross-mailbox responses and false confirmations',async()=>{
 const previous=globalThis.fetch;const r={attemptId:'a'.repeat(43),providerMessageId:ID,outcome:'confirmed_performed',providerAccepted:true,sentCopyConfirmed:true,deliveryConfirmed:false,reason:null};
 try{for(const changed of [{deliveryConfirmed:true},{providerAccepted:false},{sentCopyConfirmed:false},{outcome:'uncertain',sentCopyConfirmed:true,providerAccepted:false},{reason:'private'}]){
  globalThis.fetch=async()=>response({ok:true,mailboxId:BOX,receipt:{...r,...changed}});
  assert.equal((await sendApi.checkMicrosoftDraftSend({mailboxId:BOX,attemptId:r.attemptId})).ok,false);
 }
 globalThis.fetch=async()=>response({ok:true,mailboxId:'other',attempts:[r]});assert.equal((await sendApi.inspectMicrosoftDraftSends({mailboxId:BOX})).ok,false);
 }finally{globalThis.fetch=previous;}
});
test('source release gates stay off and disabled controls cannot invoke a send',async()=>{
 const fs=require('node:fs'),path=require('node:path');assert.match(fs.readFileSync(path.join(__dirname,'../../lib/microsoftDraftSendApi.ts'),'utf8'),/MICROSOFT_DRAFT_SEND_RELEASED = false/);
 assert.doesNotMatch(fs.readFileSync(path.join(__dirname,'MicrosoftDraftSendControls.tsx'),'utf8'),/localStorage|sessionStorage/);
 sendApi.MICROSOFT_DRAFT_SEND_RELEASED=false;const s=await setup();try{assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(button(s.tree,'Send').props.onClick,undefined);assert.equal(s.f.sends().length,0);}finally{await s.close();sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;}
});


test('Send shutdown preserves uncertain history and locks all Compose mutations after remount',async()=>{
 const s=await setup({lost:true});try{
  await send(s.tree);await act(async()=>s.tree.unmount());
  sendApi.MICROSOFT_DRAFT_SEND_RELEASED=false;
  await s.mount();
  assert.match(shown(s.tree),/Send outcome uncertain/);
  assert.equal(button(s.tree,'Send').props.disabled,true);
  assert.equal(button(s.tree,'Send').props.onClick,undefined);
  assert.equal(button(s.tree,'Save draft').props.disabled,true);
  assert.equal(field(s.tree,'Choose Compose attachments').props.disabled,true);
  await act(async()=>button(s.tree,'Check Sent status').props.onClick());
  assert.equal(s.f.sends().length,1);
  s.f.prove();await act(async()=>button(s.tree,'Check Sent status').props.onClick());
  assert.match(shown(s.tree),/Sending content confirmed by comparison with the approved manifest and exact Sent Items copy/);
  assert.equal(s.f.sends().length,1);
 }finally{await s.close();sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;}
});
for(const outcome of ['confirmed_performed','confirmed_not_started','uncertain'])test('reconnected historical '+outcome+' remains visible while Send is off',async()=>{
 const id='h'.repeat(43),receipt={attemptId:id,providerMessageId:ID,outcome,providerAccepted:outcome==='confirmed_performed',sentCopyConfirmed:outcome==='confirmed_performed',deliveryConfirmed:false,reason:null};
 sendApi.MICROSOFT_DRAFT_SEND_RELEASED=false;
 const s=await setup({initial:[[id,receipt]]});try{
  await act(async()=>s.tree.unmount());await s.mount();
  assert.match(shown(s.tree),outcome==='confirmed_performed'?/Sending content confirmed by/:outcome==='confirmed_not_started'?/Sending confirmed not started/:/Send outcome uncertain/);
  assert.equal(button(s.tree,'Send').props.disabled,true);
  assert.equal(s.f.sends().length,0);
  if(outcome==='uncertain'){
   await act(async()=>button(s.tree,'Check Sent status').props.onClick());
   assert.equal(s.f.sends().length,0);
  }
 }finally{await s.close();sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;}
});
test('disabled Send client rejects an explicit send without fetch but permits validated status reads',async()=>{
 const previous=globalThis.fetch,calls=[];
 sendApi.MICROSOFT_DRAFT_SEND_RELEASED=false;
 globalThis.fetch=async(url,init)=>{const p=JSON.parse(init.body);calls.push(p);return response({ok:true,mailboxId:BOX,attempts:[]});};
 try{
  const result=await sendApi.sendMicrosoftExistingDraft({mailboxId:BOX,attemptId:'a'.repeat(43),providerMessageId:ID,etag:'W/"v1"',snapshotHash:'a'.repeat(64)});
  assert.equal(result.ok,false);assert.equal(calls.length,0);
  assert.equal((await sendApi.inspectMicrosoftDraftSends({mailboxId:BOX})).ok,true);
  assert.deepEqual(calls,[{action:'status',mailboxId:BOX}]);
 }finally{globalThis.fetch=previous;sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;}
});

for (const release of [true, false]) test('content mismatch remains locked across remount and reconnect, send flag '+release, async()=>{
 const receipt={attemptId:'m'.repeat(43),providerMessageId:ID,outcome:'performed_content_mismatch',providerAccepted:true,sentCopyConfirmed:true,deliveryConfirmed:false,reason:null};
 sendApi.MICROSOFT_DRAFT_SEND_RELEASED=release;
 const s=await setup({initial:[[receipt.attemptId,receipt]]});
 try{
  assert.match(shown(s.tree),/content differs from the approved message/);
  assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(field(s.tree,'Compose subject').props.disabled,true);
  assert.equal(button(s.tree,'Check Sent status'),undefined);assert.equal(s.f.sent,undefined);
  await act(async()=>s.tree.unmount());await s.mount({...s.props,connected:false});
  assert.match(shown(s.tree),/Do not send again/);
  await act(async()=>s.tree.unmount());await s.mount({...s.props,connected:true});
  assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(s.f.sends().length,0);
 }finally{await s.close();sendApi.MICROSOFT_DRAFT_SEND_RELEASED=true;}
});
test('historical Sent identity without a content manifest remains uncertain and locked',async()=>{
 const receipt={attemptId:'h'.repeat(43),providerMessageId:ID,outcome:'uncertain',providerAccepted:true,sentCopyConfirmed:true,deliveryConfirmed:false,reason:null};
 const s=await setup({initial:[[receipt.attemptId,receipt]]});
 try{assert.match(shown(s.tree),/approved content cannot be verified/);assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(s.f.sent,undefined);}
 finally{await s.close();}
});
test('mismatch without exact Sent proof is rejected by the client',async()=>{
 const receipt={attemptId:'i'.repeat(43),providerMessageId:ID,outcome:'performed_content_mismatch',providerAccepted:true,sentCopyConfirmed:false,deliveryConfirmed:false,reason:null};
 const s=await setup({initial:[[receipt.attemptId,receipt]]});
 try{assert.match(shown(s.tree),/could not be verified/);assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(s.f.sent,undefined);}
 finally{await s.close();}
});


test('all older blockers remain findable through chronological history pages and reload',async()=>{
 const initial=Array.from({length:105},(_,i)=>{const r={attemptId:String(999-i).padStart(43,'a'),providerMessageId:'draft-'+i,outcome:i===0?'uncertain':i===1?'performed_content_mismatch':'confirmed_performed',providerAccepted:true,sentCopyConfirmed:i!==0,deliveryConfirmed:false,reason:null};return [r.attemptId,r];});
 const s=await setup({initial},{providerMessageId:null});
 try{
  assert.equal(s.tree.root.findAll(n=>n.props['data-send-attempt']).length,32);
  for(let i=0;i<3;i++)await act(async()=>button(s.tree,'Load older send history').props.onClick());
  const nodes=s.tree.root.findAll(n=>n.props['data-send-attempt']);
  assert.equal(nodes.length,105);assert.deepEqual(nodes.map(n=>n.props['data-send-attempt']),initial.map(([id])=>id).reverse());
  assert.match(shown(s.tree),/content differs from the approved message/);assert.ok(button(s.tree,'Check Sent status'));
  assert.equal(button(s.tree,'Load older send history'),undefined);
  await act(async()=>button(s.tree,'Reload send status').props.onClick());
  assert.equal(s.tree.root.findAll(n=>n.props['data-send-attempt']).length,105);
  await act(async()=>s.tree.unmount());await s.mount();
  for(let i=0;i<3;i++)await act(async()=>button(s.tree,'Load older send history').props.onClick());
  assert.equal(s.tree.root.findAll(n=>n.props['data-send-attempt']).length,105);assert.equal(s.f.sends().length,0);
 }finally{await s.close();}
});
test('old current draft blocker outside first page still locks Compose immediately',async()=>{
 const initial=Array.from({length:40},(_,i)=>{const r={attemptId:String(i).padStart(43,'b'),providerMessageId:i===0?ID:'other-'+i,outcome:i===0?'uncertain':'confirmed_performed',providerAccepted:true,sentCopyConfirmed:i!==0,deliveryConfirmed:false,reason:null};return [r.attemptId,r];});
 const s=await setup({initial});
 try{assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(field(s.tree,'Compose subject').props.disabled,true);assert.ok(button(s.tree,'Check Sent status'));assert.equal(s.f.sends().length,0);}
 finally{await s.close();}
});
test('history page storage failure preserves known blockers and disables new mutations',async()=>{
 const initial=Array.from({length:40},(_,i)=>{const r={attemptId:String(i).padStart(43,'c'),providerMessageId:'other-'+i,outcome:'uncertain',providerAccepted:true,sentCopyConfirmed:false,deliveryConfirmed:false,reason:null};return [r.attemptId,r];});
 const s=await setup({initial});
 try{s.f.options.statusError='microsoft_draft_send_unavailable';await act(async()=>button(s.tree,'Load older send history').props.onClick());assert.equal(s.tree.root.findAll(n=>n.props['data-send-attempt']).length,32);assert.equal(button(s.tree,'Send').props.disabled,true);assert.equal(field(s.tree,'Compose subject').props.disabled,true);}
 finally{await s.close();}
});
test('history client rejects repeated cursors, gaps, malformed ordering and foreign current draft',async()=>{
 const previous=globalThis.fetch;
 const receipt={attemptId:'x'.repeat(43),providerMessageId:ID,outcome:'uncertain',providerAccepted:true,sentCopyConfirmed:false,deliveryConfirmed:false,reason:null};
 const entry={receipt,sequence:5,createdAt:'2026-10-26T12:00:00+00:00'};
 const base={ok:true,mailboxId:BOX,entries:[entry],nextCursor:5,currentDraft:entry};
 try{for(const changed of [{nextCursor:6},{entries:[entry,entry]},{currentDraft:{...entry,receipt:{...receipt,providerMessageId:'other'}}},{entries:[{...entry,sequence:7}]},{entries:[{...entry,createdAt:'invalid'}]}]){
  globalThis.fetch=async()=>response({...base,...changed});
  assert.equal((await sendApi.listMicrosoftDraftSendHistory({mailboxId:BOX,before:6,providerMessageId:ID})).ok,false);
 }}finally{globalThis.fetch=previous;}
});


test('a stale history refresh cannot replace a known content mismatch',async()=>{
 const receipt={attemptId:'m'.repeat(43),providerMessageId:ID,outcome:'performed_content_mismatch',providerAccepted:true,sentCopyConfirmed:true,deliveryConfirmed:false,reason:null};
 const s=await setup({initial:[[receipt.attemptId,receipt]]});
 try{s.f.attempts.set(receipt.attemptId,{...receipt,outcome:'confirmed_performed'});await act(async()=>button(s.tree,'Reload send status').props.onClick());assert.match(shown(s.tree),/content differs from the approved message/);assert.equal(s.f.sent,undefined);assert.equal(button(s.tree,'Send').props.disabled,true);}
 finally{await s.close();}
});
