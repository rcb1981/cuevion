import assert from "node:assert/strict";
import { fetchMicrosoftAttachments, downloadMicrosoftAttachmentFile } from "./microsoftAttachmentReadApi";
const BOX="microsoft-0123456789abcdefabcd12345678", ID="AAMkMessage.1/";
const request={mailboxId:BOX,providerMessageId:ID,folder:"Inbox" as const};
const row={id:"file-1",name:"sample.pdf",size:100,contentType:"application/pdf",isInline:false,kind:"file"};
async function run() {
 const previous=globalThis.fetch;
 let body:unknown;let calls=0;
 globalThis.fetch=(async()=>{calls++;return {ok:true,status:200,json:async()=>body} as Response;}) as typeof fetch;
 try {
  for(const count of [26,100]) {
   body={ok:true,attachments:Array.from({length:count},(_,i)=>({...row,id:"file-"+i})),hasMore:count===100};
   const result=await fetchMicrosoftAttachments(request);
   assert.equal(result.ok,true);assert.equal(result.attachments?.length,count);
  }
  for(const bad of [
   {ok:true,attachments:Array.from({length:101},(_,i)=>({...row,id:"file-"+i})),hasMore:true},
   {ok:true,attachments:[row,row],hasMore:false},
   {ok:true,attachments:[{...row,size:-1}],hasMore:false},
   {ok:true,attachments:[{...row,isInline:null}],hasMore:false},
   {ok:true,attachments:[{...row,name:null}],hasMore:false},
   {ok:true,attachments:[{...row,contentType:123}],hasMore:false},
   {ok:true,attachments:[],nextLink:"https://evil.example"},
   {ok:true},
  ]) {body=bad;assert.equal((await fetchMicrosoftAttachments(request)).ok,false);}
  calls=0;
  assert.equal((await fetchMicrosoftAttachments({...request,mailboxId:"other mailbox"})).ok,false);
  assert.equal(calls,0);
  const fileRequest={...request,attachmentId:"file-1"};
  const png="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC";
  body={ok:true,attachmentId:"file-1",fileName:"logo.png",size:Buffer.from(png,"base64").length,base64:png,sizeVerifiedAsPng:true};
  const accepted=await downloadMicrosoftAttachmentFile(fileRequest);
  assert.equal(accepted.ok,true);assert.equal(accepted.sizeVerifiedAsPng,true);
  for(const flag of [false,"true",{},1]) {
   body={ok:true,attachmentId:"file-1",fileName:"x.txt",size:4,base64:"dGVzdA==",sizeVerifiedAsPng:flag};
   assert.equal((await downloadMicrosoftAttachmentFile(fileRequest)).ok,false);
  }
  body={ok:true,attachmentId:"file-1",fileName:"x.txt",size:4,base64:"dGVzdA==",sizeVerifiedAsPng:true};
  assert.equal((await downloadMicrosoftAttachmentFile(fileRequest)).ok,false);
  const oldWindow=globalThis.window;
  const events:string[]=[];
  try {
   globalThis.window={dispatchEvent:(event:Event)=>{events.push(event.type);return true;}} as unknown as Window & typeof globalThis;
   globalThis.fetch=(async()=>({ok:false,status:401,json:async()=>({ok:false,error:{code:"microsoft_unauthorized",message:"untrusted private detail"}})} as Response)) as typeof fetch;
   const denied=await fetchMicrosoftAttachments(request);
   assert.equal(denied.ok,false);assert.equal(denied.error?.message,"Please sign in again.");
   assert.deepEqual(events,["cuevion:auth-session-recheck"]);
  } finally {globalThis.window=oldWindow;}
 } finally {globalThis.fetch=previous;}
 console.log("Microsoft attachment page cap, malformed metadata and PNG-proof client regressions: PASS");
}
void run();
