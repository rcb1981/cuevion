const assert=require('node:assert/strict');
const {test}=require('node:test');
const React=require('react');
const {act,create}=require('react-test-renderer');
require('sucrase/register/ts');require('sucrase/register/tsx');
const api=require('../../lib/microsoftComposeDraftApi');
const attachmentPolicy=require('../../lib/microsoftDraftAttachmentPolicy');
assert.equal(api.MICROSOFT_RESPONSE_DRAFTS_RELEASED,false);
assert.equal(api.MICROSOFT_COMPOSE_DRAFTS_RELEASED,false);
api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=true;api.MICROSOFT_RESPONSE_DRAFTS_RELEASED=true;
attachmentPolicy.MICROSOFT_DRAFT_ATTACHMENTS_RELEASED=true;
globalThis.crypto??=require('node:crypto').webcrypto;
const {MicrosoftComposeDraftPanel}=require('./MicrosoftComposeDraftPanel');
const text=n=>typeof n==='string'?n:(n?.children??[]).map(text).join('');
const button=(t,name)=>t.root.findAllByType('button').find(n=>text(n)===name);
const field=(t,name)=>t.root.findByProps({'aria-label':name});
const response=(data,status=200)=>({ok:status===200,status,json:async()=>data});
function fixture(options={}){
 const calls=[],operations=new Map(),drafts=new Map();let pending;
 const editFor=mode=>({subject:mode==='forward'?'Fw: Original':'Re: Original',body:'',
   to:mode==='forward'?[]:['reply@example.com'],cc:mode==='reply_all'?['other@example.com']:[],bcc:[]});
 const context=mode=>({mode,quotePreview:'Original <script>never execute</script> complete quote'});
 function finish(){
   const {p,resolve}=pending;
   const id=p.providerMessageId??'saved-response';
   const receipt={requestId:p.requestId,action:p.action==='update'?'update':'create',providerMessageId:id,outcome:'confirmed_performed',blocked:false};
   drafts.set(id,{providerMessageId:id,etag:'W/"v2"',snapshotHash:'b'.repeat(64),edit:p.edit,
     response:context(p.mode??drafts.get(id).response.mode)});
   operations.set(p.requestId,receipt);resolve(response({ok:true,mailboxId:p.mailboxId,receipt}));
 }
 const fetch=async(url,init)=>{
   const p=JSON.parse(init.body);calls.push({url,p});assert.equal(init.method,'POST');assert.equal(init.credentials,'include');
   if(url.endsWith('send-microsoft-draft')){assert.equal(p.action,'history');return response({ok:true,mailboxId:p.mailboxId,entries:[],nextCursor:null,currentDraft:null});}
   assert.ok(!url.endsWith('send-microsoft'),'legacy send route forbidden');
   if(url.endsWith('mutate-microsoft-draft-attachments')){
     if(p.action==='draft_status')return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,requests:[]});
     assert.equal(p.action,'load');return response({ok:true,mailboxId:p.mailboxId,providerMessageId:p.providerMessageId,snapshotHash:'b'.repeat(64),complete:true,attachments:[]});
   }
   assert.ok(url.endsWith('microsoft-compose-draft'));
   if(p.action==='status')return response({ok:true,mailboxId:p.mailboxId,operations:[...operations.values()]});
   if(p.action==='prepare_response'){
     if(options.reconnect)return response({ok:false,error:{code:'microsoft_reconnect_required'}},409);
     return response({ok:true,mailboxId:p.mailboxId,sourceHash:'a'.repeat(64),edit:editFor(p.mode),response:context(p.mode)});
   }
   if(p.action==='load')return response({ok:true,mailboxId:p.mailboxId,...drafts.get(p.providerMessageId)});
   assert.ok(['create_response','update'].includes(p.action));
   operations.set(p.requestId,{requestId:p.requestId,action:p.action==='update'?'update':'create',providerMessageId:p.providerMessageId??null,outcome:'uncertain',blocked:true});
   if(options.uncertain)return response({ok:true,mailboxId:p.mailboxId,receipt:operations.get(p.requestId)});
   const promise=new Promise(resolve=>{pending={p,resolve};});
   if(options.autoSave)finish();
   return promise;
 };
 return {calls,fetch,finish,operations,drafts};
}
const props={mailboxId:'microsoft-box',mailboxEmail:'music@example.com',connected:true,onClose(){},onSaved(){},onOpenDrafts(){}};
async function mount(f,mode,extra={}){global.fetch=f.fetch;let t;await act(async()=>{t=create(React.createElement(MicrosoftComposeDraftPanel,{...props,responseMode:mode,sourceProviderMessageId:'source-1',...extra}));});return t;}
async function fill(t,label,value){await act(async()=>field(t,label).props.onChange({target:{value}}));}
for(const mode of ['reply','reply_all','forward'])test(mode+' prepares recipients, saves native draft, reopens and updates in existing Compose',async()=>{
 const f=fixture({autoSave:true});let t=await mount(f,mode);
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,0,'opening is read-only');
 assert.equal(field(t,'Compose To').props.value,mode==='forward'?'':'reply@example.com');
 if(mode==='reply_all')assert.equal(field(t,'Compose Cc').props.value,'other@example.com');
 if(mode==='forward')await fill(t,'Compose To','recipient@example.com');
 await fill(t,'Compose message body','My response');
 await act(async()=>button(t,'Save draft').props.onClick());
 const createCall=f.calls.find(c=>c.p.action==='create_response');assert.ok(createCall);assert.equal(createCall.p.mode,mode);
 assert.equal(createCall.p.sourceProviderMessageId,'source-1');assert.equal(createCall.p.edit.body,'My response');
 assert.ok(text(t.toJSON()).includes('Nothing was sent'));
 assert.ok(text(t.toJSON()).includes('Original <script>never execute</script> complete quote'));
 assert.equal(t.root.findAllByType('iframe').length,0);assert.equal(t.root.findAllByType('script').length,0);
 await act(async()=>t.unmount());
 t=await mount(f,undefined,{sourceProviderMessageId:undefined,providerMessageId:'saved-response'});
 assert.equal(field(t,'Compose message body').props.value,'My response');
 await fill(t,'Compose message body','Edited after reload');
 await act(async()=>button(t,'Save draft').props.onClick());
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
 const update=f.calls.find(c=>c.p.action==='update');assert.equal(update.p.providerMessageId,'saved-response');assert.equal(update.p.edit.body,'Edited after reload');
 await act(async()=>t.unmount());
});
test('double click has one request and unresolved status cannot create again after remount',async()=>{
 const f=fixture({uncertain:true});let t=await mount(f,'reply');
 await fill(t,'Compose message body','Response');
 const save=button(t,'Save draft').props.onClick;
 await act(async()=>{save();save();});
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
 assert.ok(button(t,'Save draft').props.disabled);
 await act(async()=>button(t,'Check draft status').props.onClick());
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
 await act(async()=>t.unmount());t=await mount(f,'reply');
 assert.ok(button(t,'Save draft').props.disabled);
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
 await act(async()=>t.unmount());
});
test('lost browser response recovers recorded draft through status without another create',async()=>{
 const f=fixture({autoSave:true});const original=f.fetch;f.fetch=async(url,init)=>{
   const result=await original(url,init);if(JSON.parse(init.body).action==='create_response')throw Error('lost browser ACK');return result;
 };
 const t=await mount(f,'forward');await fill(t,'Compose To','recipient@example.com');await fill(t,'Compose message body','Forward comment');
 await act(async()=>button(t,'Save draft').props.onClick());assert.ok(button(t,'Save draft').props.disabled);
 await act(async()=>button(t,'Check draft status').props.onClick());
 assert.equal(field(t,'Compose message body').props.value,'Forward comment');
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
 await act(async()=>t.unmount());
});
test('reconnect error blocks saving and successful status recheck is read-only',async()=>{
 const options={reconnect:true};const f=fixture(options);const t=await mount(f,'reply');
 assert.ok(text(t.toJSON()).includes('Reconnect'));assert.ok(button(t,'Save draft').props.disabled);
 options.reconnect=false;await act(async()=>button(t,'Check draft status').props.onClick());
 assert.equal(button(t,'Save draft').props.disabled,false);
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,0);
 await act(async()=>t.unmount());
});
test('response source gate off forbids prepare and create even with valid recipient',async()=>{
 api.MICROSOFT_RESPONSE_DRAFTS_RELEASED=false;const f=fixture();global.fetch=f.fetch;
 const prepared=await api.prepareMicrosoftResponseDraft({mailboxId:props.mailboxId,mode:'reply',sourceProviderMessageId:'source-1'});
 assert.equal(prepared.ok,false);assert.equal(f.calls.length,0);
 const result=await api.createMicrosoftResponseDraft({mailboxId:props.mailboxId,mode:'reply',sourceProviderMessageId:'source-1',sourceHash:'a'.repeat(64),requestId:'r'.repeat(43),edit:{subject:'Re:',body:'text',to:['reply@example.com'],cc:[],bcc:[]}});
 assert.equal(result.ok,false);assert.equal(f.calls.length,0);api.MICROSOFT_RESPONSE_DRAFTS_RELEASED=true;
});

