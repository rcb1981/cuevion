"""Legacy create/reply/forward-and-send requires its own reviewed release.

Tokens, OAuth scopes, environment overrides and the existing-draft Send gate
cannot enable this route. Production remains independently prohibited.
"""
from api.inboxes.microsoft_production_pilot import production_pilot_writes_blocked
from api.inboxes.microsoft_graph import MicrosoftGraphError

_LEGACY_SEND_RELEASED = False


def blocked():
    return not _LEGACY_SEND_RELEASED or production_pilot_writes_blocked()


def require_release():
    if blocked():
        raise MicrosoftGraphError('microsoft_legacy_send_disabled')
