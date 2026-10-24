"""Source-only attachment release gate and ephemeral selection validation."""
import os
from types import SimpleNamespace
from api.inboxes import microsoft_draft_attachment_ledger as ledger

_DRAFT_ATTACHMENTS_RELEASED = False
MAX_NEW_FILES = 10
MAX_NEW_BYTES = 8 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024


def writes_blocked():
    # No environment value/browser flag can release these writes. Production
    # stays denied independently, even in tests that override the source gate.
    return not _DRAFT_ATTACHMENTS_RELEASED or os.getenv('VERCEL_ENV', '').strip().lower() == 'production'


def require_release():
    if writes_blocked():
        ledger.fail('disabled')


def validate_files(files):
    if type(files) is not list or not 1 <= len(files) <= MAX_NEW_FILES:
        ledger.fail('selection_invalid')
    checked, total = [], 0
    for file in files:
        if type(file) is not dict or set(file) != {'name', 'content_type', 'content'}:
            ledger.fail('selection_invalid')
        intent = ledger._intent(SimpleNamespace(attachments=()), 'add', **file)
        total += len(file['content'])
        if total > MAX_NEW_BYTES:
            ledger.fail('selection_invalid')
        checked.append((dict(file), intent))
    return checked
