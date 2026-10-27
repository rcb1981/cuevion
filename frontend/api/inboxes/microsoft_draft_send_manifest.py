"""Content evidence for existing-draft Send only. GETs and durable hash-only CAS.

No MIME/HTML equivalence, recipient alias resolution, or delivery claims.
A matching Sent copy is retrospective evidence, never an Outlook transaction.
Only ordinary fileAttachment bytes can currently be verified (10 files,
2 MiB each, 8 MiB total). Inline/item/reference/unknown attachments fail closed.
"""
from api.inboxes import microsoft_compose_body_contract as bodies
import base64
import copy
import hashlib
import json
from datetime import datetime
from urllib.parse import quote
from api.inboxes import microsoft_draft_attachment_snapshot as snapshots
from api.inboxes import microsoft_draft_send_index as index
from api.inboxes import microsoft_outbound_ledger as outbound
from api.inboxes import microsoft_sent_copy_proof as identity
from api.inboxes.microsoft_archive_move import _json_graph
from api.inboxes.microsoft_graph import _get_json, _GRAPH_OPENER, MAX_GRAPH_JSON_BYTES

PREFIX = 'cuevion:microsoft:draft-send-manifest:v1:'
MAX_FILES, MAX_FILE_BYTES, MAX_TOTAL_BYTES = 10, 2 * 1024 * 1024, 8 * 1024 * 1024
MAX_RECORD_BYTES = 16 * 1024
FIELDS = ('toRecipients', 'ccRecipients', 'bccRecipients', 'from', 'sender', 'replyTo', 'subject', 'body')


def fail(code='unverifiable'):
    index.fail(code)


def verify_account(token, authority, *, opener=None):
    # Revalidate the current token's actual /me identity, including after refresh
    # or reconnect. Mail.Send permission by itself is never sender authority.
    me = _get_json('/me?$select=id,displayName,mail,userPrincipalName', token,
                   maximum_bytes=16 * 1024, opener=opener or _GRAPH_OPENER.open)
    if me.get('id') != authority.account_id or me.get('mail') != authority.email:
        fail('sender_unsupported')


def _recipient(value):
    if (type(value) is not dict or set(value) != {'emailAddress'}
            or type(value['emailAddress']) is not dict
            or not {'address'} <= set(value['emailAddress']) <= {'address', 'name'}):
        fail()
    entry = value['emailAddress']
    address = entry['address']
    if (type(address) is not str or not 3 <= len(address) <= 320 or address.count('@') != 1
            or any(ord(c) <= 32 or ord(c) >= 127 for c in address)
            or any(c in address for c in '<>,;:"\\')
            or 'name' in entry and (type(entry['name']) is not str or len(entry['name']) > 512)):
        fail()
    return address


def _content(message, authority, *, sending):
    for key in ('toRecipients', 'ccRecipients', 'bccRecipients', 'replyTo'):
        values = message.get(key)
        if type(values) is not list or len(values) > 500:
            fail()
        for value in values:
            _recipient(value)
    addresses = [_recipient(message.get(key)) for key in ('from', 'sender')]
    if sending and (any(address != authority.email for address in addresses)
                    or any(_recipient(x) != authority.email for x in message['replyTo'])):
        fail('sender_unsupported')
    body = message.get('body')
    if (type(message.get('subject')) is not str or len(message['subject'].encode()) > 8192
            or type(body) is not dict or set(body) != {'contentType', 'content'}
            or body.get('contentType') not in {'text', 'Text', 'html', 'HTML', 'Html'}
            or type(body.get('content')) is not str or len(body['content'].encode()) > 512 * 1024):
        fail()
    # Exact JSON field equality, including body type. Display-name/address
    # changes, ordering, whitespace, casing and HTML are NOT erased.
    return {key: snapshots.digest(message[key]) for key in FIELDS}


