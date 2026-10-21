import assert from "node:assert/strict";
import { archiveMicrosoftInboxMessage } from "./inboxConnectionApi";

const MAILBOX = "microsoft-0123456789abcdefabcd12345678";
const ID = "graph-provider-id==";
const original = globalThis.fetch;
let calls: Array<{url:string; init?:RequestInit}> = [];
function respond(payload: unknown, status=200) {
  calls = [];
  globalThis.fetch = (async (input:RequestInfo|URL, init?:RequestInit) => {
    calls.push({url:String(input),init});
    return new Response(JSON.stringify(payload),{status,headers:{"Content-Type":"application/json"}});
  }) as typeof globalThis.fetch;
}
async function main() {
  try {
    respond({ok:true,providerMessageId:ID,archived:true});
    assert.deepEqual(await archiveMicrosoftInboxMessage({mailboxId:MAILBOX,providerMessageId:ID}),
      {ok:true,providerMessageId:ID,archived:true});
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,"/api/inboxes/archive-microsoft");
    assert.equal(calls[0].init?.method,"POST");
    assert.equal(calls[0].init?.credentials,"include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)),{mailboxId:MAILBOX,providerMessageId:ID});

    respond({ok:true,providerMessageId:"wrong-id",archived:true});
    assert.equal((await archiveMicrosoftInboxMessage({mailboxId:MAILBOX,providerMessageId:ID})).ok,false);
    assert.equal(calls.length,1);

    respond({ok:false,error:{code:"microsoft_pilot_read_only",message:"Not enabled"}},403);
    assert.equal((await archiveMicrosoftInboxMessage({mailboxId:MAILBOX,providerMessageId:ID})).ok,false);
    assert.equal(calls.length,1);

    calls=[];
    assert.equal((await archiveMicrosoftInboxMessage({mailboxId:MAILBOX,providerMessageId:"not a valid id"})).ok,false);
    assert.equal(calls.length,0);

    calls=[];
    globalThis.fetch = (async () => { calls.push({url:"fetch"}); throw new Error("network"); }) as typeof globalThis.fetch;
    assert.equal((await archiveMicrosoftInboxMessage({mailboxId:MAILBOX,providerMessageId:ID})).ok,false);
    assert.equal(calls.length,1);
    console.log("Microsoft Archive client: exact request, confirm-only, no retry, validation and failures PASS");
  } finally {
    globalThis.fetch = original;
  }
}
void main().catch(error => {globalThis.fetch=original; throw error;});
