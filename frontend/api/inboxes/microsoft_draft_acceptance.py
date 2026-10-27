"""Default-OFF, owner/account-bound draft acceptance on the existing app.

No onboarding, enrollment endpoint, Graph mutation, token refresh or control
writer here. Operator-only configuration/control lives in the existing CAS store.
Neither an email address nor a client capability grants mutation authority.
"""
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from api.auth.http import CANONICAL_APP_ORIGIN
from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes import microsoft_mailbox_access as owned
from api.inboxes import microsoft_compose_draft_ledger as ledger
from api.inboxes import microsoft_token_authority as tokens
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes.microsoft_preview_origin import require_microsoft_request_origin
from api.inboxes.microsoft_draft_attachment_snapshot import digest

APPROVED_ADDRESS='carltricks@live.nl'
CONFIG_KEY='cuevion:microsoft:draft-acceptance-policy:v1'
STOP_ENV='CUEVION_MICROSOFT_DRAFT_ACCEPTANCE_STOP'
CONTROL_PREFIX='cuevion:microsoft:draft-acceptance:v1:'
FIELDS={'v','ownerUserId','workspaceId','ownerEmail','accountId','mailboxId','mailboxAddress'}
CONTROL_FIELDS={'v','policyHash','stopped','notBefore','expiresAt','baselineCount','maxOperations','sourceIds'}


def fail(code='blocked'):
    raise MicrosoftGraphError('microsoft_acceptance_'+code)


def config():
    storage=ledger.base.store._resolve_durable_store_config()
    if type(storage) is not dict or not storage:fail('unavailable')
    stored,raw=ledger.segments._snapshot(storage,CONFIG_KEY)
    if stored is None:return None
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result:fail()
            result[k]=v
        return result
    try:
        if len(raw.encode())>4096:fail()
        value=json.loads(raw,object_pairs_hook=unique)
        if (type(value) is not dict or set(value)!=FIELDS or type(value['v']) is not int or value['v']!=1
                or value['mailboxAddress']!=APPROVED_ADDRESS
                or any(type(value[k]) is not str or not value[k] or len(value[k])>256
                       or any(ord(c)<33 or ord(c)>126 for c in value[k]) for k in FIELDS-{'v'})):
            fail()
        return value
    except MicrosoftGraphError:raise
    except Exception:fail()


def configured():
    # This is only permission to parse the route, never authorization to write.
    try:return os.getenv('VERCEL_ENV')=='production' and config() is not None
    except MicrosoftGraphError:return False


def _identity(headers, authority, cfg):
    if os.getenv('VERCEL_ENV')!='production' or require_microsoft_request_origin(headers)!=CANONICAL_APP_ORIGIN:fail()
    resolved=owned.resolve_owned_managed_inbox_record(headers,authority.mailbox_id,include_member_authority=True)
    member=resolved.get('memberAuthority') if type(resolved) is dict and resolved.get('status')=='ok' else None
    box=resolved.get('inbox') if type(resolved) is dict else None
    if (type(member) is not AuthenticatedMemberContext or member.membership_role!='owner'
            or member.auth_source!='auth0' or member.user_type!='member' or type(box) is not dict
            or (member.user_id,member.workspace_id,member.email)!=(cfg['ownerUserId'],cfg['workspaceId'],cfg['ownerEmail'])
            or (authority.owner_email,authority.mailbox_id,authority.account_id,authority.email)!=
               (cfg['ownerEmail'],cfg['mailboxId'],cfg['accountId'],APPROVED_ADDRESS)
            or (box.get('id'),box.get('email'),box.get('oauthAccountId'),box.get('oauthOwnerUserId'),box.get('oauthWorkspaceId'))!=
               (cfg['mailboxId'],APPROVED_ADDRESS,cfg['accountId'],cfg['ownerUserId'],cfg['workspaceId'])):fail()


def control(cfg,authority):
    storage,_,_=ledger.context(authority)
    value,_=ledger.segments._snapshot(storage,CONTROL_PREFIX+digest(cfg))
    if (type(value) is not dict or set(value)!=CONTROL_FIELDS or type(value['v']) is not int or value['v']!=1
            or value['policyHash']!=digest(cfg) or type(value['stopped']) is not bool
            or any(type(value[k]) is not int for k in ('notBefore','expiresAt','baselineCount','maxOperations'))
            or not 0<value['expiresAt']-value['notBefore']<=1800
            or not 0<=value['baselineCount']<=ledger.MAX_SEQUENCE or not 1<=value['maxOperations']<=12
            or type(value['sourceIds']) is not list or len(value['sourceIds'])>2):fail('unavailable')
    for source in value['sourceIds']:_required_message_id(source)
    if len(set(value['sourceIds']))!=len(value['sourceIds']):fail('unavailable')
    return value


