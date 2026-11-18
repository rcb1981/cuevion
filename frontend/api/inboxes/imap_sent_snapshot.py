"""Bounded UID traversal and MIME display data; never mailbox ingestion."""
import base64
import hashlib
import hmac
import json
import re
import time
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime

from .imap_sent import SentError, quote_folder
from .imap_uid_validity import read_selected_mailbox_uid_validity
from .mailbox_secret_store import _resolve_encryption_key
from imap_connect_preview import (
    decode_mime_words, get_html_body, get_message_body, get_message_attachments,
    extract_message_thread_metadata, resolve_custom_imap_thread_ids,
)

MAX_UID = 4294967295
WINDOW = 4096
MAX_BODY = 2 * 1024 * 1024
MAX_RAW_PAGE = 8 * 1024 * 1024
MAX_RESPONSE = 10 * 1024 * 1024
MAX_TOKEN = 32768


def uint(value, allow_zero=False):
    return type(value) is int and (0 if allow_zero else 1) <= value <= MAX_UID


def connection_scope(mailbox, generation):
    # Domain-separated key from the already-required credential encryption key.
    # Never expose a password-derived verifier or reuse an encryption key directly.
    secret, error = _resolve_encryption_key()
    if error or secret is None:
        raise SentError('imap_sent_unavailable', 'Sent pagination could not be initialized safely.', 503)
    material = json.dumps(["imap-sent-v1", mailbox['ownerEmail'], mailbox['mailboxId'],
                           mailbox['email'], generation, mailbox['imap']], sort_keys=True).encode()
    key = hmac.digest(secret, material, 'sha256')
    return hashlib.sha256(key).hexdigest(), key


def encode_cursor(value, key):
    raw = json.dumps(value, separators=(',', ':'), sort_keys=True).encode()
    token = base64.urlsafe_b64encode(raw + hmac.digest(key, raw, 'sha256')).decode().rstrip('=')
    if len(token) > MAX_TOKEN:
        raise SentError('sent_folder_unavailable', 'This folder name is too long for safe pagination.', 409)
    return token


def decode_cursor(token, key, scope):
    try:
        if type(token) is not str or not 1 <= len(token) <= MAX_TOKEN or not re.fullmatch(r'[A-Za-z0-9_-]+', token):
            raise ValueError()
        raw = base64.b64decode(token + '=' * (-len(token) % 4), altchars=b'-_', validate=True)
        body, signature = raw[:-32], raw[-32:]
        if not hmac.compare_digest(signature, hmac.digest(key, body, 'sha256')):
            raise ValueError()
        value = json.loads(body)
        if (type(value) is not dict or set(value) != {'scope', 'folder', 'uv', 'ceiling', 'before', 'expires'}
                or value['scope'] != scope or not uint(value['ceiling'])
                or not uint(value['before']) or value['before'] >= value['ceiling']
                or type(value['expires']) is not int or value['expires'] < time.time()
                or value['expires'] > time.time() + 901):
            raise ValueError()
        quote_folder(value['folder'])
        if type(value['uv']) is not str or not value['uv'].isdigit() or not uint(int(value['uv'])):
            raise ValueError()
        return value
    except (ValueError, TypeError, KeyError, UnicodeError):
        raise SentError('invalid_page_token', 'Sent pagination expired. Refresh Sent to continue.', 409) from None


