from __future__ import annotations

import json

from . import redis_store


class StatefulV2Store:
    """Non-authoritative wrapper simulator; it is never evidence of Lua correctness."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}
        self.commands: list[list] = []

    @staticmethod
    def _record_kind(value: object) -> str | None:
        if not isinstance(value, dict):
            return None
        if "sessionHash" in value:
            return "session"
        if "tokenHash" in value:
            return "invite"
        if "sourceRef" in value and "messages" in value:
            return "thread"
        return None

    @classmethod
    def _decode_raw(cls, raw: str) -> dict | None:
        try:
            wire = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return None
        kind = cls._record_kind(wire)
        return redis_store._v2_record_from_wire(wire, kind) if kind else None

    def put_json(self, key: str, value: dict) -> None:
        kind = self._record_kind(value)
        raw = redis_store._v2_wire_json(value, kind) if kind else None
        if raw is None:
            raise ValueError("StatefulV2Store accepts only typed v2 records")
        self.values[key] = raw

    def get_json(self, key: str) -> dict | None:
        value = self.values.get(key)
        return self._decode_raw(value) if value is not None else None

    def __call__(self, command: list) -> dict:
        self.commands.append(command)
        if command[0] == "GET" and len(command) == 2:
            return {"result": self.values.get(command[1])}
        if command[0] != "EVAL" or not isinstance(command[2], int):
            raise AssertionError(f"unexpected command: {command!r}")
        script = command[1]
        key_count = command[2]
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        handlers = {
            redis_store._CREATE_V2_THREAD_LUA: self._create_thread,
            redis_store._LOAD_AND_MIGRATE_V2_SOURCE_LUA: self._load_source,
            redis_store._SAVE_V2_THREAD_CAS_LUA: self._save_thread,
            redis_store._CREATE_V2_INVITE_LUA: self._create_invite,
            redis_store._EXCHANGE_V2_INVITE_LUA: self._exchange_invite,
            redis_store._UPDATE_V2_SESSION_LUA: self._update_session,
            redis_store._REVOKE_V2_INVITE_LUA: self._revoke_invite,
            redis_store._REVOKE_V2_SESSION_LUA: self._revoke_session,
        }
        handler = handlers.get(script)
        if handler is None:
            raise AssertionError("unexpected EVAL script")
        return {"result": json.dumps(handler(keys, args), separators=(",", ":"))}

    def _create_thread(self, keys, args):
        current = self.values.get(keys[1])
        previous = self.values.get(keys[2]) if len(keys) == 3 else None
        if current and previous and current != previous:
            return {"status": "source_pointer_conflict"}
        existing = current or previous
        if existing is not None:
            target_key = args[3] + existing
            if target_key not in self.values:
                self.values.pop(keys[1], None)
                if len(keys) == 3:
                    self.values.pop(keys[2], None)
            else:
                pointer_key = keys[1] if current else keys[2]
                if self.ttls.get(pointer_key, 0) <= 0:
                    return {"status": "source_pointer_conflict"}
                try:
                    target = self.get_json(target_key)
                except (TypeError, json.JSONDecodeError):
                    return {"status": "source_pointer_conflict"}
                proposed = self._decode_raw(args[0])
                if (
                    target is None
                    or proposed is None
                    or target.get("collaborationId") != existing
                    or target.get("ownerEmail") != proposed.get("ownerEmail")
                    or target.get("workspaceId") != proposed.get("workspaceId")
                    or target.get("mailboxId") != proposed.get("mailboxId")
                    or target.get("sourceRef") != proposed.get("sourceRef")
                ):
                    return {"status": "source_pointer_conflict"}
                self.ttls[target_key] = self.ttls[keys[1]] = int(args[2])
                self.values[keys[1]] = existing
                if len(keys) == 3:
                    self.values.pop(keys[2], None)
                return {"status": "duplicate", "collaborationId": existing}
        if keys[0] in self.values:
            return {"status": "conflict"}
        self.values[keys[0]], self.values[keys[1]] = args[0], args[1]
        self.ttls[keys[0]] = self.ttls[keys[1]] = int(args[2])
        return {"status": "created"}

    def _load_source(self, keys, args):
        current = self.values.get(keys[0])
        previous = self.values.get(keys[1]) if len(keys) == 2 else None
        if current and previous and current != previous:
            return {"status": "conflict"}
        pointer = current or previous
        if pointer is None:
            return {"status": "missing"}
        target = self.get_json(args[0] + pointer)
        expected_source = json.loads(args[3])
        if (
            target is None
            or target.get("collaborationId") != pointer
            or target.get("ownerEmail") != args[1]
            or target.get("workspaceId") != args[1]
            or target.get("mailboxId") != args[2]
            or target.get("sourceRef") != expected_source
        ):
            return {"status": "conflict"}
        if previous:
            self.values[keys[0]] = pointer
            self.ttls[keys[0]] = min(self.ttls.get(keys[1], 0), self.ttls.get(args[0] + pointer, 0))
            self.values.pop(keys[1], None)
        return {"status": "found", "collaborationId": pointer}

    def _save_thread(self, keys, args):
        current = self.get_json(keys[0])
        if current is None:
            return {"status": "missing"}
        replacement = self._decode_raw(args[1])
        if current.get("v") != 2 or replacement.get("v") != 2 or not isinstance(current.get("updatedAt"), int):
            return {"status": "malformed"}
        immutable = ("collaborationId", "ownerEmail", "workspaceId", "mailboxId", "sourceRef")
        if any(current.get(field) != replacement.get(field) for field in immutable):
            return {"status": "invalid_scope"}
        if str(current["updatedAt"]) != args[0]:
            return {"status": "stale"}
        if replacement.get("updatedAt", -1) <= current["updatedAt"]:
            return {"status": "nonadvancing"}
        if self.values.get(keys[1]) != current.get("collaborationId"):
            return {"status": "source_pointer_conflict"}
        if len(replacement.get("messages", [])) != len(current.get("messages", [])) + 1:
            return {"status": "invalid_messages"}
        if replacement["messages"][:-1] != current.get("messages"):
            return {"status": "invalid_messages"}
        self.values[keys[0]] = args[1]
        self.ttls[keys[0]] = self.ttls[keys[1]] = int(args[2])
        return {"status": "saved"}

    def _create_invite(self, keys, args):
        current = self.get_json(keys[2])
        if current and current.get("status") == "active" and current.get("expiresAt", 0) > int(args[2]):
            return {"status": "duplicate", "inviteId": current["inviteId"]}
        if keys[0] in self.values or keys[1] in self.values:
            return {"status": "conflict"}
        self.values[keys[0]], self.values[keys[1]], self.values[keys[2]] = args[0], args[3], args[0]
        for key in keys:
            self.ttls[key] = int(args[1])
        return {"status": "created"}

    def _exchange_invite(self, keys, args):
        if self.values.get(keys[0]) != args[0]:
            return {"status": "missing"}
        invite = self.get_json(keys[1])
        if invite is None:
            return {"status": "missing"}
        now = int(args[1])
        session = self._decode_raw(args[3])
        if invite.get("expiresAt", 0) <= now:
            return {"status": "expired"}
        if invite.get("status") == "revoked":
            return {"status": "revoked"}
        expected = {
            "v": 2, "inviteId": args[0], "tokenHash": args[14],
            "ownerEmail": args[7], "workspaceId": args[8], "mailboxId": args[9],
            "collaborationId": args[10], "identityAssurance": "link_possession",
            "allowedActions": ["read", "reply"], "visibility": "shared_only",
        }
        if any(invite.get(key) != value for key, value in expected.items()) or (invite.get("invitedEmail") or "") != args[11]:
            return {"status": "malformed"}
        if invite.get("status") != "active" or invite.get("exchangeCount") != 0 or invite.get("activeSessionHash") is not None:
            return {"status": "exchanged"}
        session_expected = {
            "v": 2, "inviteId": args[0], "sessionHash": args[4], "csrfTokenHash": args[5],
            "ownerEmail": args[7], "workspaceId": args[8], "mailboxId": args[9],
            "collaborationId": args[10], "allowedActions": ["read", "reply"],
            "visibility": "shared_only", "identityAssurance": "link_possession",
            "status": "active", "createdAt": int(args[12]), "lastUsedAt": int(args[12]),
            "expiresAt": int(args[13]), "revokedAt": None, "loggedOutAt": None,
        }
        if any(session.get(key) != value for key, value in session_expected.items()):
            return {"status": "malformed"}
        if session["expiresAt"] > invite["expiresAt"] or session["expiresAt"] - now > 28_800:
            return {"status": "malformed"}
        if keys[2] in self.values:
            return {"status": "conflict"}
        invite.update(status="exchanged", exchangedAt=now, exchangeCount=1, activeSessionHash=args[4])
        self.put_json(keys[1], invite)
        self.values[keys[2]] = args[3]
        self.ttls[keys[1]], self.ttls[keys[2]] = int(args[2]), int(args[6])
        return {"status": "exchanged_ok"}

    def _update_session(self, keys, args):
        session = self.get_json(keys[0])
        if session is None:
            return {"status": "missing"}
        now = int(args[0])
        if session.get("status") != "active":
            return {"status": "revoked"}
        if session.get("expiresAt", 0) <= now:
            return {"status": "expired"}
        if session.get("csrfTokenHash") != args[4]:
            return {"status": "stale"}
        if args[3] == "1":
            session["lastUsedAt"] = now
        if args[1]:
            session["csrfTokenHash"] = args[1]
        self.put_json(keys[0], session)
        self.ttls[keys[0]] = int(args[2])
        return {
            "status": "updated",
            "session": redis_store.encode_v2_wire_record(session, "session"),
        }

    def _revoke_invite(self, keys, args):
        invite = self.get_json(keys[0])
        if invite is None:
            return {"status": "missing"}
        if redis_store.normalize_v2_invite_record(invite) is None:
            return {"status": "malformed"}
        if (
            invite.get("ownerEmail") != args[0]
            or invite.get("workspaceId") != args[1]
            or invite.get("mailboxId") != args[2]
            or invite.get("collaborationId") != args[3]
            or invite.get("inviteId") != args[4]
            or args[5] != args[0]
            or args[6] != "revoke_invite"
        ):
            return {"status": "forbidden"}
        if invite.get("v") != 2 or invite.get("workspaceId") != invite.get("ownerEmail"):
            return {"status": "malformed"}
        if invite.get("status") == "revoked":
            return {"status": "already_revoked"}
        if invite.get("activeSessionHash") and len(keys) < 2:
            return {"status": "retry"}
        now = int(args[7])
        if len(keys) > 1 and keys[1] in self.values:
            session = self.get_json(keys[1])
            from .guest_session import normalize_v2_guest_session_record
            if (
                normalize_v2_guest_session_record(session) is None
                or session.get("sessionHash") != invite.get("activeSessionHash")
                or session.get("inviteId") != invite.get("inviteId")
                or session.get("ownerEmail") != invite.get("ownerEmail")
                or session.get("workspaceId") != invite.get("workspaceId")
                or session.get("mailboxId") != invite.get("mailboxId")
                or session.get("collaborationId") != invite.get("collaborationId")
                or session.get("allowedActions") != ["read", "reply"]
                or session.get("visibility") != "shared_only"
                or session.get("expiresAt", 0) > invite.get("expiresAt", -1)
            ):
                return {"status": "malformed"}
            session.update(status="revoked", revokedAt=now)
            self.put_json(keys[1], session)
        invite.update(status="revoked", revokedAt=now, revokedBy=args[5])
        self.put_json(keys[0], invite)
        return {"status": "revoked_ok"}

    def _revoke_session(self, keys, args):
        session = self.get_json(keys[0])
        if session is None:
            return {"status": "missing"}
        if session.get("v") != 2 or session.get("sessionHash") != args[1]:
            return {"status": "malformed"}
        if session.get("status") in {"revoked", "logged_out"} or session.get("loggedOutAt") is not None:
            return {"status": "already_logged_out"}
        now = int(args[0])
        session.update(status="logged_out", loggedOutAt=now)
        self.put_json(keys[0], session)
        return {"status": "revoked_ok"}
