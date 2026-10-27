"""Native Graph response drafts. Never sends; opaque quoted body stays immutable.

Graph builds the reply/forward context. A second conditional PATCH adds only the
user's editable comment and headers. Provenance stores hashes/lengths, not content.
No HTML/text equivalence is inferred. Provider normalization fails closed.
"""
import html
import json
import re
import time
from html.parser import HTMLParser
from urllib.parse import quote
from urllib.request import Request
from api import user_config_store
from api.inboxes import microsoft_compose_draft_policy as policy
from api.inboxes import microsoft_draft_acceptance as acceptance
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_compose_draft_graph as drafts
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_draft_attachment_executor as executor
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_draft_attachment_requests as attachment_requests
from api.inboxes import microsoft_draft_send_manifest as manifest
from api.inboxes.microsoft_graph import (_get_json, _GRAPH_OPENER, _GRAPH_PREFER, GRAPH_ROOT,
    GRAPH_TIMEOUT_SECONDS, MAX_GRAPH_JSON_BYTES, MicrosoftGraphError, _required_message_id)

_RESPONSE_DRAFTS_RELEASED = False
MODES = {'reply':'createReply','reply_all':'createReplyAll','forward':'createForward'}
PREFIX = 'cuevion:microsoft:response-draft:v1:'
SOURCE_SELECT = 'id,isDraft,conversationId,subject,from,replyTo,toRecipients,ccRecipients,body,hasAttachments,changeKey'


def fail(code='unsupported'):
    raise MicrosoftGraphError('microsoft_response_' + code)


def require_release(permit=None):
    if permit is not None:
        if type(permit) is not acceptance.Permit:fail('disabled')
        permit.checkpoint();return
    policy.require_release()
    if not _RESPONSE_DRAFTS_RELEASED:
        fail('disabled')


def _path(identifier, draft=False):
    return ('/me/mailFolders/drafts/messages/' if draft else '/me/messages/') + quote(_required_message_id(identifier), safe='')


def _get(token, path, opener):
    return _get_json(path, token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)


def _body(value):
    body = value.get('body')
    if (type(body) is not dict or set(body) != {'contentType','content'} or
            body.get('contentType') not in {'html','text','HTML','Text'} or type(body.get('content')) is not str
            or len(body['content'].encode()) > 256 * 1024 or '\x00' in body['content']):
        fail()
    return body['contentType'].lower(), body['content']


def _addresses(rows):
    if type(rows) is not list:fail()
    try:
        values = [x['emailAddress']['address'].strip().lower() for x in rows]
        # Graph can repeat a recipient across fields; normalize only for planning.
        return drafts.plain._addresses(list(dict.fromkeys(values)))
    except Exception:fail()


class _Preview(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True);self.parts=[];self.hidden=0
    def handle_starttag(self,tag,attrs):
        if tag in {'script','style','head','template'}:self.hidden+=1
        elif tag in {'p','div','br','tr','blockquote'} and not self.hidden:self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in {'script','style','head','template'}:self.hidden=max(0,self.hidden-1)
    def handle_data(self,data):
        if not self.hidden:self.parts.append(data)


def _visible(kind, content):
    if kind == 'text':return content
    p=_Preview();p.feed(content);p.close();return ''.join(p.parts)


def preview(kind, content):
    # Display only; not proof of HTML equivalence.
    return _visible(kind,content)[:6000]


def _require_native_quote(original, draft):
    # Detect missing/truncated original text, not HTML equivalence. Native Graph
    # owns quote construction; subsequent edits preserve its exact representation.
    original_text=' '.join(_visible(*_body(original)).split())
    quoted_text=' '.join(_visible(*_body(draft)).split())
    if original_text and original_text not in quoted_text:fail('uncertain')


def source(token, authority, identifier, mode, opener):
    if mode not in MODES:fail('invalid')
    path=_path(identifier)
    value=_get(token,path+'?$select='+SOURCE_SELECT,opener)
    kind,content=_body(value)
    if (value.get('id') != identifier or value.get('isDraft') is not False or
            not drafts.plain._ETAG.fullmatch(value.get('@odata.etag','')) or
            type(value.get('subject')) is not str):fail()
    _required_message_id(value.get('conversationId'))
    targets=_addresses(value.get('replyTo')) or _addresses([value.get('from')])
    original_to=_addresses(value.get('toRecipients'));original_cc=_addresses(value.get('ccRecipients'))
    own=authority.email.lower()
    to=[x for x in (original_to if targets == [own] else targets) if x != own]
    cc=[]
    if mode == 'reply_all':
        cc=list(dict.fromkeys(x for x in original_to+original_cc if x != own and x not in to))
    if mode == 'forward':to=[];cc=[]
    subject=value['subject']
    prefix='Fw: ' if mode == 'forward' else 'Re: '
    if not re.match(r'^(?:fw|fwd):' if mode == 'forward' else r'^re:',subject,re.I):subject=prefix+subject
    edit=policy.validate_edit({'subject':subject,'to':to,'cc':cc,'bcc':[],'body':''})
    try:
        files = manifest._files(token,path,opener)[0] if mode == 'forward' else None
    except MicrosoftGraphError:
        fail('unsupported')
    if _get(token,path+'?$select='+SOURCE_SELECT,opener) != value:fail('conflict')
    return value,files,{'sourceHash':snapshots.digest([value,files]),'edit':edit,
                       'response':{'mode':mode,'quotePreview':preview(kind,content)}}