class ReadBudget:
    """Bound actual literals/lines before imaplib allocates an advertised literal."""
    def __init__(self, mailbox, deadline):
        self.mailbox, self.deadline, self.bytes = mailbox, deadline, 0
        # BufferedReader.read1 performs at most one underlying read. Unlike
        # read(n)/readline(), it cannot keep resetting the socket timeout while
        # a server trickles an entire literal or line. Preserve buffered bytes
        # from login, and keep this wrapper local to this dedicated connection.
        self.pending = bytearray()
        existing = getattr(mailbox, '_readbuf', None)
        if type(existing) is list:
            # Newer imaplib keeps its own socket buffer instead of using file.
            if any(type(part) is not bytes for part in existing) or sum(map(len, existing)) > MAX_RAW_PAGE:
                raise SentError('sent_response_too_large', 'The IMAP response is too large.')
            self.pending.extend(b''.join(existing))
            self.bytes = len(self.pending)
            mailbox._readbuf = []
            self.receive = mailbox.sock.recv
        else:
            self.receive = getattr(getattr(mailbox, 'file', None), 'read1', None)
        if not callable(self.receive):
            raise SentError('sent_transport_unavailable', 'This IMAP runtime cannot safely bound Sent reads.')
        mailbox.read, mailbox.readline = self.read, self.readline

    def chunk(self, maximum):
        self.check()
        data = self.receive(min(maximum, 65536))
        self.bytes += len(data)
        if self.bytes > MAX_RAW_PAGE + 1024 * 1024:
            raise SentError('sent_response_too_large', 'The IMAP response is too large.')
        self.check()
        return data

    def read(self, size):
        self.check()
        if type(size) is not int or not 0 <= size <= MAX_BODY + 1:
            raise SentError('sent_response_too_large', 'The IMAP response is too large.')
        result = self.pending[:size]
        del self.pending[:size]
        while len(result) < size:
            data = self.chunk(size - len(result))
            if not data:
                break
            result.extend(data)
        return bytes(result)

    def readline(self):
        self.check()
        while True:
            end = self.pending.find(b'\n')
            if end >= 0:
                if end >= 65536:
                    raise SentError('sent_response_too_large', 'The IMAP response line is too large.')
                line = bytes(self.pending[:end + 1])
                del self.pending[:end + 1]
                return line
            if len(self.pending) >= 65536:
                raise SentError('sent_response_too_large', 'The IMAP response line is too large.')
            data = self.chunk(65536 - len(self.pending))
            if not data:
                line = bytes(self.pending)
                self.pending.clear()
                return line
            self.pending.extend(data)

    def check(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise SentError('sent_timeout', 'Sent took too long to load. Try again.')
        self.mailbox.sock.settimeout(min(5, remaining))


def examine(mailbox, folder):
    status, _ = mailbox.select(quote_folder(folder), readonly=True)
    if status != 'OK':
        raise SentError('sent_folder_unavailable', 'The selected Sent folder could not be opened.', 409)
    uv = read_selected_mailbox_uid_validity(mailbox)
    if uv is None or not uint(int(uv)):
        raise SentError('uid_validity_unavailable', 'Sent identity could not be verified.', 409)
    return uv


def search_window(mailbox, low, high):
    status, data = mailbox.uid('SEARCH', None, 'UID', f'{low}:{high}')
    if status != 'OK' or type(data) is not list or len(data) != 1 or type(data[0]) is not bytes or len(data[0]) > WINDOW * 11:
        raise SentError('sent_fetch_failed', 'Sent identifiers could not be read safely.')
    tokens = data[0].split()
    if len(tokens) > WINDOW or any(not re.fullmatch(rb'[1-9][0-9]{0,9}', x) for x in tokens):
        raise SentError('sent_fetch_failed', 'Sent identifiers could not be read safely.')
    values = [int(x) for x in tokens]
    if len(set(values)) != len(values) or any(not low <= x <= high for x in values):
        raise SentError('sent_fetch_failed', 'Sent identifiers are outside the requested range.')
    return sorted(values, reverse=True)


def fetch_literal(mailbox, uid, fields, maximum):
    status, data = mailbox.uid('FETCH', str(uid), fields)
    if status != 'OK' or type(data) is not list:
        raise SentError('sent_fetch_failed', 'A Sent message could not be read.')
    if data in ([], [None], [b'']):
        return None  # An expunge during traversal is not an identity error.
    tuples = [x for x in data if type(x) is tuple]
    if len(tuples) != 1 or any(type(x) is not tuple and x not in (b')', b'', None) for x in data):
        raise SentError('sent_fetch_failed', 'An IMAP message response is malformed.')
    item = tuples[0]
    if len(item) != 2 or type(item[0]) is not bytes or type(item[1]) is not bytes or len(item[0]) > 4096 or len(item[1]) > maximum:
        raise SentError('sent_response_too_large', 'A Sent message exceeds the safe read limit.')
    meta, raw = item
    ids = re.findall(rb'\bUID ([0-9]+)\b', meta)
    size = re.search(rb'\{([0-9]+)\}\)?$', meta)
    section = b'BODY[HEADER]' if 'HEADER' in fields else b'BODY[]'
    if ids != [str(uid).encode()] or meta.count(section) != 1 or not size or int(size[1]) != len(raw):
        raise SentError('sent_identity_invalid', 'A Sent message identity could not be verified.')
    return meta, raw


def message_date(message, metadata):
    try:
        date = parsedate_to_datetime(str(message.get('Date', '')))
        if date.tzinfo is None:
            date = date.replace(tzinfo=timezone.utc)
        if not 1900 <= date.year <= 2100:
            raise ValueError()
        return date.astimezone(timezone.utc).isoformat()
    except (ValueError, TypeError, OverflowError):
        try:
            stamp = re.search(rb'INTERNALDATE "([^"]+)"', metadata)[1].decode('ascii').strip()
            return datetime.strptime(stamp, '%d-%b-%Y %H:%M:%S %z').astimezone(timezone.utc).isoformat()
        except (ValueError, TypeError, UnicodeError, OverflowError):
            return '1970-01-01T00:00:00+00:00'


def header_text(value):
    unfolded = re.sub(r'\r?\n[ \t]+', ' ', str(value))
    decoded = decode_mime_words(unfolded)
    return ''.join(c for c in decoded if ord(c) >= 32 and not 127 <= ord(c) <= 159)[:16384]


def read_page(mailbox, mailbox_id, folder, scope, key, limit=20, cursor=None, check=lambda: None):
    check()
    uv = examine(mailbox, folder)
    if cursor and (cursor['folder'] != folder or cursor['uv'] != uv):
        raise SentError('uid_validity_changed', 'Sent changed. Refresh Sent to continue.', 409)
    if cursor:
        ceiling, high = cursor['ceiling'], cursor['before']
    else:
        tag, values = mailbox.response('UIDNEXT')
        if tag != 'UIDNEXT' or type(values) is not list or len(values) != 1 or type(values[0]) is not bytes or not re.fullmatch(rb'[1-9][0-9]{0,9}', values[0]) or not uint(int(values[0])):
            raise SentError('sent_identity_invalid', 'Sent pagination could not be initialized.')
        ceiling = high = int(values[0]) - 1
    low = max(1, high - WINDOW + 1)
    uids = search_window(mailbox, low, high) if high else []
    selected = uids[:limit]
    before = selected[-1] - 1 if len(uids) >= limit else low - 1
    rows, records, consumed = [], [], 0
    for uid in selected:
        check()
        header = fetch_literal(mailbox, uid, '(UID FLAGS RFC822.SIZE INTERNALDATE BODY.PEEK[HEADER]<0.65536>)', 65536)
        if header is None:
            continue
        meta, raw = header
        sizes = re.findall(rb'\bRFC822.SIZE ([0-9]+)', meta)
        flags = re.search(rb'FLAGS \(([^)]*)\)', meta)
        if len(sizes) != 1 or flags is None:
            raise SentError('sent_fetch_failed', 'Sent message metadata is incomplete.')
        flags = flags[1].lower().split()
        if b'\\deleted' in flags or b'\\draft' in flags:
            continue
        size = int(sizes[0])
        unavailable = None
        if size > MAX_BODY or consumed + size > MAX_RAW_PAGE:
            unavailable = 'This message is too large to preview safely. Open it in your mail provider.'
        else:
            full = fetch_literal(mailbox, uid, f'(UID BODY.PEEK[]<0.{MAX_BODY + 1}>)', MAX_BODY)
            if full is None:
                continue
            _, raw = full
            if len(raw) != size:
                raise SentError('sent_fetch_failed', 'A Sent message was incomplete.')
            consumed += len(raw)
        message = BytesParser(policy=policy.compat32).parsebytes(raw)
        parts = list(message.walk())
        if any(part.defects for part in parts) or len(parts) > 256:
            unavailable = 'This message could not be displayed safely. Open it in your mail provider.'
        html = get_html_body(message) if not unavailable else None
        body = get_message_body(message, html_body=html) if not unavailable else unavailable
        date = message_date(message, meta)
        record = extract_message_thread_metadata(message, str(uid), str(uid))
        row = {'id': str(uid), 'serverMailboxId': mailbox_id, 'providerFolder': folder,
               'uidValidity': uv, 'imapUid': str(uid), 'sender': header_text(message.get('From', 'Unknown sender')),
               'from': header_text(message.get('From', 'Unknown sender')),
               'to': header_text(message.get('To', '')), 'cc': header_text(message.get('Cc', '')),
               'subject': header_text(message.get('Subject', 'Untitled message')),
               'snippet': re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', body)[:220], 'body': body.split('\n\n') if body else ['No message preview available.'],
               'createdAt': date, 'timestamp': date, 'unread': b'\\seen' not in flags,
               'flagged': b'\\flagged' in flags, 'attachments': get_message_attachments(message) if not unavailable else []}
        if html:
            row['bodyHtml'] = html
        if record['message_id']:
            row['rfcMessageId'] = record['message_id']
        rows.append(row)
        records.append(record)
    for row, thread_id in zip(rows, resolve_custom_imap_thread_ids(records, mailbox_id, folder, uv)):
        row['threadId'] = thread_id
    check()
    if examine(mailbox, folder) != uv:
        raise SentError('uid_validity_changed', 'Sent changed. Refresh Sent to continue.', 409)
    next_token = encode_cursor({'scope': scope, 'folder': folder, 'uv': uv, 'ceiling': ceiling,
                               'before': before, 'expires': cursor['expires'] if cursor else int(time.time()) + 900}, key) if before > 0 else None
    return {'serverMailboxId': mailbox_id, 'providerFolder': folder, 'uidValidity': uv,
            'scope': scope, 'messages': rows, 'scanLow': before + 1, 'scanHigh': high,
            'nextPageToken': next_token}