for(const [labelName,mode] of [['Reply','reply'],['Reply All','reply_all'],['Forward','forward']])test('mailbox toolbar '+labelName+' opens the draft Compose flow only',async()=>{
 const oldWindow=global.window;global.window={setInterval(){return 1;},clearInterval(){}};
 const {MicrosoftMailboxReadOnlyView}=require('./MicrosoftMailboxReadOnlyView');
 const f=fixture();const original=f.fetch;
 const snapshot=full=>({id:'source-1',providerMessageId:'source-1',providerThreadId:'thread-1',serverMailboxId:props.mailboxId,
   providerFolder:'INBOX',labelIds:['INBOX'],sender:'Artist',from:'artist@example.com',to:'music@example.com',cc:'',subject:'Original discussion',
   snippet:'Original text',timestamp:'Today',createdAt:'2026-10-26T12:00:00Z',body:full?['Original text']:[],unread:false,flagged:false,attachments:[],hasAttachments:false});
 global.fetch=async(url,init)=>{
   if(url.endsWith('fetch-microsoft'))return response({ok:true,messages:[snapshot(false)],inboxUidSet:['source-1'],uidValidity:'microsoft-graph-immutable',hasMore:false});
   if(url.endsWith('fetch-microsoft-message'))return response({ok:true,message:snapshot(true)});
   if(url.endsWith('fetch-microsoft-attachments'))return response({ok:true,attachments:[],hasMore:false});
   return original(url,init);
 };
 let t;await act(async()=>{t=create(React.createElement(MicrosoftMailboxReadOnlyView,{mailbox:{id:props.mailboxId,email:props.mailboxEmail,provider:'microsoft',connectionStatus:'connected'},connected:true,onBack(){},onOpenConnectionSettings(){}}));});
 const row=t.root.findAllByType('button').find(n=>text(n).includes('Original discussion'));
 assert.ok(row,'source row exists');await act(async()=>row.props.onClick());
 const action=field(t,labelName);assert.equal(action.props.disabled,false);await act(async()=>action.props.onClick());
 assert.equal(t.root.findAllByProps({'aria-label':'Microsoft draft composer'}).length,1);
 assert.equal(f.calls.find(c=>c.p.action==='prepare_response').p.mode,mode);
 assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,0);
 await act(async()=>t.unmount());global.window=oldWindow;
});

