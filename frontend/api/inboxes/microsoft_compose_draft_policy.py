"""Compose drafts only. Source gate OFF; Production independently denied."""
import os
from api.inboxes.microsoft_graph import MicrosoftGraphError
from api.inboxes import microsoft_draft_editor as plain

_COMPOSE_DRAFTS_RELEASED = False


def blocked():
    return not _COMPOSE_DRAFTS_RELEASED or os.getenv('VERCEL_ENV', '').strip().lower() == 'production'


def require_release():
    if blocked():
        raise MicrosoftGraphError('microsoft_compose_disabled')


def validate_edit(value):
    # Drafts can intentionally have empty fields, unlike a send request.
    if type(value) is not dict or set(value) != {'subject', 'to', 'cc', 'bcc', 'body'}:
        raise MicrosoftGraphError('microsoft_compose_invalid')
    try:
        base = plain.validate_edit({k: value[k] for k in ('subject', 'to', 'cc', 'body')})
        bcc = plain._addresses(value['bcc'])
    except MicrosoftGraphError:
        raise MicrosoftGraphError('microsoft_compose_invalid') from None
    addresses = base['to'] + base['cc'] + bcc
    if len(addresses) > plain.MAX_RECIPIENTS or len(set(addresses)) != len(addresses):
        raise MicrosoftGraphError('microsoft_compose_invalid')
    return {**base, 'bcc': bcc}
