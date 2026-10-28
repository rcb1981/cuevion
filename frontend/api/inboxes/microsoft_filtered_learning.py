"""Owner-scoped, durable Microsoft Filtered/Learning preferences.

These are application-only preferences stored in the existing authenticated
user-config CAS record. They never move, mark, or otherwise mutate Graph mail,
and do not alter Gmail/IMAP learning or mailbox synchronization tables.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import re

from api.inboxes.microsoft_mailbox_access import (
    MicrosoftMailboxAccessError,
    resolve_owned_microsoft_mailbox,
)
from api import user_config_store

FIELD = "microsoftFilteredLearning"
MAX_MAILBOXES = 12
MAX_SENDERS = 200
MAX_ATTEMPTS = 4
MAX_TOTAL_STATE_BYTES = 128000
MAILBOX_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9:_-]{0,159}$")
SENDER = re.compile(r"^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$")
PRIORITIES = frozenset(("important", "normal", "show_less"))
LABELS = frozenset(("Demo", "Promo", "Business", "Finance", "Update", "Reply", "Other"))
PLACEMENTS = frozenset(("show_less", "keep"))


class MicrosoftFilteredLearningError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _valid_sender(value: object) -> bool:
    return (type(value) is str and len(value) <= 320 and
            SENDER.fullmatch(value) is not None)


def _validate_placements(value: object) -> dict:
    if type(value) is not dict or len(value) > MAX_SENDERS:
        raise MicrosoftFilteredLearningError("invalid_learning")
    if any(not _valid_sender(key) or type(mode) is not str or mode not in PLACEMENTS
           for key, mode in value.items()):
        raise MicrosoftFilteredLearningError("invalid_learning")
    if len(json.dumps(value, separators=(",", ":"))) > 24000:
        raise MicrosoftFilteredLearningError("invalid_learning")
    return dict(value)


def _validate_rules(value: object) -> dict:
    if type(value) is not dict or len(value) > MAX_SENDERS:
        raise MicrosoftFilteredLearningError("invalid_learning")
    for sender, rule in value.items():
        if (not _valid_sender(sender) or type(rule) is not dict or
                set(rule) != {"priority", "label"} or
                type(rule["priority"]) is not str or type(rule["label"]) is not str or
                rule["priority"] not in PRIORITIES or
                rule["label"] not in LABELS):
            raise MicrosoftFilteredLearningError("invalid_learning")
    if len(json.dumps(value, separators=(",", ":"))) > 26000:
        raise MicrosoftFilteredLearningError("invalid_learning")
    return deepcopy(value)


def _read_states(config: dict) -> dict:
    states = config.get(FIELD, {})
    if type(states) is not dict or len(states) > MAX_MAILBOXES:
        raise MicrosoftFilteredLearningError("learning_unavailable")
    if len(json.dumps(states, separators=(",", ":"))) > MAX_TOTAL_STATE_BYTES:
        raise MicrosoftFilteredLearningError("learning_unavailable")
    for mailbox_id, state in states.items():
        if (type(mailbox_id) is not str or MAILBOX_ID.fullmatch(mailbox_id) is None or
                type(state) is not dict or set(state) != {"placements", "rules"}):
            raise MicrosoftFilteredLearningError("learning_unavailable")
        try:
            _validate_placements(state["placements"])
            _validate_rules(state["rules"])
        except MicrosoftFilteredLearningError:
            raise MicrosoftFilteredLearningError("learning_unavailable") from None
    return states


def _project(states: dict, mailbox_id: str) -> dict:
    state = states.get(mailbox_id)
    return {
        "ok": True,
        "exists": state is not None,
        "placements": deepcopy(state["placements"]) if state else {},
        "rules": deepcopy(state["rules"]) if state else {},
    }


def _validate_action(action: str, payload: dict) -> None:
    allowed = {
        "read": {"mailboxId", "action"},
        "bootstrap": {"mailboxId", "action", "placements", "rules"},
        "setPlacement": {"mailboxId", "action", "sender", "placement"},
        "setRule": {"mailboxId", "action", "sender", "rule"},
    }
    if action not in allowed or set(payload) != allowed[action]:
        raise MicrosoftFilteredLearningError("invalid_learning")
    if action == "bootstrap":
        _validate_placements(payload["placements"])
        _validate_rules(payload["rules"])
    if action in {"setPlacement", "setRule"} and not _valid_sender(payload["sender"]):
        raise MicrosoftFilteredLearningError("invalid_learning")
    if action == "setPlacement" and (
        type(payload["placement"]) is not str or payload["placement"] not in PLACEMENTS
    ):
        raise MicrosoftFilteredLearningError("invalid_learning")
    if action == "setRule":
        _validate_rules({payload["sender"]: payload["rule"]})


def apply_owned_learning_action(headers, payload: object) -> dict:
    """CAS-retry per sender; never replace other mailboxes' or senders' data."""
    if type(payload) is not dict or type(payload.get("action")) is not str:
        raise MicrosoftFilteredLearningError("invalid_learning")
    mailbox_id = payload.get("mailboxId")
    if type(mailbox_id) is not str or MAILBOX_ID.fullmatch(mailbox_id) is None:
        raise MicrosoftFilteredLearningError("invalid_learning")
    action = payload["action"]
    _validate_action(action, payload)

    store, error = user_config_store.resolve_user_config_store()
    if error or not store:
        raise MicrosoftFilteredLearningError("learning_unavailable")

    for _attempt in range(MAX_ATTEMPTS if action != "read" else 1):
        # Authentication + exact connected mailbox are rechecked for every
        # attempt; a selector supplied by a browser is never authorization.
        try:
            authority = resolve_owned_microsoft_mailbox(headers, mailbox_id)
        except MicrosoftMailboxAccessError as exc:
            raise MicrosoftFilteredLearningError(exc.code) from None
        read = user_config_store.read_user_config_record(store, authority.owner_email)
        if type(read) is not dict or read.get("status") != "ok" or type(read.get("config")) is not dict:
            raise MicrosoftFilteredLearningError("learning_unavailable")
        config = read["config"]
        if config.get("email") != authority.owner_email:
            raise MicrosoftFilteredLearningError("learning_unavailable")
        states = _read_states(config)
        if action == "read" or (action == "bootstrap" and mailbox_id in states):
            return _project(states, mailbox_id)
        next_states = deepcopy(states)
        if mailbox_id not in next_states:
            if len(next_states) >= MAX_MAILBOXES:
                raise MicrosoftFilteredLearningError("learning_unavailable")
            next_states[mailbox_id] = {"placements": {}, "rules": {}}
        item = next_states[mailbox_id]
        if action == "bootstrap":
            item["placements"] = _validate_placements(payload["placements"])
            item["rules"] = _validate_rules(payload["rules"])
        elif action == "setPlacement":
            if (payload["sender"] not in item["placements"] and
                    len(item["placements"]) >= MAX_SENDERS):
                raise MicrosoftFilteredLearningError("learning_limit_reached")
            item["placements"][payload["sender"]] = payload["placement"]
            _validate_placements(item["placements"])
        elif action == "setRule":
            if (payload["sender"] not in item["rules"] and
                    len(item["rules"]) >= MAX_SENDERS):
                raise MicrosoftFilteredLearningError("learning_limit_reached")
            item["rules"][payload["sender"]] = deepcopy(payload["rule"])
            _validate_rules(item["rules"])

        if len(json.dumps(next_states, separators=(",", ":"))) > MAX_TOTAL_STATE_BYTES:
            raise MicrosoftFilteredLearningError("learning_limit_reached")

        replacement = deepcopy(config)
        replacement[FIELD] = next_states
        replacement["updatedAt"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        # A parallel user Settings save cannot erase a placement: retry and
        # merge against the newly read config, preserving all other fields.
        result = user_config_store.write_user_config_record_if_unchanged(
            store, authority.owner_email, config, replacement,
        )
        if type(result) is dict and result.get("status") == "ok":
            return _project(next_states, mailbox_id)
        if type(result) is not dict or result.get("status") != "conflict":
            raise MicrosoftFilteredLearningError("learning_unavailable")
    raise MicrosoftFilteredLearningError("learning_conflict")
