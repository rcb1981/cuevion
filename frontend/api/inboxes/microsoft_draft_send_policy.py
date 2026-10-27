"""Existing-draft sending is independently source-gated OFF."""
import os
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes import microsoft_compose_draft_policy as compose

_DRAFT_SEND_RELEASED = False


def status_blocked():
    return os.getenv('VERCEL_ENV', '').strip().lower() == 'production'


def require_status():
    if status_blocked():
        raise MicrosoftGraphError('microsoft_draft_send_disabled')


def blocked():
    return not _DRAFT_SEND_RELEASED or status_blocked()


def require_release():
    if blocked():
        raise MicrosoftGraphError('microsoft_draft_send_disabled')


def validate_send(edit):
    result = compose.validate_edit(edit)
    if not (result['to'] + result['cc'] + result['bcc']) or not result['subject'].strip() or not result['body'].strip():
        raise MicrosoftGraphError('microsoft_draft_send_invalid')
    return result