def prepare_owned(headers, mailbox_id, source_id, mode, *, opener=None):
    permit=acceptance.for_compose(headers,mailbox_id)
    require_release(permit);opener=opener or _GRAPH_OPENER.open
    token,authority=owned._owned_token(headers,mailbox_id)
    manifest.verify_account(token,authority,opener=opener)
    owned._assert_owned_draft_mutable(headers,mailbox_id,authority,source_id)
    if permit:
        window=acceptance.control(permit.cfg,authority)
        if mode not in {'reply','forward'} or source_id not in window['sourceIds']:acceptance.fail()
    result=source(token,authority,source_id,mode,opener)[2]
    owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
    return result


def _context(authority, identifier):
    config,_,scope=ledger.context(authority)
    return config,PREFIX+snapshots.digest([scope,_required_message_id(identifier)]),scope


def provenance(authority, identifier):
    config,key,scope=_context(authority,identifier)
    try:
        value,raw,error=ledger.base.store._read_durable_record_snapshot(config,key)
        if error or (value is None)!=(raw is None):fail('store_unavailable')
        if value is None:return None
        if (type(raw) is not str or len(raw)>4096 or json.loads(raw)!=value or type(value) is not dict or
                set(value)!={'v','scope','mode','bodyType','baseHash','prefixLength','suffixLength','conversationId'} or
                type(value['v']) is not int or value['v']!=1 or value['scope']!=scope or value['mode'] not in MODES or
                value['bodyType'] not in {'html','text'} or not re.fullmatch('[a-f0-9]{64}',value['baseHash']) or
                any(type(value[k]) is not int or not 0<=value[k]<=256*1024 for k in ('prefixLength','suffixLength'))):fail('store_unavailable')
        _required_message_id(value['conversationId'])
        return value
    except MicrosoftGraphError:raise
    except Exception:fail('store_unavailable')


def _body_cut(content):
    class OpeningBody(HTMLParser):
        cut = None
        def handle_starttag(self, tag, attrs):
            if tag == 'body' and self.cut is None:
                line, column = self.getpos()
                offset = sum(len(part)+1 for part in content.split('\n')[:line-1]) + column
                self.cut = offset + len(self.get_starttag_text())
    parser=OpeningBody();parser.feed(content);parser.close()
    return parser.cut or 0


def _save_provenance(authority, identifier, base, mode):
    kind,content=_body(base)
    cut=_body_cut(content) if kind=='html' else 0
    config,key,scope=_context(authority,identifier)
    value={'v':1,'scope':scope,'mode':mode,'bodyType':kind,'baseHash':snapshots.digest(content),
           'prefixLength':cut,'suffixLength':len(content)-cut,'conversationId':base['conversationId']}
    stored,error=ledger.base.store._write_google_durable_record_if_unchanged(config,key,None,value)
    if error or stored!=value or provenance(authority,identifier)!=value:fail('store_unavailable')
    return value


def _fragment(comment, kind, rich=False):
    if rich:
        if kind != 'html':fail('unsupported')
        from api.inboxes.microsoft_compose_html import canonical
        return '<div><div>'+canonical(comment)+'</div></div>'
    return '<div><pre>'+html.escape(comment,quote=False)+'</pre></div>' if kind=='html' else comment+'\n\n'


def _split(data, meta):
    kind,content=_body(data)
    left,right=meta['prefixLength'],meta['suffixLength']
    if kind!=meta['bodyType'] or len(content)<left+right or data.get('conversationId')!=meta['conversationId']:fail('conflict')
    prefix,suffix=content[:left],content[len(content)-right:] if right else ''
    middle=content[left:len(content)-right if right else len(content)]
    if snapshots.digest(prefix+suffix)!=meta['baseHash']:fail('conflict')
    if kind=='html':
        if middle.startswith('<div><div>') and middle.endswith('</div></div>'):
            from api.inboxes.microsoft_compose_html import canonical
            comment=canonical(middle[10:-12])
            if _fragment(comment,kind,True)!=middle:fail('conflict')
            return comment,prefix,suffix,True
        if not middle.startswith('<div><pre>') or not middle.endswith('</pre></div>'):fail('conflict')
        comment=html.unescape(middle[10:-12])
    else:
        if not middle.endswith('\n\n'):fail('conflict')
        comment=middle[:-2]
    if _fragment(comment,kind)!=middle:fail('conflict')
    return comment,prefix,suffix,False


