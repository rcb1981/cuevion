import {test} from 'node:test';
import assert from 'node:assert/strict';
import {prepareMicrosoftComposeRequest as prepare,normalizeMicrosoftRecipientList as recipients} from './microsoftComposeRequest.ts';
const id='microsoft-0123456789abcdefabcd12345678';
const messageId='AAMkAAAB123==';
const source={providerFolder:'INBOX',serverMailboxId:id,providerMessageId:messageId};
const input={mailboxId:id,source,sourceIsExact:true,selectedProviderMessageId:messageId,
  connected:true,busy:false,body:'Please review this information.'};

test('Reply uses exact Graph identity without injecting guessed recipients',()=>{
  const r=prepare({...input,mode:'reply'});
  assert.equal(r.ok,true);assert.deepEqual(r.compose,{mode:'reply',sourceProviderMessageId:messageId,body:input.body});
});
test('Reply All uses Graph recipient authority, no guessed to/cc',()=>{
  const r=prepare({...input,mode:'reply_all'});
  assert.equal(r.ok,true);assert.deepEqual(Object.keys(r.compose).sort(),['body','mode','sourceProviderMessageId']);
});
test('Forward requires explicit To/CC, exact source; no original content injection',()=>{
  const r=prepare({...input,mode:'forward',to:'test@example.com, Artist@Label.COM',cc:'manager@example.com'});
  assert.equal(r.ok,true);assert.deepEqual(r.compose.to,['test@example.com','artist@label.com']);
  assert.deepEqual(r.compose.cc,['manager@example.com']);
  assert.equal(r.compose.sourceProviderMessageId,messageId);
});
test('New compose creates validated To/Cc + Subject',()=>{
  const r=prepare({...input,mode:'new',to:'artist@example.com',cc:'manager@example.com',subject:'My offer'});
  assert.equal(r.ok,true);assert.equal(r.compose.mode,'new');
  assert.deepEqual(r.compose.to,['artist@example.com']);
});
test('Rejects stale selection and foreign mailbox/folder',()=>{
  for(const [change,code] of [
    [{sourceIsExact:false},'unverified_source'],[{source:null},'unverified_source'],
    [{source:{...source,serverMailboxId:'other'}},'wrong_mailbox'],
    [{source:{...source,providerFolder:'DELETED'}},'wrong_folder'],
    [{source:{...source,providerFolder:'ARCHIVE'}},'wrong_folder'],
    [{selectedProviderMessageId:'someone-else'},'invalid_source'],
    [{source:{...source,providerMessageId:'bad id'}},'invalid_source'],
    [{connected:false},'disconnected'],[{busy:true},'busy'],
  ]) assert.deepEqual(prepare({...input,mode:'reply',...change}),{ok:false,code});
});
test('Rejects invalid recipient lists and cross-To/CC duplicate',()=>{
  for(const bad of ['not an email','a@example.com,,b@example.com',
    'someone@example.com, Someone@Example.com','bob@example.com\r\nBcc:attacker@example.com',
    'a@invalid','alice@example.com'.repeat(30)]) assert.equal(recipients(bad),null);
  assert.deepEqual(prepare({...input,mode:'forward',to:'a@example.com',cc:'A@Example.com'}),{ok:false,code:'invalid_recipients'});
  assert.deepEqual(prepare({...input,mode:'forward',to:''}),{ok:false,code:'invalid_recipients'});
});
test('Rejects user-injected Reply-To recipients and arbitrary Subject in a reply',()=>{
  assert.deepEqual(prepare({...input,mode:'reply_all',to:'attacker@example.com'}),{ok:false,code:'invalid_recipients'});
  assert.deepEqual(prepare({...input,mode:'reply',subject:'changed'}),{ok:false,code:'invalid_recipients'});
});
test('Rejects header control injection and oversized/empty messages',()=>{
  assert.deepEqual(prepare({...input,mode:'new',to:'ok@example.com',subject:'Hi\r\nBcc:bad@example.com'}),{ok:false,code:'invalid_subject'});
  assert.deepEqual(prepare({...input,mode:'forward',to:'ok@example.com',body:'\u0000bad'}),{ok:false,code:'invalid_body'});
  assert.deepEqual(prepare({...input,mode:'reply',body:' '}),{ok:false,code:'invalid_body'});
});
