"""Read-only Sent discovery. Wire names are opaque; labels are display-only."""
import base64
import imaplib
import re

from .imap_folder_inventory import (
    is_runtime_compatible_mailbox_name, is_selectable_imap_list_entry,
    read_imap_list_inventory,
)


class SentError(Exception):
    def __init__(self, code, message, status=502):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def quote_folder(folder):
    if not is_runtime_compatible_mailbox_name(folder):
        raise SentError("invalid_request", "Invalid Sent folder.", 400)
    return '"' + folder.replace('\\', '\\\\').replace('"', '\\"') + '"'


def folder_label(wire):
    def decode(match):
        token = match.group(1)
        if not token:
            return "&"
        try:
            raw = base64.b64decode(token.replace(',', '/') + '=' * (-len(token) % 4), validate=True)
            return raw.decode('utf-16-be')
        except (ValueError, UnicodeError):
            return match.group(0)
    label = re.sub(r'&([A-Za-z0-9+,]*)-', decode, wire)
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', label)


def populated_candidate(mailbox, candidates, check):
    # EXAMINE is already supported by this reader. It returns EXISTS metadata
    # without fetching bodies or setting flags. Bound the extra work tightly.
    if not 1 <= len(candidates) <= 4:
        return None
    populated = []
    for folder in candidates:
        check()
        try:
            status, data = mailbox.select(quote_folder(folder), readonly=True)
        except (imaplib.IMAP4.error, OSError):
            return None
        check()
        if (status != 'OK' or type(data) is not list or len(data) != 1
                or type(data[0]) is not bytes or not re.fullmatch(rb'0|[1-9][0-9]{0,9}', data[0])
                or int(data[0]) > 4294967295):
            return None
        if int(data[0]) > 0:
            populated.append(folder)
        if len(populated) > 1:
            return None
    return populated[0] if len(populated) == 1 else None


def discover_sent(mailbox, choice=None, *, remembered=None, check=lambda: None):
    # LOGIN may cache a CAPABILITY response code. Consume it before asking for
    # fresh capabilities so imaplib does not combine the two response lists.
    mailbox.response('CAPABILITY')
    status, data = mailbox.capability()
    if status != 'OK' or type(data) is not list or len(data) != 1 or type(data[0]) is not bytes or len(data[0]) > 16384:
        raise SentError('sent_inventory_unavailable', 'IMAP folder capabilities could not be verified.')
    try:
        capabilities = set(data[0].decode('ascii').upper().split())
    except UnicodeError:
        raise SentError('sent_inventory_unavailable', 'IMAP folder capabilities could not be verified.') from None
    if 'SPECIAL-USE' in capabilities:
        # Constant extension syntax: newer imaplib versions quote list()'s
        # pattern argument, so RETURN must be a separate protocol argument.
        status, data = mailbox._simple_command('LIST', '""', '"*"', 'RETURN', '(SPECIAL-USE)')
        response = mailbox._untagged_response(status, data, 'LIST')
    else:
        response = mailbox.list('""', '"*"')
    class Inventory:
        def list(self):
            return response
    inventory = read_imap_list_inventory(Inventory())
    if inventory.error:
        raise SentError("sent_inventory_unavailable", "Sent folders could not be read safely.")
    safe = []
    seen = set()
    for entry in inventory.entries:
        if entry.mailbox in seen:
            raise SentError("sent_inventory_unavailable", "Sent folder metadata is ambiguous.")
        seen.add(entry.mailbox)
        if (not is_selectable_imap_list_entry(entry)
                or not is_runtime_compatible_mailbox_name(entry.mailbox)
                or entry.mailbox.casefold() == 'inbox'
                or entry.attributes & {r'\trash', r'\junk', r'\drafts', r'\all', r'\archive'}):
            continue
        # Current connection uses IMAP4rev1 wire encoding. Modified UTF-7 stays ASCII.
        if not entry.mailbox.isascii() and not getattr(mailbox, 'utf8_enabled', False):
            continue
        safe.append(entry)
    authoritative = [e for e in safe if r'\sent' in e.attributes]
    candidates = authoritative if authoritative else safe
    if len(candidates) > 256:
        raise SentError("sent_inventory_unavailable", "Too many folders to select Sent safely.")
    if choice is not None:
        if choice not in {e.mailbox for e in candidates}:
            raise SentError("sent_folder_changed", "The selected Sent folder is no longer available. Choose it again.", 409)
        selected = choice
    else:
        selected = authoritative[0].mailbox if len(authoritative) == 1 else None
    suggestions = {'sent', 'sent items', 'sent messages', 'inbox.sent'}
    folders = []
    for entry in candidates:
        label = folder_label(entry.mailbox)
        leaf = label.rsplit(entry.delimiter, 1)[-1] if entry.delimiter else label
        folders.append({"providerFolder": entry.mailbox, "displayName": label,
                        "suggested": r'\sent' in entry.attributes or leaf.casefold() in suggestions})
    if selected is None and choice is None:
        if remembered is not None:
            # A deleted/changed saved folder requires a new explicit decision.
            selected = remembered if remembered in {e.mailbox for e in candidates} else None
        else:
            plausible = [f['providerFolder'] for f in folders if f['suggested']]
            selected = populated_candidate(mailbox, plausible, check)
    folders.sort(key=lambda item: (not item['suggested'], item['displayName'], item['providerFolder']))
    return {"mode": "ready" if selected else "choose" if folders else "unavailable",
            "providerFolder": selected, "folders": folders}