def observe(token, authority, identifier, opener):
    snapshot=snapshots.read_draft_attachment_snapshot(token,authority,identifier,opener=opener)
    data=_get(token,_path(identifier,True)+'?$select='+snapshots.SELECT+',conversationId',opener)
    protected={k:data.get(k) for k in manifest.FIELDS}
    if (data.get('id')!=identifier or data.get('isDraft') is not True or data.get('@odata.etag')!=snapshot.etag
            or data.get('parentFolderId')!=snapshot.drafts_folder_id or data.get('changeKey')!=snapshot.change_key
            or snapshots.digest(protected)!=snapshot.protected_content_hash):fail('conflict')
    _required_message_id(data.get('conversationId'))
    return snapshot,data


def load_owned(headers, mailbox_id, identifier, *, opener=None):
    permit=acceptance.for_compose(headers,mailbox_id)
    require_release(permit);opener=opener or _GRAPH_OPENER.open
    token,authority=owned._owned_token(headers,mailbox_id)
    manifest.verify_account(token,authority,opener=opener)
    if permit:permit.draft(identifier)
    meta=provenance(authority,identifier)
    if meta is None:fail()
    snap,data=observe(token,authority,identifier,opener)
    comment,prefix,suffix,rich=_split(data,meta)
    edit=policy.validate_edit({'subject':data['subject'],'body':comment,
        **{k:_addresses(data.get(k+'Recipients')) for k in ('to','cc','bcc')}, **({'bodyType':'html'} if rich else {})})
    owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
    return {'providerMessageId':identifier,'etag':snap.etag,'snapshotHash':snap.fingerprint,'edit':edit,
            'response':{'mode':meta['mode'],'quotePreview':preview(meta['bodyType'],prefix+suffix)}}


def _write(token, path, payload, opener, *, before_write, etag=None, acceptance_permit=None):
    req=Request(GRAPH_ROOT+path,method='PATCH' if etag else 'POST',data=json.dumps(payload).encode(),
        headers={'Authorization':'Bearer '+token,'Accept':'application/json','Content-Type':'application/json',
                 'Prefer':_GRAPH_PREFER,**({'If-Match':etag} if etag else {})})
    # Build the request first; all guards run immediately before transport.
    before_write()
    if acceptance_permit is not None:acceptance_permit.require_live_window()
    # Fixed action/ID paths only, no redirects, transport retries or send endpoints.
    with opener(req,timeout=GRAPH_TIMEOUT_SECONDS) as response:
        if response.status!=(200 if etag else 201):fail('uncertain')
        raw=response.read(MAX_GRAPH_JSON_BYTES+1)
        if len(raw)>MAX_GRAPH_JSON_BYTES:fail('uncertain')
        value=json.loads(raw)
    if type(value) is not dict or value.get('isDraft') is not True:fail('uncertain')
    _required_message_id(value.get('id'))
    return value


