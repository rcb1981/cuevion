import assert from "node:assert/strict";
import {
  createMicrosoftSendAttemptId,
  sendMicrosoftMessage,
  type MicrosoftSendRequest,
} from "./inboxConnectionApi";

const BOX="microsoft-0123456789abcdefabcd12345678";
const ID="a".repeat(43);
const REQUEST:MicrosoftSendRequest={
  mailboxId:BOX, attemptId:ID,
  compose:{mode:"new",to:["artist@example.com"],subject:"Demo",body:"Please listen"},
};

async function main() {
  const original=globalThis.fetch;
  let calls:Array<{url:string;init?:RequestInit}>=[];
  const fake=(payload:unknown,status=202) => {
    calls=[];
    globalThis.fetch=(async (url:string,init?:RequestInit)=>{
      calls.push({url,init});
      return {ok:status>=200&&status<300,status,json:async()=>payload} as Response;
    }) as typeof fetch;
  };
  const ok={ok:true,attemptId:ID,status:"accepted_by_microsoft",
            deliveryConfirmed:false,sentCopyConfirmed:false,replayed:false};
  try {
    const attempt=createMicrosoftSendAttemptId();
    assert.match(attempt,/^[A-Za-z0-9_-]{43}$/);
    assert.notEqual(attempt,createMicrosoftSendAttemptId());

    fake(ok);
    const result=await sendMicrosoftMessage(REQUEST);
    assert.deepEqual(result,ok);
    assert.equal(calls.length,1);
    assert.equal(calls[0].url,"/api/inboxes/send-microsoft");
    assert.equal(calls[0].init?.method,"POST");
    assert.equal(calls[0].init?.credentials,"include");
    assert.deepEqual(JSON.parse(String(calls[0].init?.body)),REQUEST);

    fake({...ok,replayed:true});
    const replay=await sendMicrosoftMessage(REQUEST);
    assert.equal(replay.ok,true);
    if (replay.ok) assert.equal(replay.replayed,true);

    for (const mode of (["reply","reply_all"] as const)) {
      fake(ok);
      const reply=await sendMicrosoftMessage({
        ...REQUEST,compose:{mode,sourceProviderMessageId:"original-id",body:"Thanks!"},
      });
      assert.equal(reply.ok,true);
      assert.deepEqual(JSON.parse(String(calls[0].init?.body)).compose,{
        mode,sourceProviderMessageId:"original-id",body:"Thanks!",
      });
    }

    for (const invalid of ([
      {...REQUEST,compose:{mode:"new",to:[],subject:"Demo",body:"message"}},
      {...REQUEST,compose:{mode:"new",to:["invalid"],subject:"Demo",body:"message"}},
      {...REQUEST,compose:{mode:"new",to:["a@example.com"],subject:"ok\r\nBcc:evil",body:"message"}},
      {...REQUEST,compose:{mode:"reply",sourceProviderMessageId:"../other",body:"hi"}},
      {...REQUEST,compose:{mode:"reply",sourceProviderMessageId:"original-id",body:" "}},
      {...REQUEST,attemptId:"bad"},
      {...REQUEST,mailboxId:"../mailbox"},
    ] as MicrosoftSendRequest[])) {
      fake(ok);
      const outcome=await sendMicrosoftMessage(invalid);
      assert.equal(outcome.ok,false);
      assert.equal(calls.length,0);
    }

    for (const wrong of ([
      {...ok,deliveryConfirmed:true},
      {...ok,sentCopyConfirmed:true},
      {...ok,attemptId:"wrong"},
      {...ok,status:"delivered"},
      {...ok,ok:false},
    ])) {
      fake(wrong);
      const reply=await sendMicrosoftMessage(REQUEST);
      assert.equal(reply.ok,false);
      assert.equal(reply.error?.code,"microsoft_send_unconfirmed");
    }

    fake({ok:false,error:{
      code:"microsoft_send_attempt_already_started",
      message:"PRIVATE token secret!",
    }},409);
    const blocked=await sendMicrosoftMessage(REQUEST);
    assert.equal(blocked.ok,false);
    assert.equal(blocked.error?.code,"microsoft_send_attempt_already_started");
    assert.doesNotMatch(JSON.stringify(blocked),/PRIVATE/);

    fake({ok:false,error:{
      code:"microsoft_send_permission_required",
      message:"PRIVATE token secret!",
    }},403);
    const permission=await sendMicrosoftMessage(REQUEST);
    assert.equal(permission.error?.code,"microsoft_send_permission_required");
    assert.doesNotMatch(JSON.stringify(permission),/PRIVATE/);

    calls=[];
    globalThis.fetch=(async()=>{calls.push({url:"called"});throw Error("private network error");}) as typeof fetch;
    const lost=await sendMicrosoftMessage(REQUEST);
    assert.equal(lost.ok,false);
    assert.equal(lost.error?.code,"microsoft_send_unconfirmed");
    assert.equal(calls.length,1,"unknown send must not trigger a second network request");
    assert.doesNotMatch(JSON.stringify(lost),/private network/);
    console.log("✓ Microsoft compose/reply one-shot send client tests passed.");
  } finally {
    globalThis.fetch=original;
  }
}
void main().catch(e=>{console.error(e);process.exitCode=1;});