for(const mode of ['reply','reply_all','forward'])test(mode+' rich comment keeps quote outside editor and reopens formatting',async()=>{
 const f=fixture({autoSave:true});let t=await mount(f,mode);
 try{
  if(mode==='forward')await fill(t,'Compose To','recipient@example.com');
  await act(async()=>button(t,'Formatting').props.onClick());
  const {MicrosoftComposeBodyEditor}=require('./MicrosoftComposeBodyEditor');
  await act(async()=>t.root.findByType(MicrosoftComposeBodyEditor).props.onChange('<p>My <strong>comment</strong></p>'));
  await act(async()=>button(t,'Save draft').props.onClick());
  assert.equal(f.calls.find(c=>c.p.action==='create_response').p.edit.bodyType,'html');
  assert.equal(t.root.findByType(MicrosoftComposeBodyEditor).props.value,'<p>My <strong>comment</strong></p>');
  assert.ok(text(t.toJSON()).includes('Original <script>never execute</script> complete quote'));
  assert.equal(t.root.findAllByProps({contentEditable:true}).length,1);
  assert.equal(button(t,'Send').props.disabled,true);
 }finally{await act(async()=>t.unmount());}
});

for(const mode of ['reply','forward'])test(mode+' account-bound draft acceptance keeps global gates OFF',async()=>{
 api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=false;api.MICROSOFT_RESPONSE_DRAFTS_RELEASED=false;
 const f=fixture({autoSave:true});let t=await mount(f,mode,{acceptance:{canWrite:true}});
 try{
  if(mode==='forward')await fill(t,'Compose To','recipient@example.com');
  await fill(t,'Compose message body','Test draft only');
  await act(async()=>button(t,'Save draft').props.onClick());
  assert.equal(f.calls.filter(c=>c.p.action==='create_response').length,1);
  assert.equal(button(t,'Send').props.disabled,true);
  assert.equal(field(t,'Add Compose attachments').props.disabled,true);
 }finally{await act(async()=>t.unmount());api.MICROSOFT_COMPOSE_DRAFTS_RELEASED=true;api.MICROSOFT_RESPONSE_DRAFTS_RELEASED=true;}
});
