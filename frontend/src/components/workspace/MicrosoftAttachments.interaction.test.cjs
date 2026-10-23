const assert=require("node:assert/strict");
const {test}=require("node:test");
const React=require("react");
const {act,create}=require("react-test-renderer");
const {renderToStaticMarkup}=require("react-dom/server");
require("sucrase/register/ts");require("sucrase/register/tsx");
require("react-dom").createPortal=children=>children;
const learning=require("./useMicrosoftFilteredLearning");
learning.useMicrosoftFilteredLearning=()=>({learned:{},placements:{},rules:{},ready:true,error:null});
const {MicrosoftAttachmentList,microsoftAttachmentName}=require("./MicrosoftAttachmentList");
const {MicrosoftMailboxReadOnlyView}=require("./MicrosoftMailboxReadOnlyView");
const {MicrosoftMessageListRow}=require("./MicrosoftMessageListRow");
const {MicrosoftFullMessageModal}=require("./MicrosoftFullMessageModal");
const {MicrosoftInertHtmlStage}=require("./MicrosoftInertHtmlStage");
const BOX="microsoft-0123456789abcdefabcd12345678", ID="AAMkMessage.1/", OTHER="AAMkMessage.2/";
const row=(overrides={})=>({id:"file-1",name:"invoice.pdf",size:2048,contentType:"application/pdf",isInline:false,kind:"file",...overrides});
const inline=row({id:"inline-1",name:"logo.png",isInline:true,contentId:"logo@message",contentType:"image/png"});
const reply=(body,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body});
const text=n=>typeof n==="string"?n:(n?.children||[]).map(text).join("");
const cards=props=>renderToStaticMarkup(React.createElement(MicrosoftAttachmentList,{folder:"Inbox",onDownload(){},...props}));
test("reference-shaped cards label supported file types and readable sizes",()=>{
 const values=[["invoice.pdf","application/pdf","PDF"],["memo.docx","application/octet-stream","TXT"],
  ["photo.jpg","image/jpeg","IMG"],["budget.xlsx","application/vnd.ms-excel","XLS"],
  ["demo.wav","audio/wav","AUD"],["movie.mp4","video/mp4","VID"],["files.zip","application/zip","ZIP"],
  ["unknown.bin","application/octet-stream","FILE"]];
 for(const [name,mime,icon] of values) {
  const markup=cards({attachments:[row({name,contentType:mime})]});
  assert.match(markup,new RegExp(icon));assert.match(markup,/2 KB/);
  assert.match(markup,/rounded-\[18px\]/);assert.match(markup,/aria-label="Download /);
 }
 assert.match(cards({attachments:[row({size:1048576})]}),/1.0 MB/);
 assert.match(cards({attachments:[row({size:0})]}),/0 B/);
});
test("inline files remain separate from downloadable ordinary cards",()=>{
 const html=cards({attachments:[row(),inline,row({id:"inline-2",isInline:false,contentId:"other@message",name:"hidden.png"})]});
 assert.match(html,/invoice.pdf/);assert.doesNotMatch(html,/logo.png|hidden.png/);
 assert.equal(cards({attachments:[inline]}),"");
});
test("unsafe names are inert text; path fragments and bidi controls do not disguise downloads",()=>{
 const name='../<img src=x onerror=alert(1)>.pdf';
 const html=cards({attachments:[row({name})]});
 assert.doesNotMatch(html,/<img/);assert.match(html,/&lt;img/);
 assert.equal(microsoftAttachmentName("C:\\temp\\bad\nname\u202e.pdf"),"bad_name_.pdf");
});
test("invalid metadata, missing fields and duplicates fail closed",()=>{
 for(const attachments of [[{...row(),size:NaN}],[{...row(),name:null}],[{...row(),isInline:undefined}],
                            [null],[row(),row()],Array.from({length:101},(_,i)=>row({id:String(i)}))]) {
  const html=cards({attachments});assert.match(html,/role="alert"/);assert.doesNotMatch(html,/<button/);
 }
});
test("Junk, oversized files and unsupported attachment kinds stay non-downloadable",()=>{
 for(const props of [{folder:"Junk",attachments:[row()]},{attachments:[row({size:2097153})]},
  {attachments:[row({kind:"item"})]},{attachments:[row({kind:"reference"})]}]) {
  assert.match(cards(props),/disabled=""/);
 }
 assert.match(cards({attachments:[row()],hasMore:true}),/first 100 attachments/);
});

async function mailbox(options,check) {
 const old={fetch:globalThis.fetch,window:globalThis.window,document:globalThis.document,
  create:URL.createObjectURL,revoke:URL.revokeObjectURL};
 const calls=[],saved=[],alerts=[];let tree;
 const attachments=options.attachments??[row(),inline];
 const snapshot=(id,full)=>({id,providerMessageId:id,providerThreadId:"thread-"+id,
  serverMailboxId:options.wrongMailbox&&full?"microsoft-other":BOX,providerFolder:"INBOX",labelIds:["INBOX"],
  sender:"Sender",from:"sender@example.com",to:"owner@example.com",subject:id===ID?"Attachment message":"Other message",
  snippet:"Preview",createdAt:"2026-10-23T12:00:00Z",timestamp:"Today",unread:false,flagged:false,
  attachments:[],hasAttachments:options.inlineOnly?false:true,body:full?["Message body"]:[],
  ...(full&&options.html?{bodyHtml:'<p>HTML body</p><img data-microsoft-cid="logo@message">'}:{})});
 globalThis.window={setInterval:()=>1,clearInterval(){},setTimeout:fn=>{fn();return 1;},alert:msg=>alerts.push(msg)};
 globalThis.document={addEventListener(){},removeEventListener(){},body:{appendChild(){}},
  createElement:()=>({style:{},click(){saved.push(this.download);},remove(){}})};
 URL.createObjectURL=()=>"blob:offline-attachment";URL.revokeObjectURL=()=>{};
 globalThis.fetch=async(url,init)=>{
  const payload=JSON.parse(init.body);calls.push({url,payload});assert.equal(payload.mailboxId,BOX);
  if(url.endsWith("fetch-microsoft")) return reply({ok:true,messages:[snapshot(ID,false),snapshot(OTHER,false)],
   inboxUidSet:[ID,OTHER],uidValidity:"microsoft-graph-immutable",hasMore:false});
  if(url.endsWith("fetch-microsoft-message"))return reply({ok:true,message:snapshot(payload.providerMessageId,true)});
  if(url.endsWith("fetch-microsoft-attachments"))return options.metadataError?reply({ok:true}):reply({ok:true,attachments,hasMore:options.hasMore??false});
  if(url.endsWith("download-microsoft-attachment")) {
   if(payload.attachmentId==="inline-1") {
    const png="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC";
    return reply({ok:true,attachmentId:"inline-1",fileName:"logo.png",size:Buffer.from(png,"base64").length,
      base64:png,sizeVerifiedAsPng:true});
   }
   if(options.pendingDownload)return options.pendingDownload;
   if(options.downloadFailure)return reply({ok:false,error:{code:"microsoft_graph_unavailable",message:"untrusted provider message"}},503);
   return reply(options.download??{ok:true,attachmentId:"file-1",fileName:"invoice.pdf",size:2048,base64:Buffer.alloc(2048).toString("base64")});
  }
  assert.fail("Unexpected HTTP request: "+url);
 };
 try {
  await act(async()=>{tree=create(React.createElement(MicrosoftMailboxReadOnlyView,{mailbox:{id:BOX,title:"Test",email:"owner@example.com"},connected:true,onBack(){},onOpenConnectionSettings(){}}));});
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenMessage(ID));
  await check({tree,calls,saved,alerts});
 } finally {
  if(tree)await act(async()=>tree.unmount());
  globalThis.fetch=old.fetch;globalThis.window=old.window;globalThis.document=old.document;
  URL.createObjectURL=old.create;URL.revokeObjectURL=old.revoke;
 }
}
const button=tree=>tree.root.findAllByType("button").find(n=>n.props["aria-label"]==="Download invoice.pdf");
test("real reader and full modal use the same cards and one bounded download",async()=>{
 await mailbox({},async({tree,calls,saved})=>{
  const list=tree.root.findByType(MicrosoftAttachmentList);
  assert.equal(list.props.attachments.length,2,"inline metadata remains available to the HTML/CID reader");
  assert.equal(tree.root.findAllByProps({"aria-label":"Download logo.png"}).length,0);
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenFullMessage(ID));
  assert.equal(tree.root.findAllByType(MicrosoftFullMessageModal).length,1);
  assert.equal(tree.root.findAllByType(MicrosoftAttachmentList).length,2);
  const modal=tree.root.findByType(MicrosoftFullMessageModal);
  const click=modal.findAllByType("button").find(n=>n.props["aria-label"]==="Download invoice.pdf").props.onClick;
  await act(async()=>{click();click();});
  assert.deepEqual(saved,["invoice.pdf"]);
  assert.equal(calls.filter(c=>c.url.endsWith("download-microsoft-attachment")).length,1);
 });
});
test("download failure retains cards, alerts safely and shows inline feedback",async()=>{
 await mailbox({downloadFailure:true},async({tree,alerts,saved})=>{
  await act(async()=>button(tree).props.onClick());
  assert.equal(alerts.length,1);assert.match(alerts[0],/could not be downloaded/i);
  assert.doesNotMatch(alerts[0],/untrusted/);assert.equal(saved.length,0);
  assert.equal(button(tree).props.disabled,false);
  assert.ok(tree.root.findAllByProps({role:"alert"}).length);
 });
});
test("wrong mailbox and incomplete metadata never provide a download action",async()=>{
 for(const options of [{wrongMailbox:true},{metadataError:true}]) await mailbox(options,async({tree,calls})=>{
  assert.equal(button(tree),undefined);
  assert.equal(calls.some(c=>c.url.endsWith("download-microsoft-attachment")),false);
 });
});
test("26 ordinary files render and the 100-file cap remains explicit",async()=>{
 await mailbox({attachments:Array.from({length:26},(_,i)=>row({id:"file-"+i,name:"file-"+i+".pdf"})),hasMore:true},async({tree})=>{
  assert.equal(tree.root.findAllByType("button").filter(n=>/^Download file-/.test(n.props["aria-label"]??"")).length,26);
  assert.match(text(tree.toJSON()),/first 100 attachments/);
 });
});
test("PNG size proof permits the validated mismatch; other mismatches stay blocked",async()=>{
 const png="iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGNgYGAAAAAEAAH2FzhVAAAAAElFTkSuQmCC";
 const file={ok:true,attachmentId:"file-1",fileName:"logo.png",size:Buffer.from(png,"base64").length,base64:png};
 for(const proof of [true,false]) await mailbox({download:{...file,...(proof?{sizeVerifiedAsPng:true}:{})}},async({tree,saved,alerts})=>{
  await act(async()=>button(tree).props.onClick());
  assert.equal(saved.length,proof?1:0);assert.equal(alerts.length,proof?0:1);
 });
});
test("late download response after another selection cannot save or alert",async()=>{
 let resolve;const pendingDownload=new Promise(done=>{resolve=done;});
 await mailbox({pendingDownload},async({tree,saved,alerts,calls})=>{
  await act(async()=>button(tree).props.onClick());
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[1].props.onOpenMessage(OTHER));
  await act(async()=>resolve(reply({ok:false,error:{code:"microsoft_graph_unavailable"}},503)));
  assert.equal(saved.length,0);assert.equal(alerts.length,0);
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenMessage(ID));
  await act(async()=>button(tree).props.onClick());
  assert.equal(calls.filter(c=>c.url.endsWith("download-microsoft-attachment")).length,2,
    "a completed stale request must not permanently block a later explicit download");
 });
});

test("inline-only HTML retains exact authenticated CID reads in reader and modal",async()=>{
 await mailbox({html:true,inlineOnly:true,attachments:[inline]},async({tree,calls,saved})=>{
  assert.equal(button(tree),undefined);
  const stage=tree.root.findByType(MicrosoftInertHtmlStage);
  assert.equal(stage.props.attachments[0].contentId,"logo@message");
  assert.equal(stage.props.mailboxId,BOX);assert.equal(stage.props.providerMessageId,ID);
  assert.match(stage.props.html,/data-microsoft-cid/);
  assert.equal(calls.filter(c=>c.url.endsWith("fetch-microsoft-attachments")).length,1);
  assert.ok(calls.some(c=>c.url.endsWith("download-microsoft-attachment")&&c.payload.attachmentId==="inline-1"));
  await act(async()=>tree.root.findAllByType(MicrosoftMessageListRow)[0].props.onOpenFullMessage(ID));
  const modal=tree.root.findByType(MicrosoftFullMessageModal);
  assert.equal(modal.findByType(MicrosoftInertHtmlStage).props.attachments[0].contentId,"logo@message");
  assert.equal(saved.length,0,"inline data is never downloaded through an ordinary file card");
 });
});
