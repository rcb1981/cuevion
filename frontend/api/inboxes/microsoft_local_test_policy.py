"""Fail-closed foundation, NOT a local login, OAuth grant or release switch.

No runtime enrollment, arming, reset, infrastructure creation or Graph calls.
The operator must provision a separate loopback-only durable REST CAS store and
its marker/control records in a later approved step. Only Send consumes a fuse.
Production auth/OAuth and existing release policies remain independent.
"""
from dataclasses import dataclass
import json
import os
import re
import time
from urllib.parse import urlsplit
from api.auth.email_address import is_valid_auth_email, normalize_auth_email
from api.auth.http import get_unique_header
from api.auth.runtime import AuthenticatedMemberContext
from api.inboxes.microsoft_graph import MicrosoftGraphError, _required_message_id
from api.inboxes.microsoft_draft_attachment_snapshot import digest
from api.inboxes import microsoft_outbound_ledger as outbound

APPROVED_MAILBOX = 'carltricks@live.nl'
CONFIG_ENV = 'CUEVION_MICROSOFT_LOCAL_TEST_CONFIG'
STOP_ENV = 'CUEVION_MICROSOFT_LOCAL_TEST_STOP'
MARKER_KEY = 'cuevion:microsoft:local-test-store:v1'
CONTROL_KEY = 'cuevion:microsoft:local-test-control:v1'
FUSE_KEY = 'cuevion:microsoft:local-test-attempt:v1'
MAX_AUTH_SECONDS = 30 * 60
FIELDS = {'v','origin','storeUrl','storeId','ownerUserId','workspaceId','ownerEmail',
          'accountId','mailboxId','mailboxAddress','authorization'}
AUTH_FIELDS = {'draftId','recipients','notBefore','expiresAt'}


def fail(kind='blocked'):
    raise MicrosoftGraphError('microsoft_local_test_' + kind)


def is_context():
    return os.getenv('VERCEL_ENV', '').strip().lower() == 'development' or any(
        name.startswith('CUEVION_MICROSOFT_LOCAL_TEST_') for name in os.environ)


def _exact(left, right):
    return type(left) is dict and digest(left) == digest(right)


def _identifier(value):
    return type(value) is str and 1 <= len(value) <= 256 and all(33 <= ord(c) <= 126 for c in value)


def _loopback(value, *, storage):
    if type(value) is not str or len(value) > 256:
        fail()
    try:
        uri = urlsplit(value)
        hosts = {'127.0.0.1'} if storage else {'127.0.0.1','localhost'}
        scheme = 'http' if storage else 'https'
        if (uri.scheme != scheme or uri.hostname not in hosts or uri.port is None
                or not 1024 <= uri.port <= 65535 or uri.username or uri.password
                or uri.path or uri.query or uri.fragment
                or uri.netloc != uri.hostname + ':' + str(uri.port)):
            fail()
        return uri.netloc
    except (ValueError, TypeError):
        fail()


def _config(headers):
    if (os.getenv('VERCEL_ENV') != 'development' or os.getenv('VERCEL')
            or os.getenv('VERCEL_URL') or os.getenv('VERCEL_BRANCH_URL')
            or any(os.getenv(k, '').strip() for k in
                   ('HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','http_proxy','https_proxy','all_proxy'))):
        fail()
    raw = os.getenv(CONFIG_ENV, '')
    if not raw or len(raw.encode()) > 8192:
        fail()
    def unique(pairs):
        value = {}
        for k,v in pairs:
            if k in value:fail()
            value[k]=v
        return value
    try:
        cfg = json.loads(raw, object_pairs_hook=unique)
        if (type(cfg) is not dict or set(cfg) != FIELDS or type(cfg['v']) is not int or cfg['v'] != 1
                or cfg['mailboxAddress'] != APPROVED_MAILBOX
                or any(not _identifier(cfg[k]) for k in ('ownerUserId','workspaceId','accountId','mailboxId'))
                or type(cfg['ownerEmail']) is not str or not is_valid_auth_email(cfg['ownerEmail'])
                or normalize_auth_email(cfg['ownerEmail']) != cfg['ownerEmail']
                or type(cfg['storeId']) is not str or not re.fullmatch('[a-f0-9]{64}',cfg['storeId'])
                or type(cfg['authorization']) is not dict or set(cfg['authorization']) != AUTH_FIELDS):
            fail()
        host = _loopback(cfg['origin'], storage=False)
        _loopback(cfg['storeUrl'], storage=True)
        if (os.getenv('CUEVION_APP_URL') != cfg['origin']
                or get_unique_header(headers,'host',required=True) != host
                or get_unique_header(headers,'origin',required=True) != cfg['origin']
                or get_unique_header(headers,'x-forwarded-host') not in (None,host)
                or get_unique_header(headers,'x-forwarded-proto') not in (None,'https')):
            fail()
        # Every existing user/token/ledger/lease adapter reads these same vars.
        # Check before resolving any owner record, never after touching a remote store.
        if (os.getenv('KV_REST_API_URL') != cfg['storeUrl']
                or not os.getenv('KV_REST_API_TOKEN', '').strip()):
            fail()
        return cfg
    except MicrosoftGraphError:
        raise
    except Exception:
        fail()


def _store(cfg):
    storage = outbound._storage()
    if storage.get('rest_url') != cfg['storeUrl'] or not storage.get('rest_token'):
        fail('store_unavailable')
    return storage


def _read(cfg, key):
    try:
        value, raw, error = outbound.store._read_durable_record_snapshot(_store(cfg), key)
        if (error or (value is None) != (raw is None)
                or raw is not None and (type(raw) is not str or len(raw.encode()) > 8192
                    or type(value) is not dict or json.loads(raw) != value)):
            fail('store_unavailable')
        return value, raw
    except Exception:
        fail('store_unavailable')


