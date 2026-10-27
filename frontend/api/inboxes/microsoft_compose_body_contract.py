"""Compose-only body proof. Never changes the shared HTML/CID reader.

Graph GET defaults to HTML. Its text preference is a projection, not proof of
native plain text. Accept that projection only with Preference-Applied and an
independent, strictly bounded MIME text/plain body. No HTML flattening, charset
fallback, trimming or Unicode normalization. CRLF/LF differ only as line endings.
https://learn.microsoft.com/en-us/graph/api/message-get?view=graph-rest-1.0
"""
import re
from itertools import islice
from contextlib import contextmanager
from email import policy
from email.parser import BytesParser, BytesHeaderParser
from urllib.error import HTTPError, URLError
from urllib.request import Request
from api.inboxes.microsoft_graph import (
    MicrosoftGraphError, GRAPH_ROOT, GRAPH_TIMEOUT_SECONDS, MAX_GRAPH_JSON_BYTES,
    _GRAPH_PREFER, _GRAPH_OPENER, _get_json, _required_access_token,
)

MAX_MIME_BYTES = 16 * 1024 * 1024
CODE = 'microsoft_compose_body_unverifiable'


def fail():
    raise MicrosoftGraphError(CODE)


def native_text(raw):
    # Bound parser work before building a MIME tree; nested multipart is outside
    # this initial contract. Conservative false rejections never authorize Send.
    if len(raw) > MAX_MIME_BYTES:
        fail()
    lower = raw.lower()
    if (lower.count(b'content-type:') > 32
            or lower.count(b'multipart/') > 1
            or sum(1 for _ in islice(re.finditer(rb'(?m)^--', raw), 14)) > 13):
        fail()
    try:
        # Bound even parts with no headers before the recursive MIME parser runs.
        breaks = [n for n in (raw.find(b'\r\n\r\n'), raw.find(b'\n\n')) if n >= 0]
        if not breaks or min(breaks) > 64 * 1024:
            fail()
        header = BytesHeaderParser(policy=policy.default).parsebytes(raw[:min(breaks)])
        boundary = header.get_boundary()
        if header.get_content_maintype() == 'multipart':
            if not boundary or len(boundary) > 70 or raw.count(b'--' + boundary.encode('ascii')) > 13:
                fail()
        message = BytesParser(policy=policy.default).parsebytes(raw)
        parts = list(message.walk())
        if len(parts) > 12:
            fail()
        for part in parts:
            if part.defects or any(getattr(value, 'defects', ()) for _key, value in part.items()) or any(len(part.get_all(h, [])) > 1 for h in
                                  ('content-type', 'content-transfer-encoding', 'content-disposition')):
                fail()
        if message.is_multipart():
            if message.get_content_type() != 'multipart/mixed':
                fail()
            children = list(message.iter_parts())
            if not children or any(p.is_multipart() for p in children):
                fail()
            if any(p.get_content_disposition() != 'attachment' for p in children[1:]):
                fail()
            body = children[0]
        else:
            body = message
        if (len(body.get_all('Content-Type', [])) != 1 or body.get_filename() is not None
                or body.get_content_type() != 'text/plain' or body.get_content_disposition() is not None
                or body.get_content_charset('us-ascii') not in {'us-ascii', 'utf-8'}
                or str(body.get('Content-Transfer-Encoding', '7bit')).lower() not in
                   {'7bit', '8bit', 'base64', 'quoted-printable'}):
            fail()
        encoded = body.get_payload()
        if (body.get('Content-Transfer-Encoding', '').lower() == 'quoted-printable'
                and re.search(r'=(?![0-9a-fA-F]{2}|\r?\n)', encoded)):
            fail()
        data = body.get_payload(decode=True)
        if body.defects or type(data) is not bytes or len(data) > 512 * 1024:
            fail()
        return data.decode(body.get_content_charset('us-ascii'), errors='strict').replace('\r\n', '\n')
    except MicrosoftGraphError:
        raise
    except Exception:
        fail()


def verified_text(token, path, reference, *, opener=None):
    # Paths are constructed by server callers from a validated immutable ID.
    opener = opener or _GRAPH_OPENER.open
    @contextmanager
    def preferred(request, **kwargs):
        request.add_header('Prefer', _GRAPH_PREFER + ', outlook.body-content-type="text"')
        with opener(request, **kwargs) as response:
            headers = getattr(response, 'headers', None)
            applied = (headers.get_all('Preference-Applied', []) if hasattr(headers, 'get_all')
                       else [headers.get('Preference-Applied', '')] if headers is not None else [])
            if not applied or any(type(v) is not str or len(v) > 4096 for v in applied):
                fail()
            values = [v.strip().lower() for v in ','.join(applied).split(',')]
            if values.count('outlook.body-content-type="text"') != 1 or any(
                    v.startswith('outlook.body-content-type') and v != 'outlook.body-content-type="text"' for v in values):
                fail()
            yield response
    projected = _get_json(path, token, maximum_bytes=MAX_GRAPH_JSON_BYTES, opener=preferred)
    body = projected.get('body')
    if (type(body) is not dict or set(body) != {'contentType', 'content'}
            or body.get('contentType') not in {'text', 'Text'} or type(body.get('content')) is not str
            or {k:v for k,v in projected.items() if k != 'body'} != {k:v for k,v in reference.items() if k != 'body'}):
        fail()
    request = Request(GRAPH_ROOT + path.split('?')[0] + '/$value', method='GET', headers={
        'Authorization':'Bearer ' + _required_access_token(token), 'Accept':'message/rfc822', 'Prefer':_GRAPH_PREFER})
    try:
        with opener(request, timeout=GRAPH_TIMEOUT_SECONDS) as response:
            if getattr(response, 'status', None) != 200:
                fail()
            raw = response.read(MAX_MIME_BYTES + 1)
        if type(raw) is not bytes or len(raw) > MAX_MIME_BYTES:
            fail()
        text = native_text(raw)
        if text != body['content'].replace('\r\n', '\n'):
            fail()
        return text
    except HTTPError as error:
        error.close()
        fail()
    except (URLError, TimeoutError, OSError):
        fail()