def save_owned(headers, mailbox_id, request_id, edit, *, source_id=None, mode=None, source_hash=None,
               message_id=None, etag=None, opener=None):
    permit=acceptance.for_compose(headers,mailbox_id,write=True)
    require_release(permit);edit=policy.validate_edit(edit);ledger.base._id(request_id)
    opener=opener or _GRAPH_OPENER.open
    if message_id is None:
        _required_message_id(source_id)
        if mode not in MODES or type(source_hash) is not str or not re.fullmatch('[a-f0-9]{64}',source_hash):fail('invalid')
    else:
        _required_message_id(message_id)
        if type(etag) is not str or not drafts.plain._ETAG.fullmatch(etag):fail('invalid')
    authority=owned.resolve_owned_microsoft_mailbox(headers,mailbox_id)
    action='create' if message_id is None else 'update'
    binding=snapshots.digest([snapshots.authority_digest(authority,generation=False),'response',action,source_id,mode,source_hash,message_id,etag,edit])
    replay=ledger.lookup(authority,request_id,binding)
    if replay is not None:
        owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority);return replay
    start=time.monotonic();lease=owned.acquire_mailbox_mutation_lease(authority.owner_email,mailbox_id)
    if type(lease) is not dict or lease.get('status')!='acquired' or not lease.get('token'):fail('busy')
    deadline=start+user_config_store.MAILBOX_MUTATION_LEASE_TTL_MILLISECONDS/1000
    reservation=None;started=False
    def guard(identifier):
        owned._assert_owned_draft_mutable(headers,mailbox_id,authority,identifier)
        owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
        if permit:permit.checkpoint()
        executor._assert_lease(authority,lease['token'],deadline)
        if permit:permit.require_live_window()
    try:
        token,checked=owned._owned_token(headers,mailbox_id,require_read_write=True)
        if checked!=authority:fail('conflict')
        manifest.verify_account(token,authority,opener=opener)
        ledger.assert_unblocked(authority,message_id)
        guard(message_id or source_id)
        if message_id is None:
            if permit:permit.source(source_id,mode)
            original,files,prepared=source(token,authority,source_id,mode,opener)
            if permit and manifest._files(token,_path(source_id),opener)[0]:acceptance.fail()
            if prepared['sourceHash']!=source_hash:fail('conflict')
        else:
            if permit:permit.draft(message_id)
            meta=provenance(authority,message_id)
            if meta is None:fail()
            if any(x['blocked'] for x in attachment_requests.inspect_draft(authority,message_id)):fail('blocked')
            from api.inboxes.microsoft_draft_send_service import assert_draft_settled
            assert_draft_settled(authority,message_id)
            before,data=observe(token,authority,message_id,opener)
            if permit and (before.attachments or meta['mode'] not in {'reply','forward'}):acceptance.fail()
            if before.etag!=etag:fail('conflict')
            _,prefix,suffix,_=_split(data,meta)
            if edit.get('bodyType')=='html' and meta['bodyType']!='html':fail('unsupported')
        if permit:permit.checkpoint(new_operation=True)
        reservation=ledger.reserve(authority,request_id,action,message_id,binding)
        if not reservation['new']:return {k:v for k,v in reservation.items() if k!='new'}
        guard(message_id or source_id)
        started=True  # Lost start-CAS ACK is never evidence of non-execution.
        ledger.advance(authority,request_id,reservation['claim'],'started')
        guard(message_id or source_id)
        if message_id is None:
            created=_write(token,_path(source_id)+'/'+MODES[mode],{},opener,
                before_write=lambda:guard(source_id),acceptance_permit=permit)
            message_id=created['id']
            if message_id==source_id:fail('uncertain')
            ledger.advance(authority,request_id,reservation['claim'],'started',draft_id=message_id)
            before,data=observe(token,authority,message_id,opener)
            if mode!='forward' and data['conversationId']!=original['conversationId']:fail('uncertain')
            _require_native_quote(original,data)
            if mode=='forward' and manifest._files(token,_path(message_id,True),opener)[0]!=files:fail('uncertain')
            if source(token,authority,source_id,mode,opener)[2]['sourceHash']!=source_hash:fail('conflict')
            meta=_save_provenance(authority,message_id,data,mode)
            kind,base=_body(data);prefix=base[:meta['prefixLength']];suffix=base[meta['prefixLength']:]
        body={'contentType':meta['bodyType'],'content':prefix+_fragment(edit['body'],meta['bodyType'],edit.get('bodyType')=='html')+suffix}
        _body({'body':body})  # Never write content beyond the readback contract.
        payload={'subject':edit['subject'],'body':body,
            **{k+'Recipients':[{'emailAddress':{'address':address}} for address in edit[k]] for k in ('to','cc','bcc')}}
        verified_files=manifest._files(token,_path(message_id,True),opener)[0] if meta['mode']=='forward' else None
        if action=='create' and mode=='forward' and verified_files!=files:fail('conflict')
        guard(message_id)
        changed=_write(token,_path(message_id,True),payload,opener,etag=before.etag,
            before_write=lambda:guard(message_id),acceptance_permit=permit)
        if changed['id']!=message_id:fail('uncertain')
        after,actual=observe(token,authority,message_id,opener)
        if (any(actual.get(k)!=v for k,v in payload.items()) or after.etag==before.etag or
                after.inventory_hash!=before.inventory_hash or actual['conversationId']!=meta['conversationId']):fail('uncertain')
        _split(actual,meta)
        if meta['mode']=='forward' and manifest._files(token,_path(message_id,True),opener)[0]!=verified_files:fail('uncertain')
        guard(message_id)
        result=ledger.advance(authority,request_id,reservation['claim'],'confirmed_performed')
        owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
        return result
    except Exception:
        if reservation is None or not reservation.get('new'):raise
        if not started:ledger.advance(authority,request_id,reservation['claim'],'confirmed_not_performed')
        owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
        return ledger.operation(authority, request_id)
    finally:
        try:released=owned.release_mailbox_mutation_lease(authority.owner_email,mailbox_id,lease['token'])
        except Exception:fail('uncertain')
        if type(released) is not dict or released.get('status')!='released':fail('uncertain')