def guard_environment(headers):
    cfg = _config(headers)
    marker,_ = _read(cfg, MARKER_KEY)
    if not _exact(marker, {'v':1,'purpose':'microsoft-local-test-only','storeId':cfg['storeId']}):
        fail('store_unavailable')
    return cfg


def _identity(cfg, authority, member):
    if (type(member) is not AuthenticatedMemberContext or member.auth_source != 'auth0'
            or member.user_type != 'member' or member.membership_role != 'owner'
            or (member.user_id,member.workspace_id,member.email) !=
               (cfg['ownerUserId'],cfg['workspaceId'],cfg['ownerEmail'])
            or (authority.owner_email,authority.mailbox_id,authority.account_id,authority.email) !=
               (cfg['ownerEmail'],cfg['mailboxId'],cfg['accountId'],APPROVED_MAILBOX)):
        fail()


def check_resolved(headers, authority, member):
    _identity(guard_environment(headers), authority, member)


def _authorized_identity(headers, authority):
    from api.inboxes import microsoft_mailbox_access as owned
    cfg = guard_environment(headers)
    # Re-resolve the real authenticated membership; no browser-supplied subject,
    # email, workspace or header acts as authority.
    resolved = owned.resolve_owned_managed_inbox_record(headers, authority.mailbox_id, include_member_authority=True)
    if type(resolved) is not dict or resolved.get('status') != 'ok':
        fail()
    _identity(cfg, authority, resolved.get('memberAuthority'))
    box = resolved.get('inbox')
    if (type(box) is not dict or (box.get('id'),box.get('email'),box.get('oauthAccountId'),
            box.get('oauthOwnerUserId'),box.get('oauthWorkspaceId')) !=
            (cfg['mailboxId'],APPROVED_MAILBOX,cfg['accountId'],cfg['ownerUserId'],cfg['workspaceId'])):
        fail()
    return cfg


def authorize_status(headers, authority):
    if is_context():
        _authorized_identity(headers, authority)
    # Status needs identity/storage, never an armed control, recipient or live TTL.


def _send_window(cfg, draft_id):
    auth = cfg['authorization']
    recipient = auth.get('recipients')
    start,end = auth.get('notBefore'),auth.get('expiresAt')
    if (type(start) is not int or type(end) is not int or not 0 < end-start <= MAX_AUTH_SECONDS
            or not start <= time.time() < end or auth.get('draftId') != draft_id
            or type(recipient) is not list or len(recipient) != 1
            or type(recipient[0]) is not str or not is_valid_auth_email(recipient[0])
            or normalize_auth_email(recipient[0]) != recipient[0]):
        fail()
    _required_message_id(draft_id)
    return end-time.time()


def _armed(cfg):
    if os.getenv(STOP_ENV, '1') != '0':
        fail('stopped')
    control,_ = _read(cfg, CONTROL_KEY)
    expected = {'v':1,'storeId':cfg['storeId'],'policyHash':digest(cfg),'stopped':False}
    if not _exact(control, expected):
        fail('stopped')


@dataclass(frozen=True)
class Permit:
    headers: object
    authority: object
    cfg: dict
    draft_id: str
    attempt_id: str
    binding: str
    deadline: float

    def check_deadline(self):
        if time.monotonic() >= self.deadline:
            fail()
        _send_window(self.cfg, self.draft_id)
        if os.getenv(STOP_ENV, '1') != '0':
            fail('stopped')

    def checkpoint(self):
        cfg = _authorized_identity(self.headers, self.authority)
        if cfg != self.cfg or time.monotonic() >= self.deadline:
            fail()
        _send_window(cfg,self.draft_id)
        _armed(cfg)
        self.check_deadline()  # Storage latency must not extend the grant.

    def content(self, current):
        self.checkpoint()
        if (current.snapshot.provider_message_id != self.draft_id
                or current.edit['to'] != self.cfg['authorization']['recipients']
                or current.edit['cc'] or current.edit['bcc']
                or current.raw_recipients != (tuple(self.cfg['authorization']['recipients']), (), ())
                or current.snapshot.attachments or not current.snapshot.inventory_complete):
            fail()

    def claim_once(self):
        self.checkpoint()
        value,_ = _read(self.cfg,FUSE_KEY)
        if value is not None:
            fail('consumed')
        claim = {'v':1,'storeId':self.cfg['storeId'],'policyHash':digest(self.cfg),
                 'draftHash':digest(self.draft_id),'attemptId':self.attempt_id,'binding':self.binding}
        try:
            stored,error = outbound.store._write_google_durable_record_if_unchanged(_store(self.cfg),FUSE_KEY,None,claim)
            # A lost ACK never grants execution, even when the fuse was persisted.
            if error or not _exact(stored,claim) or not _exact(_read(self.cfg,FUSE_KEY)[0],claim):
                fail('store_unavailable')
        except Exception:
            fail('store_unavailable')


def authorize_send(headers, authority, draft_id, attempt_id, binding):
    cfg = _authorized_identity(headers,authority)
    remaining = _send_window(cfg,draft_id)
    deadline = time.monotonic() + remaining
    _armed(cfg)
    outbound.ATTEMPT_ID.fullmatch(attempt_id) or fail()
    if type(binding) is not str or not re.fullmatch('[a-f0-9]{64}',binding):fail()
    prior,_ = _read(cfg,FUSE_KEY)
    expected = {'v':1,'storeId':cfg['storeId'],'policyHash':digest(cfg),
                'draftHash':digest(draft_id),'attemptId':attempt_id,'binding':binding}
    if prior is not None and not _exact(prior,expected):
        fail('consumed')
    return Permit(headers,authority,cfg,draft_id,attempt_id,binding,deadline)