def _count(root):return len(root['operations']) if root['v']==1 else root['count']


@dataclass(frozen=True,repr=False)
class Permit:
    headers: object
    authority: object
    cfg: dict
    initial: dict | None
    writable: bool
    def require_live_window(self):
        """No I/O: final deadline check after slow guards and at Graph dispatch.

        This cannot cancel an already dispatched request or make Graph and the
        durable stop record atomic. A stopped/expired permit never grants a new
        dispatch when observed by this check.
        """
        if (not self.writable or self.initial is None or os.getenv(STOP_ENV, '0') != '0'
                or not self.initial['notBefore'] <= time.time() < self.initial['expiresAt']):
            fail('stopped')

    def checkpoint(self, *, new_operation=False):
        if config()!=self.cfg:fail()
        owned._verify_unchanged_owned_mailbox(self.headers,self.authority.mailbox_id,self.authority)
        _identity(self.headers,self.authority,self.cfg)
        if not self.writable:return
        current=control(self.cfg,self.authority)
        if (os.getenv(STOP_ENV,'0')!='0' or current['stopped'] or current!=self.initial
                or not current['notBefore']<=time.time()<current['expiresAt']):fail('stopped')
        count=_count(ledger.read(self.authority)[0])
        if count<current['baselineCount'] or count>current['baselineCount']+current['maxOperations']-int(new_operation):fail('limit')
        # The count lookup may finish after the authorization window expires.
        self.require_live_window()
    def source(self,identifier,mode):
        if mode not in {'reply','forward'} or not self.initial or identifier not in self.initial['sourceIds']:fail()
    def draft(self,identifier):
        # Only drafts created in this explicitly bounded test window may change.
        self.checkpoint()
        window=self.initial or control(self.cfg,self.authority)
        root,_=ledger.read(self.authority)
        for seq in range(window['baselineCount']+1,min(_count(root),window['baselineCount']+window['maxOperations'])+1):
            key=list(root['operations'])[seq-1] if root['v']==1 else ledger.segments.get(self.authority,root['order'],'order',seq)
            item=ledger.entry(self.authority,key,root)['item']
            if item['action']=='create' and item['draftId']==identifier:return
        fail()


def authorize(headers,mailbox_id,*,write=False):
    cfg=config()
    if cfg is None:fail()
    authority=owned.resolve_owned_microsoft_mailbox(headers,mailbox_id)
    _identity(headers,authority,cfg)
    permit=Permit(headers,authority,cfg,control(cfg,authority) if write else None,write)
    permit.checkpoint(new_operation=write)
    return permit


def for_compose(headers,mailbox_id,*,write=False):
    from api.inboxes import microsoft_compose_draft_policy as policy
    if not policy.blocked():return None
    if not configured():policy.require_release()
    return authorize(headers,mailbox_id,write=write)


def capabilities(headers,mailbox_id):
    # No Graph, refresh, lease acquisition, CAS write or client-provided identity.
    authority=owned.resolve_owned_microsoft_mailbox(headers,mailbox_id)
    record,_=tokens._snapshot(tokens._durable_config(),tokens._key(authority))
    record=tokens._validate_record(record,authority)
    scope=set(record['scope'].split())
    read_write=bool(scope & {'Mail.ReadWrite','https://graph.microsoft.com/Mail.ReadWrite'})
    expired=tokens._parse_utc(record['expires_at'])<=datetime.now(timezone.utc)
    resolved=owned.resolve_owned_managed_inbox_record(headers,mailbox_id,include_member_authority=True)
    member=resolved.get('memberAuthority') if type(resolved) is dict and resolved.get('status')=='ok' else None
    if type(member) is not AuthenticatedMemberContext or member.membership_role!='owner':fail()
    eligible=False;can_write=False;reason='not_configured'
    try:cfg=config()
    except MicrosoftGraphError:cfg=None;reason='blocked'
    if cfg is not None:
        try:
            _identity(headers,authority,cfg);eligible=True
            authorize(headers,mailbox_id,write=True)
            can_write=read_write;reason='ready' if read_write else 'consent_required'
        except MicrosoftGraphError as error:reason='blocked'
    owned._verify_unchanged_owned_mailbox(headers,mailbox_id,authority)
    return {'mailboxId':mailbox_id,'canInspect':eligible,'canWrite':can_write,'mailReadWrite':read_write,
            'tokenExpired':expired,'reason':reason,'send':False,'attachments':False,'replyAll':False,
            'identity':{'ownerUserId':member.user_id,'workspaceId':member.workspace_id,'ownerEmail':authority.owner_email,
                        'accountId':authority.account_id,'mailboxId':authority.mailbox_id,'mailboxAddress':authority.email}}
