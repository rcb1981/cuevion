"""Small server-only Sent preference in the existing owner config; no new store."""
import hashlib
import json
from copy import deepcopy

from api import user_config_store as config_store
from .imap_sent import is_runtime_compatible_mailbox_name

FIELD = 'imapSentFolderPreferences'
MAX_PREFERENCES = 64


def preference_id(owned, mailbox_id):
    member = owned['memberAuthority']
    return hashlib.sha256(json.dumps([member.user_id, member.workspace_id, mailbox_id]).encode()).hexdigest()


def remembered_folder(owned, mailbox_id, scope):
    preferences = owned['config'].get(FIELD, {})
    item = preferences.get(preference_id(owned, mailbox_id)) if type(preferences) is dict else None
    if (type(item) is dict and set(item) == {'scope', 'folder'} and item['scope'] == scope
            and is_runtime_compatible_mailbox_name(item['folder'])):
        return item['folder']
    return None


def remember_folder(owned, mailbox_id, scope, folder):
    """One CAS, no retry or write when unchanged; never overwrite concurrent edits."""
    if remembered_folder(owned, mailbox_id, scope) == folder:
        return True
    if not is_runtime_compatible_mailbox_name(folder):
        return False
    original = owned['config']
    preferences = original.get(FIELD, {})
    slot = preference_id(owned, mailbox_id)
    if type(preferences) is not dict or (slot not in preferences and len(preferences) >= MAX_PREFERENCES):
        return False
    replacement = deepcopy(original)
    replacement.setdefault(FIELD, {})[slot] = {'scope': scope, 'folder': folder}
    if len(json.dumps(replacement).encode()) > config_store.MAX_USER_CONFIG_STORE_RESPONSE_BYTES:
        return False
    store, error = config_store.resolve_user_config_store()
    if error or not store:
        return False
    result = config_store.write_user_config_record_if_unchanged(
        store, owned['user']['email'], original, replacement)
    return result.get('status') == 'ok'