def _files(token, path, opener):
    # A single bounded page. A continuation is NEVER treated as a complete list.
    value = _get_json(path + '/attachments?$top=11&$select=' + snapshots._ATTACHMENT_SELECT,
                      token, maximum_bytes=256 * 1024, opener=opener)
    rows = value.get('value')
    if type(rows) is not list or len(rows) > MAX_FILES or '@odata.nextLink' in value:
        fail()
    files, observations, seen, total = [], [], set(), 0
    for row in rows:
        if type(row) is not dict:
            fail()
        aid = snapshots._required_message_id(row.get('id'))
        if aid in seen:
            fail()
        seen.add(aid)
        def metadata(item):
            if (item.get('@odata.type') not in {'#microsoft.graph.fileAttachment', 'microsoft.graph.fileAttachment'}
                    or item.get('isInline') is not False or item.get('contentId') not in {None, ''}
                    or type(item.get('name')) is not str or not 1 <= len(item['name']) <= 256
                    or type(item.get('contentType')) is not str or not 1 <= len(item['contentType']) <= 128
                    or type(item.get('size')) is not int or not 0 <= item['size'] <= MAX_FILE_BYTES):
                fail()
            return {k: item.get(k) for k in ('name', 'contentType', 'size', 'isInline', 'contentId')}
        meta = metadata(row)
        item = _get_json(path + '/attachments/' + quote(aid, safe=''), token,
                         maximum_bytes=4 * ((MAX_FILE_BYTES + 2) // 3) + 8192, opener=opener)
        encoded = item.get('contentBytes')
        if (item.get('id') != aid or metadata(item) != meta or type(encoded) is not str
                or len(encoded) > 4 * ((MAX_FILE_BYTES + 2) // 3)):
            fail()
        try:
            data = base64.b64decode(encoded, validate=True)
        except Exception:
            fail()
        if len(data) > MAX_FILE_BYTES:
            fail()
        total += len(data)
        if total > MAX_TOTAL_BYTES:
            fail()
        # Graph size can include overhead: never substitute size for byte length.
        file = {'metadataHash': snapshots.digest(meta), 'byteLength': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}
        files.append(file)
        observations.append([aid, file])
    # IDs may change as provider resources move. Compare the full multiset of
    # metadata and bytes, retaining duplicates; IDs still bind each individual GET.
    files.sort(key=lambda x: (x['metadataHash'], x['sha256'], x['byteLength']))
    return files, sorted(observations, key=lambda x: x[0])


def _observe(token, authority, draft_id, *, sent, expected=None, opener=None):
    opener = opener or _GRAPH_OPENER.open
    folder = 'sentitems' if sent else 'drafts'
    path = '/me/mailFolders/' + folder + '/messages/' + quote(snapshots._required_message_id(draft_id), safe='')
    folder_id = _json_graph(token, '/me/mailFolders/' + folder + '?$select=id', opener=opener).get('id')
    snapshots._required_message_id(folder_id)
    def message():
        value = _get_json(path + '?$select=' + snapshots.SELECT + ',sentDateTime', token,
                          maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=opener)
        if (value.get('id') != draft_id or value.get('parentFolderId') != folder_id
                or value.get('isDraft') is not (not sent)
                or type(value.get('hasAttachments')) is not bool
                or type(value.get('@odata.etag')) is not str or not snapshots._ETAG.fullmatch(value['@odata.etag'])
                or type(value.get('changeKey')) is not str or not value['changeKey']):
            fail()
        if sent:
            try:
                stamp = datetime.fromisoformat(value['sentDateTime'].replace('Z', '+00:00'))
                if stamp.tzinfo is None:
                    fail()
            except (KeyError, TypeError, AttributeError, ValueError):
                fail()
        elif expected is not None:
            if (value['@odata.etag'] != expected.etag or value['changeKey'] != expected.change_key
                    or snapshots.digest({k: value.get(k) for k in FIELDS}) != expected.protected_content_hash):
                fail('conflict')
        _content(value, authority, sending=not sent)
        return value
    first = message()
    try:
        text = bodies.verified_text(token, path + '?$select=' + snapshots.SELECT + ',sentDateTime', first, opener=opener)
    except snapshots.MicrosoftGraphError as error:
        if not sent or error.code != bodies.CODE:
            raise
        text = None  # Can prove a raw-body mismatch, never a match, without native proof.
    files, inventory = _files(token, path, opener)
    middle = message()
    _second_files, second_inventory = _files(token, path, opener)
    last = message()
    if first != middle or first != last or inventory != second_inventory:
        fail('conflict')
    if first['hasAttachments'] != bool(files):
        fail()
    if expected is not None and [x[0] for x in inventory] != sorted(x.id for x in expected.attachments):
        fail('conflict')
    return {'fields': _content(first, authority, sending=not sent), 'attachments': files,
            'nativeTextHash': snapshots.digest(text) if text is not None else None}


def capture(token, authority, current, *, opener=None):
    return _observe(token, authority, current.snapshot.provider_message_id,
                    sent=False, expected=current.snapshot, opener=opener)


def compare_sent(token, authority, draft_id, approved, *, opener=None):
    opener = opener or _GRAPH_OPENER.open
    if not identity.verify_microsoft_sent_copy(token, draft_id, opener=opener):
        return None
    # Older pending manifests lack native-body proof. Never invent it retrospectively.
    if 'nativeTextHash' not in approved:
        return None
    observed = _observe(token, authority, draft_id, sent=True, opener=opener)
    if observed['fields'] != approved['fields'] or observed['attachments'] != approved['attachments']:
        return 'mismatch'
    if observed['nativeTextHash'] is None:
        return None
    return 'matched' if observed == approved else 'mismatch'


def _context(authority, attempt_id, item):
    # Resolve the bound historical index independently, not from a client scope.
    if index.get_item(authority, attempt_id) != item:
        fail('unavailable')
    return (outbound._storage(), PREFIX + snapshots.digest([authority.owner_email, authority.mailbox_id, attempt_id]),
            {'v': 1, 'scope': snapshots.authority_digest(authority, generation=False),
             'attemptId': attempt_id, 'draftId': item['draftId'], 'binding': item['binding']})


def read(authority, attempt_id, item):
    config, key, binding = _context(authority, attempt_id, item)
    try:
        value, raw, error = outbound.store._read_durable_record_snapshot(config, key)
        if error or (value is None) != (raw is None):
            fail('unavailable')
        if value is None:
            return None, None
        if (type(raw) is not str or len(raw.encode()) > MAX_RECORD_BYTES or json.loads(raw) != value
                or type(value) is not dict or set(value) != set(binding) | {'approved', 'result'}
                or type(value['v']) is not int or any(value[k] != v for k, v in binding.items())
                or value['result'] not in {'pending', 'matched', 'mismatch'}):
            fail('unavailable')
        approved = value['approved']
        if (type(approved) is not dict or set(approved) not in ({'fields', 'attachments'}, {'fields', 'attachments', 'nativeTextHash'})
                or 'nativeTextHash' in approved and (type(approved['nativeTextHash']) is not str or not index.base.HASH.fullmatch(approved['nativeTextHash']))
                or type(approved['fields']) is not dict or set(approved['fields']) != set(FIELDS)
                or any(type(h) is not str or not index.base.HASH.fullmatch(h) for h in approved['fields'].values())
                or type(approved['attachments']) is not list or len(approved['attachments']) > MAX_FILES):
            fail('unavailable')
        total = 0
        for file in approved['attachments']:
            if (type(file) is not dict or set(file) != {'metadataHash', 'byteLength', 'sha256'}
                    or any(type(file[k]) is not str or not index.base.HASH.fullmatch(file[k]) for k in ('metadataHash', 'sha256'))
                    or type(file['byteLength']) is not int or not 0 <= file['byteLength'] <= MAX_FILE_BYTES):
                fail('unavailable')
            total += file['byteLength']
        if total > MAX_TOTAL_BYTES:
            fail('unavailable')
        return copy.deepcopy(value), raw
    except Exception:
        fail('unavailable')


def _write(authority, attempt_id, item, raw, value):
    config, key, _ = _context(authority, attempt_id, item)
    try:
        outbound.store._write_google_durable_record_if_unchanged(config, key, raw, value)
        # A lost ACK is recoverable only through exact, validated durable readback.
        if read(authority, attempt_id, item)[0] != value:
            fail('unavailable')
    except Exception:
        fail('unavailable')


def prepare(authority, attempt_id, approved):
    item = index.get_item(authority, attempt_id)
    if item is None:
        fail('unavailable')
    record = index.state(authority, attempt_id, item)
    if record is None or record['phase'] != 'draft_prepared':
        fail('unavailable')
    previous, raw = read(authority, attempt_id, item)
    _, _, binding = _context(authority, attempt_id, item)
    value = {**binding, 'approved': approved, 'result': 'pending'}
    if previous is not None:
        fail('unavailable')
    _write(authority, attempt_id, item, raw, value)


def settle(authority, attempt_id, item, result):
    if result not in {'matched', 'mismatch'}:
        fail('unavailable')
    for _ in range(3):
        value, raw = read(authority, attempt_id, item)
        record = index.state(authority, attempt_id, item)
        if value is None or record is None or record['phase'] not in {'send_started', 'accepted_by_microsoft', 'sent_copy_confirmed'}:
            fail('unavailable')
        # Mismatch is absorbing, also when simultaneous probes disagree.
        if value['result'] == 'mismatch' or value['result'] == result:
            return
        try:
            _write(authority, attempt_id, item, raw, {**value, 'result': result})
            return
        except Exception:
            continue
    fail('unavailable')
