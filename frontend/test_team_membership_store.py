from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from api.auth.runtime import AuthenticatedMemberContext
from api.team import authority
from api.team import members as team_members


NOW_MS = 1_800_000_000_000


def owner(workspace_id: str = "workspace-a") -> AuthenticatedMemberContext:
    return AuthenticatedMemberContext(
        user_id="user-owner",
        email="owner@example.test",
        name="Owner",
        workspace_id=workspace_id,
        membership_role="owner",
    )


def recipient(
    email: str = "recipient@example.test",
    *,
    user_id: str = "user-recipient",
) -> AuthenticatedMemberContext:
    return AuthenticatedMemberContext(
        user_id=user_id,
        email=email,
        name="Recipient Account",
        workspace_id="recipient-home",
        membership_role="member",
    )


class MutableClock:
    def __init__(self, value: int = NOW_MS):
        self.value = value

    def __call__(self) -> int:
        return self.value


class FixedRandom:
    def __init__(self):
        self.counter = 1

    def __call__(self, size: int) -> bytes:
        value = bytes((self.counter,)) * size
        self.counter += 1
        return value


class MemoryTeamRedis:
    """Interpret the production command boundary while keeping tests offline."""

    def __init__(self):
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []
        self.lose_ack_once: set[str] = set()
        self.false_applied_once: set[str] = set()
        self.forced_result_once: dict[str, str] = {}

    @staticmethod
    def _decode(raw: str | None):
        return json.loads(raw) if raw is not None else None

    @staticmethod
    def _index(raw: str | None) -> list[str]:
        value = json.loads(raw) if raw is not None else []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ValueError("malformed index")
        return list(dict.fromkeys(value))

    @staticmethod
    def _wire(value: object) -> str:
        return json.dumps(value, separators=(",", ":"), sort_keys=True)

    @staticmethod
    def _add(values: list[str], wanted: str) -> list[str]:
        return values if wanted in values else [*values, wanted]

    @staticmethod
    def _remove(values: list[str], unwanted: str) -> list[str]:
        return [value for value in values if value != unwanted]

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(list(command))
        if command[0] == "GET":
            return {"result": self.values.get(str(command[1]))}
        if command[0] != "EVAL":
            raise AssertionError(f"unexpected command: {command[0]!r}")

        script = command[1]
        key_count = int(command[2])
        keys = [str(value) for value in command[3 : 3 + key_count]]
        arguments = [str(value) for value in command[3 + key_count :]]
        operation = self._operation(script, arguments)

        forced_result = self.forced_result_once.pop(operation, None)
        if forced_result is not None:
            return {"result": forced_result}

        if operation in self.false_applied_once:
            self.false_applied_once.remove(operation)
            return {"result": "applied"}

        result = self._apply(operation, keys, arguments)
        if operation in self.lose_ack_once:
            self.lose_ack_once.remove(operation)
            raise TimeoutError("conditional write committed but acknowledgement was lost")
        return {"result": result}

    @staticmethod
    def _operation(script: object, arguments: list[str]) -> str:
        if script == authority.ATOMIC_MUTATION_SCRIPTS["issue"]:
            return "issue"
        if script == authority.ATOMIC_MUTATION_SCRIPTS["accept"]:
            return "accept"
        if script == authority.ATOMIC_MUTATION_SCRIPTS["remove"]:
            return "remove"
        if script == authority.ATOMIC_MUTATION_SCRIPTS["update_access"]:
            return "update_access"
        if script == authority.ATOMIC_MUTATION_SCRIPTS["prune_pending"]:
            return "prune_pending"
        if script == authority.ATOMIC_MUTATION_SCRIPTS["decline"]:
            return "decline" if arguments[-1] == "declined" else "cancel"
        raise AssertionError("unknown Team mutation script")

    def _apply(self, operation: str, keys: list[str], arguments: list[str]) -> str:
        try:
            if operation == "issue":
                return self._issue(keys, arguments)
            if operation == "accept":
                return self._accept(keys, arguments)
            if operation in {"decline", "cancel"}:
                return self._terminal(keys, arguments)
            if operation == "remove":
                return self._remove_member(keys, arguments)
            if operation == "update_access":
                return self._update_access(keys, arguments)
            if operation == "prune_pending":
                return self._prune_pending(keys, arguments)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return "malformed"
        raise AssertionError(operation)

    def _issue(self, keys: list[str], arguments: list[str]) -> str:
        token_key, invitation_key, recipient_key, member_key, pending_key = keys
        record_wire, now, invitation_id, workspace_id, email = arguments
        member = self._decode(self.values.get(member_key))
        if member is not None and member.get("status") == "active":
            return "member_active"
        existing = self._decode(self.values.get(recipient_key))
        if (
            existing is not None
            and existing.get("status") == "invited"
            and int(existing.get("expiresAt")) > int(now)
        ):
            return "invite_live"
        superseded_id = None
        if (
            existing is not None
            and existing.get("status") == "invited"
            and int(existing.get("expiresAt")) <= int(now)
        ):
            if (
                not isinstance(existing.get("id"), str)
                or existing.get("workspaceId") != workspace_id
                or existing.get("inviteeEmail") != email
            ):
                return "malformed"
            superseded_id = existing["id"]
        if token_key in self.values or invitation_key in self.values:
            return "collision"
        pending = self._add(self._index(self.values.get(pending_key)), invitation_id)
        if superseded_id is not None:
            pending = self._add(
                self._remove(pending, superseded_id),
                invitation_id,
            )
        self.values[token_key] = record_wire
        self.values[invitation_key] = record_wire
        self.values[recipient_key] = record_wire
        self.values[pending_key] = self._wire(pending)
        return "applied"

    def _accept(self, keys: list[str], arguments: list[str]) -> str:
        (
            token_key,
            invitation_key,
            recipient_key,
            member_key,
            member_index,
            pending_index,
            member_user_pointer_key,
        ) = keys
        (
            expected,
            replacement,
            member_wire,
            now,
            email,
            invitation_id,
            workspace_id,
            member_user_id,
            member_user_pointer_wire,
        ) = arguments
        if self.values.get(token_key) != expected:
            return "stale"
        current = self._decode(expected)
        if current.get("status") != "invited":
            return str(current.get("status"))
        if int(current.get("expiresAt")) <= int(now):
            return "expired"
        if self.values.get(invitation_key) != expected or self.values.get(recipient_key) != expected:
            return "stale"
        current_member = self._decode(self.values.get(member_key))
        if current_member is not None and current_member.get("status") == "active":
            return "member_active"
        member_user_pointer = self._decode(member_user_pointer_wire)
        if (
            member_user_pointer.get("v") != 2
            or member_user_pointer.get("workspaceId") != workspace_id
            or member_user_pointer.get("memberUserId") != member_user_id
            or member_user_pointer.get("email") != email
            or member_user_pointer.get("sourceInvitationId") != invitation_id
            or member_user_pointer.get("status") != "active"
        ):
            return "malformed"
        existing_pointer = self._decode(self.values.get(member_user_pointer_key))
        if existing_pointer is not None:
            if (
                existing_pointer.get("v") != 2
                or existing_pointer.get("workspaceId") != workspace_id
                or existing_pointer.get("memberUserId") != member_user_id
                or not isinstance(existing_pointer.get("email"), str)
                or not isinstance(existing_pointer.get("sourceInvitationId"), str)
                or existing_pointer.get("status") != "active"
            ):
                return "malformed"
            return "member_active"
        members = self._add(self._index(self.values.get(member_index)), email)
        pending = self._remove(self._index(self.values.get(pending_index)), invitation_id)
        self.values[token_key] = replacement
        self.values[invitation_key] = replacement
        self.values[recipient_key] = replacement
        self.values[member_key] = member_wire
        self.values[member_index] = self._wire(members)
        self.values[pending_index] = self._wire(pending)
        self.values[member_user_pointer_key] = member_user_pointer_wire
        return "applied"

    def _terminal(self, keys: list[str], arguments: list[str]) -> str:
        token_key, invitation_key, recipient_key, pending_index = keys
        expected, replacement, now, invitation_id, _target = arguments
        if self.values.get(token_key) != expected:
            return "stale"
        current = self._decode(expected)
        if current.get("status") != "invited":
            return str(current.get("status"))
        if int(current.get("expiresAt")) <= int(now):
            return "expired"
        if self.values.get(invitation_key) != expected or self.values.get(recipient_key) != expected:
            return "stale"
        pending = self._remove(self._index(self.values.get(pending_index)), invitation_id)
        self.values[token_key] = replacement
        self.values[invitation_key] = replacement
        self.values[recipient_key] = replacement
        self.values[pending_index] = self._wire(pending)
        return "applied"

    def _remove_member(self, keys: list[str], arguments: list[str]) -> str:
        member_key, member_index = keys[:2]
        expected, replacement, email = arguments[:3]
        current_wire = self.values.get(member_key)
        if current_wire is None:
            return "missing"
        if current_wire != expected:
            return "stale"
        if self._decode(current_wire).get("status") != "active":
            return "not_active"
        if len(keys) == 3:
            if len(arguments) != 4 or self.values.get(keys[2]) != arguments[3]:
                return "stale"
        elif len(keys) != 2:
            return "malformed"
        members = self._remove(self._index(self.values.get(member_index)), email)
        self.values[member_key] = replacement
        self.values[member_index] = self._wire(members)
        if len(keys) == 3:
            self.values.pop(keys[2], None)
        return "applied"

    def _update_access(self, keys: list[str], arguments: list[str]) -> str:
        member_key = keys[0]
        expected, replacement = arguments
        current_wire = self.values.get(member_key)
        if current_wire is None:
            return "missing"
        if current_wire != expected:
            return "stale"
        if self._decode(current_wire).get("status") != "active":
            return "not_active"
        self.values[member_key] = replacement
        return "applied"

    def _prune_pending(self, keys: list[str], arguments: list[str]) -> str:
        pending_key = keys[0]
        expected, replacement = arguments
        current_wire = self.values.get(pending_key)
        if current_wire is None:
            return "missing"
        if current_wire != expected:
            return "stale"
        self._index(current_wire)
        replacement_index = self._index(replacement)
        self.values[pending_key] = self._wire(replacement_index)
        return "applied"


class TeamMembershipStoreTests(unittest.TestCase):
    def setUp(self):
        self.memory = MemoryTeamRedis()
        self.clock = MutableClock()
        self.runtime = authority.build_runtime_team_authority(
            {},
            command_transport=self.memory,
            now_ms=self.clock,
            random_bytes=FixedRandom(),
            inviter_owner_validator=lambda _user_id, _workspace_id: "authorized",
        )

    def issue(self, email: str = "recipient@example.test"):
        return self.runtime.issue_invitation(
            actor=owner(),
            invitee_email=email,
            invitee_name="Recipient",
            access_level="Limited",
        )

    def seed_legacy_member(self, **overrides):
        record = {
            "v": 1,
            "workspaceId": "workspace-a",
            "email": "legacy@example.test",
            "displayName": "Legacy Member",
            "accessLevel": "Limited",
            "status": "active",
            "inviteToken": "legacy-raw-invitation-bearer",
            "invitedByUserId": "legacy-owner",
            "invitedByUserName": "Legacy Owner",
            "createdAt": NOW_MS - 200,
            "acceptedAt": NOW_MS - 100,
            "updatedAt": NOW_MS - 100,
            "mailboxCredential": "must-be-discarded",
        }
        record.update(overrides)
        email = str(overrides.get("key_email") or "legacy@example.test")
        record.pop("key_email", None)
        member_key = f"cuevion:team:v1:member:workspace-a:{email}"
        index_key = "cuevion:team:v1:members-index:workspace-a"
        self.memory.values[member_key] = self.memory._wire(record)
        self.memory.values[index_key] = self.memory._wire([email])
        return member_key, index_key, record

    def accept_eval_command(
        self,
        issued: dict,
        accepted_recipient: AuthenticatedMemberContext,
        *,
        accepted_at: int,
    ) -> list[object]:
        invitation_id = issued["invite"]["id"]
        raw_token = issued["rawToken"]
        token_key = next(
            key
            for key in self.memory.values
            if key.startswith(
                f"cuevion:team:v2:invite-token:{invitation_id}:"
            )
        )
        current_wire = self.memory.values[token_key]
        invitation = json.loads(current_wire)
        accepted_invitation = {
            **invitation,
            "status": "accepted",
            "updatedAt": accepted_at,
            "acceptedAt": accepted_at,
            "acceptedByUserId": accepted_recipient.user_id,
            "acceptedByEmail": accepted_recipient.email,
        }
        membership = authority.build_membership_record(
            invitation_record=invitation,
            recipient=accepted_recipient,
            accepted_at=accepted_at,
        )
        member_user_pointer = authority._build_member_user_pointer(membership)
        self.assertIsNotNone(member_user_pointer)
        workspace_id = invitation["workspaceId"]
        email = invitation["inviteeEmail"]
        keys = [
            token_key,
            f"cuevion:team:v2:workspace-invite:{workspace_id}:{invitation_id}",
            f"cuevion:team:v2:recipient-invite:{workspace_id}:{email}",
            f"cuevion:team:v1:member:{workspace_id}:{email}",
            f"cuevion:team:v1:members-index:{workspace_id}",
            f"cuevion:team:v2:pending-index:{workspace_id}",
            (
                f"cuevion:team:v2:member-user:{workspace_id}:"
                f"{accepted_recipient.user_id}"
            ),
        ]
        return [
            "EVAL",
            authority.ATOMIC_MUTATION_SCRIPTS["accept"],
            len(keys),
            *keys,
            current_wire,
            self.memory._wire(accepted_invitation),
            self.memory._wire(membership),
            str(accepted_at),
            email,
            invitation_id,
            workspace_id,
            accepted_recipient.user_id,
            self.memory._wire(member_user_pointer),
        ]

    def terminal_eval_command(
        self,
        issued: dict,
        target: str,
        *,
        transitioned_at: int,
    ) -> list[object]:
        invitation_id = issued["invite"]["id"]
        token_key = next(
            key
            for key in self.memory.values
            if key.startswith(
                f"cuevion:team:v2:invite-token:{invitation_id}:"
            )
        )
        current_wire = self.memory.values[token_key]
        invitation = json.loads(current_wire)
        candidate = {
            **invitation,
            "status": target,
            "updatedAt": transitioned_at,
            f"{target}At": transitioned_at,
        }
        workspace_id = invitation["workspaceId"]
        email = invitation["inviteeEmail"]
        keys = [
            token_key,
            f"cuevion:team:v2:workspace-invite:{workspace_id}:{invitation_id}",
            f"cuevion:team:v2:recipient-invite:{workspace_id}:{email}",
            f"cuevion:team:v2:pending-index:{workspace_id}",
        ]
        operation = "decline" if target == "declined" else "cancel"
        return [
            "EVAL",
            authority.ATOMIC_MUTATION_SCRIPTS[operation],
            len(keys),
            *keys,
            current_wire,
            self.memory._wire(candidate),
            str(transitioned_at),
            invitation_id,
            target,
        ]

    def test_issue_and_accept_are_atomic_token_safe_and_roster_visible(self):
        issued, issue_error = self.issue()
        self.assertIsNone(issue_error)
        self.assertIsNotNone(issued)
        raw_token = issued["rawToken"]
        secret = raw_token.split(".", 1)[1]

        stored = json.dumps(self.memory.values, sort_keys=True)
        self.assertNotIn(raw_token, stored)
        self.assertNotIn(secret, stored)
        self.assertNotIn("rawToken", stored)
        self.assertEqual(issued["invite"]["status"], "invited")

        issue_eval = [command for command in self.memory.commands if command[0] == "EVAL"]
        self.assertEqual(len(issue_eval), 1)
        self.assertIs(issue_eval[0][1], authority.ATOMIC_MUTATION_SCRIPTS["issue"])
        self.assertEqual(issue_eval[0][2], 5)
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "GET"]),
            4,
            "a successful issue must read back all three records and the pending index",
        )

        self.clock.value += 1
        accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(),
            token=raw_token,
        )
        self.assertIsNone(accept_error)
        self.assertEqual(accepted["invite"]["status"], "accepted")
        self.assertEqual(accepted["member"]["status"], "active")

        member_keys = [
            key
            for key in self.memory.values
            if key.startswith("cuevion:team:v1:member:workspace-a:")
        ]
        self.assertEqual(member_keys, ["cuevion:team:v1:member:workspace-a:recipient@example.test"])
        member_record = json.loads(self.memory.values[member_keys[0]])
        self.assertEqual(member_record["v"], 2)
        self.assertEqual(member_record["memberUserId"], "user-recipient")
        self.assertNotIn("inviteToken", member_record)
        self.assertNotIn("tokenDigest", member_record)
        pointer_key = "cuevion:team:v2:member-user:workspace-a:user-recipient"
        pointer_record = json.loads(self.memory.values[pointer_key])
        self.assertEqual(pointer_record["email"], "recipient@example.test")
        self.assertEqual(pointer_record["memberUserId"], "user-recipient")
        self.assertNotIn("inviteToken", pointer_record)
        self.assertNotIn("tokenDigest", pointer_record)

        roster, roster_error = self.runtime.list_members(actor=owner())
        self.assertIsNone(roster_error)
        self.assertEqual(roster, [accepted["member"]])
        pending, pending_error = self.runtime.list_pending_invitations(actor=owner())
        self.assertIsNone(pending_error)
        self.assertEqual(pending, [])

        repeated, repeated_error = self.runtime.accept_invitation(
            actor=recipient(),
            token=raw_token,
        )
        self.assertIsNone(repeated)
        self.assertEqual(repeated_error["code"], "used_invite")
        self.assertEqual(
            len(
                [
                    command
                    for command in self.memory.commands
                    if command[0] == "EVAL"
                    and command[1] == authority.ATOMIC_MUTATION_SCRIPTS["accept"]
                ]
            ),
            1,
        )

    def test_duplicate_live_issue_has_one_winner_and_never_replays_bearer(self):
        first, first_error = self.issue()
        second, second_error = self.issue()

        self.assertIsNone(first_error)
        self.assertIsNone(second)
        self.assertEqual(second_error["code"], "live_invitation_exists")
        self.assertNotIn(first["rawToken"], json.dumps(second_error))
        token_records = [
            key for key in self.memory.values if key.startswith("cuevion:team:v2:invite-token:")
        ]
        self.assertEqual(len(token_records), 1)

    def test_invitee_name_validation_reports_invalid_request_at_byte_boundary(self):
        valid_name = "a" * 256
        valid, valid_error = self.runtime.issue_invitation(
            actor=owner(),
            invitee_email="boundary@example.test",
            invitee_name=valid_name,
            access_level="Limited",
        )
        self.assertIsNone(valid_error)
        self.assertEqual(valid["invite"]["displayName"], valid_name)
        eval_count = len(
            [command for command in self.memory.commands if command[0] == "EVAL"]
        )

        for invalid_name in ("a" * 257, "control\x00name", "line\nname"):
            with self.subTest(invalid_name=repr(invalid_name)):
                result, error = self.runtime.issue_invitation(
                    actor=owner(),
                    invitee_email="invalid-name@example.test",
                    invitee_name=invalid_name,
                    access_level="Limited",
                )
                self.assertIsNone(result)
                self.assertEqual(error["code"], "invalid_request")
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "EVAL"]),
            eval_count,
        )

    def test_reissue_atomically_replaces_expired_recipient_id_in_pending_index(self):
        first, first_error = self.issue()
        self.assertIsNone(first_error)
        self.clock.value = first["invite"]["expiresAt"]

        second, second_error = self.issue()
        self.assertIsNone(second_error)
        self.assertEqual(
            json.loads(self.memory.values["cuevion:team:v2:pending-index:workspace-a"]),
            [second["invite"]["id"]],
        )

        self.clock.value = second["invite"]["expiresAt"]
        third, third_error = self.issue()
        self.assertIsNone(third_error)
        self.assertEqual(
            json.loads(self.memory.values["cuevion:team:v2:pending-index:workspace-a"]),
            [third["invite"]["id"]],
        )

    def test_pending_read_prunes_expired_ids_and_resolves_lost_ack_by_readback(self):
        expired, expired_error = self.issue("expired@example.test")
        self.assertIsNone(expired_error)
        self.clock.value = expired["invite"]["expiresAt"]
        live, live_error = self.issue("live@example.test")
        self.assertIsNone(live_error)
        self.memory.lose_ack_once.add("prune_pending")

        pending, pending_error = self.runtime.list_pending_invitations(actor=owner())

        self.assertIsNone(pending_error)
        self.assertEqual([item["id"] for item in pending], [live["invite"]["id"]])
        self.assertEqual(
            json.loads(self.memory.values["cuevion:team:v2:pending-index:workspace-a"]),
            [live["invite"]["id"]],
        )
        prune_evals = [
            command
            for command in self.memory.commands
            if command[0] == "EVAL"
            and command[1] == authority.ATOMIC_MUTATION_SCRIPTS["prune_pending"]
        ]
        self.assertEqual(len(prune_evals), 1)

    def test_false_positive_pending_prune_ack_fails_closed_without_retry(self):
        expired, expired_error = self.issue("expired@example.test")
        self.assertIsNone(expired_error)
        self.clock.value = expired["invite"]["expiresAt"]
        self.memory.false_applied_once.add("prune_pending")

        pending, pending_error = self.runtime.list_pending_invitations(actor=owner())

        self.assertIsNone(pending)
        self.assertEqual(pending_error["code"], "team_authority_unavailable")
        self.assertEqual(
            json.loads(self.memory.values["cuevion:team:v2:pending-index:workspace-a"]),
            [expired["invite"]["id"]],
        )
        prune_evals = [
            command
            for command in self.memory.commands
            if command[0] == "EVAL"
            and command[1] == authority.ATOMIC_MUTATION_SCRIPTS["prune_pending"]
        ]
        self.assertEqual(len(prune_evals), 1)

    def test_stale_pending_cleanup_cannot_clobber_a_concurrent_issue(self):
        expired, expired_error = self.issue("expired@example.test")
        self.assertIsNone(expired_error)
        pending_key = "cuevion:team:v2:pending-index:workspace-a"
        stale_expected = self.memory.values[pending_key]
        stale_cleanup = [
            "EVAL",
            authority.ATOMIC_MUTATION_SCRIPTS["prune_pending"],
            1,
            pending_key,
            stale_expected,
            "[]",
        ]
        self.clock.value = expired["invite"]["expiresAt"]
        live, live_error = self.issue("live@example.test")
        self.assertIsNone(live_error)

        self.assertEqual(self.memory(stale_cleanup), {"result": "stale"})
        self.assertEqual(
            json.loads(self.memory.values[pending_key]),
            [expired["invite"]["id"], live["invite"]["id"]],
        )

        pending, pending_error = self.runtime.list_pending_invitations(actor=owner())
        self.assertIsNone(pending_error)
        self.assertEqual([item["id"] for item in pending], [live["invite"]["id"]])
        self.assertEqual(
            json.loads(self.memory.values[pending_key]),
            [live["invite"]["id"]],
        )

    def test_public_lookup_discloses_only_invitation_page_fields(self):
        issued, _error = self.issue()
        public, lookup_error = self.runtime.lookup_invitation(token=issued["rawToken"])
        self.assertIsNone(lookup_error)
        self.assertEqual(
            set(public),
            {"displayName", "accessLevel", "status", "expiresAt"},
        )
        serialized = json.dumps(public)
        for forbidden in ("id", "inviteeEmail", "inviterName", "workspaceId", "token"):
            self.assertNotIn(forbidden, serialized)

    def test_invitation_normalizers_are_total_for_collection_access_and_status(self):
        issued, issue_error = self.issue()
        self.assertIsNone(issue_error)
        invitation_id = issued["invite"]["id"]
        invitation_key = (
            f"cuevion:team:v2:workspace-invite:workspace-a:{invitation_id}"
        )
        baseline = json.loads(self.memory.values[invitation_key])
        eval_count = len(
            [command for command in self.memory.commands if command[0] == "EVAL"]
        )

        malformed_fields = (
            ("accessLevel", ["Limited"]),
            ("accessLevel", {"value": "Limited"}),
            ("status", ["invited"]),
            ("status", {"value": "invited"}),
            ("v", 2.0),
            ("v", True),
        )
        for field, malformed_value in malformed_fields:
            with self.subTest(field=field, malformed_type=type(malformed_value).__name__):
                candidate = {**baseline, field: malformed_value}
                self.assertIsNone(authority._normalize_invitation_record(candidate))
                self.assertIsNone(authority.project_public_invitation(candidate))
                self.assertIsNone(authority.project_pending_invitation(candidate))
                self.memory.values[invitation_key] = self.memory._wire(candidate)
                pending, pending_error = self.runtime.list_pending_invitations(
                    actor=owner()
                )
                self.assertIsNone(pending)
                self.assertEqual(
                    pending_error["code"],
                    "team_authority_unavailable",
                )
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "EVAL"]),
            eval_count,
        )

    def test_workspace_ids_remain_opaque_and_case_sensitive_in_records_and_keys(self):
        opaque_workspace_id = "wsp_AaBbCcDdEeFfGgHhIiJjKk"
        opaque_owner = owner(opaque_workspace_id)
        issued, issue_error = self.runtime.issue_invitation(
            actor=opaque_owner,
            invitee_email="case@example.test",
            invitee_name="Case Recipient",
            access_level="Shared",
        )
        self.assertIsNone(issue_error)
        invitation_records = [
            json.loads(value)
            for key, value in self.memory.values.items()
            if key.startswith("cuevion:team:v2:workspace-invite:")
            and opaque_workspace_id in key
        ]
        self.assertEqual(len(invitation_records), 1)
        self.assertEqual(invitation_records[0]["workspaceId"], opaque_workspace_id)
        self.assertFalse(any(opaque_workspace_id.lower() in key for key in self.memory.values))

        self.clock.value += 1
        accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient("case@example.test"),
            token=issued["rawToken"],
        )
        self.assertIsNone(accept_error)
        self.assertEqual(accepted["member"]["status"], "active")
        exact_member_key = (
            "cuevion:team:v1:member:"
            f"{opaque_workspace_id}:case@example.test"
        )
        self.assertIn(exact_member_key, self.memory.values)
        roster, roster_error = self.runtime.list_members(actor=opaque_owner)
        self.assertIsNone(roster_error)
        self.assertEqual(roster, [accepted["member"]])

    def test_decline_and_cancel_are_terminal_and_recipient_bound(self):
        issued, _error = self.issue()
        raw_token = issued["rawToken"]

        wrong, wrong_error = self.runtime.decline_invitation(
            actor=recipient("attacker@example.test"),
            token=raw_token,
        )
        self.assertIsNone(wrong)
        self.assertEqual(wrong_error["code"], "wrong_recipient")

        declined, decline_error = self.runtime.decline_invitation(
            actor=recipient(),
            token=raw_token,
        )
        self.assertIsNone(decline_error)
        self.assertEqual(declined["invite"]["status"], "declined")
        accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(),
            token=raw_token,
        )
        self.assertIsNone(accepted)
        self.assertEqual(accept_error["code"], "declined_invite")

        issued_two, _error = self.issue("second@example.test")
        invitation_id = issued_two["invite"]["id"]
        cancelled, cancel_error = self.runtime.cancel_invitation(
            actor=owner(),
            invitation_id=invitation_id,
        )
        self.assertIsNone(cancel_error)
        self.assertEqual(cancelled["invite"]["status"], "cancelled")
        cancelled_accept, cancelled_accept_error = self.runtime.accept_invitation(
            actor=recipient("second@example.test"),
            token=issued_two["rawToken"],
        )
        self.assertIsNone(cancelled_accept)
        self.assertEqual(cancelled_accept_error["code"], "cancelled_invite")

    def test_access_update_and_remove_use_existing_authoritative_roster_keys(self):
        issued, _error = self.issue()
        self.clock.value += 1
        _accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(), token=issued["rawToken"]
        )
        self.assertIsNone(accept_error)
        pointer_key = "cuevion:team:v2:member-user:workspace-a:user-recipient"
        pointer_wire = self.memory.values[pointer_key]

        self.clock.value += 1
        updated, update_error = self.runtime.update_member_access(
            actor=owner(),
            member_email="recipient@example.test",
            access_level="Shared",
        )
        self.assertIsNone(update_error)
        self.assertEqual(updated["accessLevel"], "Shared")
        self.assertEqual(self.memory.values[pointer_key], pointer_wire)

        self.clock.value += 1
        self.memory.lose_ack_once.add("remove")
        removed, remove_error = self.runtime.remove_member(
            actor=owner(),
            member_email="recipient@example.test",
        )
        self.assertIsNone(remove_error)
        self.assertEqual(removed["status"], "removed")
        self.assertNotIn(pointer_key, self.memory.values)
        remove_evals = [
            command
            for command in self.memory.commands
            if command[0] == "EVAL"
            and command[1] == authority.ATOMIC_MUTATION_SCRIPTS["remove"]
        ]
        self.assertEqual(len(remove_evals), 1)
        roster, roster_error = self.runtime.list_members(actor=owner())
        self.assertIsNone(roster_error)
        self.assertEqual(roster, [])

        again, again_error = self.runtime.remove_member(
            actor=owner(),
            member_email="recipient@example.test",
        )
        self.assertIsNone(again)
        self.assertEqual(again_error["code"], "team_member_not_active")

    def test_visible_legacy_v1_member_access_update_is_atomic_minimal_and_redacted(self):
        member_key, _index_key, original = self.seed_legacy_member()
        self.assertEqual(
            team_members._normalize_member_record(
                original,
                "workspace-a",
                "legacy@example.test",
            ),
            {
                "email": "legacy@example.test",
                "displayName": "Legacy Member",
                "accessLevel": "Limited",
                "status": "active",
            },
        )

        updated, error = self.runtime.update_member_access(
            actor=owner(),
            member_email="legacy@example.test",
            access_level="Shared",
        )

        self.assertIsNone(error)
        self.assertEqual(
            updated,
            {
                "email": "legacy@example.test",
                "displayName": "Legacy Member",
                "accessLevel": "Shared",
                "status": "active",
            },
        )
        self.assertNotIn("token", json.dumps(updated).lower())
        stored = json.loads(self.memory.values[member_key])
        self.assertEqual(
            set(stored),
            {
                "v",
                "workspaceId",
                "email",
                "displayName",
                "accessLevel",
                "status",
                "inviteToken",
                "createdAt",
                "updatedAt",
                "acceptedAt",
            },
        )
        self.assertEqual(stored["inviteToken"], original["inviteToken"])
        self.assertEqual(stored["createdAt"], original["createdAt"])
        self.assertEqual(stored["acceptedAt"], original["acceptedAt"])
        self.assertEqual(stored["updatedAt"], self.clock.value)
        self.assertEqual(
            team_members._normalize_member_record(
                stored,
                "workspace-a",
                "legacy@example.test",
            ),
            updated,
        )

    def test_visible_legacy_v1_member_revoke_strips_bearer_and_sensitive_extras(self):
        member_key, index_key, original = self.seed_legacy_member()
        self.assertIsNotNone(
            team_members._normalize_member_record(
                original,
                "workspace-a",
                "legacy@example.test",
            )
        )

        removed, error = self.runtime.remove_member(
            actor=owner(),
            member_email="legacy@example.test",
        )

        self.assertIsNone(error)
        self.assertEqual(
            removed,
            {
                "email": "legacy@example.test",
                "status": "removed",
                "removedAt": self.clock.value,
            },
        )
        stored = json.loads(self.memory.values[member_key])
        self.assertNotIn("inviteToken", stored)
        self.assertNotIn("mailboxCredential", stored)
        self.assertNotIn("invitedByUserId", stored)
        self.assertNotIn("invitedByUserName", stored)
        self.assertEqual(stored["accessLevel"], "Limited")
        self.assertEqual(stored["status"], "removed")
        self.assertEqual(stored["createdAt"], original["createdAt"])
        self.assertEqual(stored["acceptedAt"], original["acceptedAt"])
        self.assertEqual(stored["updatedAt"], self.clock.value)
        self.assertEqual(stored["removedAt"], self.clock.value)
        self.assertEqual(stored["revokedAt"], self.clock.value)
        self.assertEqual(json.loads(self.memory.values[index_key]), [])
        self.assertIsNone(
            team_members._normalize_member_record(
                stored,
                "workspace-a",
                "legacy@example.test",
            )
        )

    def test_legacy_v1_malformed_and_cross_tenant_records_fail_closed(self):
        invalid_variants = {
            "malformed_timestamp": {"acceptedAt": "not-a-timestamp"},
            "cross_workspace": {"workspaceId": "workspace-b"},
            "cross_email": {
                "email": "other@example.test",
                "key_email": "legacy@example.test",
            },
        }
        for variant, overrides in invalid_variants.items():
            for operation in ("update", "remove"):
                with self.subTest(variant=variant, operation=operation):
                    self.setUp()
                    self.seed_legacy_member(**overrides)
                    before = dict(self.memory.values)
                    if operation == "update":
                        result, error = self.runtime.update_member_access(
                            actor=owner(),
                            member_email="legacy@example.test",
                            access_level="Shared",
                        )
                    else:
                        result, error = self.runtime.remove_member(
                            actor=owner(),
                            member_email="legacy@example.test",
                        )
                    self.assertIsNone(result)
                    self.assertEqual(error["code"], "team_member_not_found")
                    self.assertEqual(self.memory.values, before)
                    self.assertFalse(
                        any(command[0] == "EVAL" for command in self.memory.commands)
                    )

    def test_member_normalizers_are_total_for_collection_access_and_status(self):
        issued, issue_error = self.issue()
        self.assertIsNone(issue_error)
        self.clock.value += 1
        _accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(),
            token=issued["rawToken"],
        )
        self.assertIsNone(accept_error)
        member_key = (
            "cuevion:team:v1:member:workspace-a:recipient@example.test"
        )
        baseline = json.loads(self.memory.values[member_key])
        eval_count = len(
            [command for command in self.memory.commands if command[0] == "EVAL"]
        )
        malformed_fields = (
            ("accessLevel", ["Limited"]),
            ("accessLevel", {"value": "Limited"}),
            ("status", ["active"]),
            ("status", {"value": "active"}),
            ("v", 2.0),
            ("v", True),
        )

        for field, malformed_value in malformed_fields:
            with self.subTest(
                schema="v2",
                field=field,
                malformed_type=type(malformed_value).__name__,
            ):
                candidate = {**baseline, field: malformed_value}
                self.assertIsNone(authority._normalize_membership_record(candidate))
                self.assertIsNone(authority.project_team_member(candidate))
                self.assertIsNone(
                    team_members._normalize_member_record(
                        candidate,
                        "workspace-a",
                        "recipient@example.test",
                    )
                )
                self.memory.values[member_key] = self.memory._wire(candidate)
                updated, update_error = self.runtime.update_member_access(
                    actor=owner(),
                    member_email="recipient@example.test",
                    access_level="Shared",
                )
                self.assertIsNone(updated)
                self.assertEqual(update_error["code"], "team_member_not_found")

            with self.subTest(
                schema="v1",
                field=field,
                malformed_type=type(malformed_value).__name__,
            ):
                legacy = {
                    "v": 1,
                    "workspaceId": "workspace-a",
                    "email": "legacy@example.test",
                    "displayName": "Legacy Member",
                    "accessLevel": "Limited",
                    "status": "active",
                    "inviteToken": "legacy-bearer",
                    "createdAt": NOW_MS - 200,
                    "acceptedAt": NOW_MS - 100,
                    "updatedAt": NOW_MS - 100,
                    field: malformed_value,
                }
                self.assertIsNone(authority._normalize_legacy_membership_record(legacy))
                self.assertIsNone(authority.project_team_member(legacy))
                self.assertIsNone(
                    team_members._normalize_member_record(
                        legacy,
                        "workspace-a",
                        "legacy@example.test",
                    )
                )
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "EVAL"]),
            eval_count,
        )

    def test_historical_v1_long_control_name_remains_visible_and_manageable(self):
        historical_name = ("Legacy" * 60) + "\x00Control"
        member_key, _index_key, original = self.seed_legacy_member(
            displayName=historical_name
        )

        visible = team_members._normalize_member_record(
            original,
            "workspace-a",
            "legacy@example.test",
        )
        self.assertEqual(visible["displayName"], historical_name)
        encoded_projection = authority._canonical_json(visible)
        self.assertNotIn("\x00", encoded_projection)
        self.assertIn("\\u0000", encoded_projection)

        updated, update_error = self.runtime.update_member_access(
            actor=owner(),
            member_email="legacy@example.test",
            access_level="Shared",
        )
        self.assertIsNone(update_error)
        self.assertEqual(updated["displayName"], historical_name)
        self.assertEqual(
            team_members._normalize_member_record(
                json.loads(self.memory.values[member_key]),
                "workspace-a",
                "legacy@example.test",
            ),
            updated,
        )

        removed, remove_error = self.runtime.remove_member(
            actor=owner(),
            member_email="legacy@example.test",
        )
        self.assertIsNone(remove_error)
        self.assertEqual(removed["status"], "removed")
        terminal_record = json.loads(self.memory.values[member_key])
        self.assertEqual(terminal_record["displayName"], historical_name)
        self.assertNotIn("inviteToken", terminal_record)

    def test_historical_v1_non_email_identifier_remains_visible_and_manageable(self):
        identifier = "legacy-handle"
        member_key, index_key, original = self.seed_legacy_member(
            email="Legacy-Handle",
            key_email=identifier,
        )

        visible = team_members._normalize_member_record(
            original,
            "workspace-a",
            identifier,
        )
        self.assertEqual(
            visible,
            {
                "email": identifier,
                "displayName": "Legacy Member",
                "accessLevel": "Limited",
                "status": "active",
            },
        )

        updated, update_error = self.runtime.update_member_access(
            actor=owner(),
            member_email=" LEGACY-HANDLE ",
            access_level="Shared",
        )
        self.assertIsNone(update_error)
        self.assertEqual(updated["email"], identifier)
        self.assertEqual(updated["accessLevel"], "Shared")
        stored = json.loads(self.memory.values[member_key])
        self.assertEqual(stored["email"], identifier)
        self.assertEqual(
            team_members._normalize_member_record(
                stored,
                "workspace-a",
                identifier,
            ),
            updated,
        )

        removed, remove_error = self.runtime.remove_member(
            actor=owner(),
            member_email="LEGACY-HANDLE",
        )
        self.assertIsNone(remove_error)
        self.assertEqual(removed["email"], identifier)
        self.assertEqual(removed["status"], "removed")
        self.assertEqual(json.loads(self.memory.values[index_key]), [])
        terminal = json.loads(self.memory.values[member_key])
        self.assertEqual(terminal["email"], identifier)
        self.assertNotIn("inviteToken", terminal)

    def test_invalid_email_target_cannot_mutate_a_non_v1_record(self):
        identifier = "legacy-handle"
        member_key = f"cuevion:team:v1:member:workspace-a:{identifier}"
        invalid_v2 = {
            "v": 2,
            "workspaceId": "workspace-a",
            "email": identifier,
            "verifiedRecipientEmail": identifier,
            "memberUserId": "user-recipient",
            "displayName": "Not A Valid V2 Member",
            "accessLevel": "Limited",
            "status": "active",
            "sourceInvitationId": "tinv_test",
            "createdAt": NOW_MS - 200,
            "acceptedAt": NOW_MS - 100,
            "updatedAt": NOW_MS - 100,
        }
        self.memory.values[member_key] = self.memory._wire(invalid_v2)
        self.memory.values["cuevion:team:v1:members-index:workspace-a"] = (
            self.memory._wire([identifier])
        )
        before = dict(self.memory.values)

        for operation in ("update", "remove"):
            with self.subTest(operation=operation):
                if operation == "update":
                    result, error = self.runtime.update_member_access(
                        actor=owner(),
                        member_email=identifier,
                        access_level="Shared",
                    )
                else:
                    result, error = self.runtime.remove_member(
                        actor=owner(),
                        member_email=identifier,
                    )
                self.assertIsNone(result)
                self.assertEqual(error["code"], "team_member_not_found")
                self.assertEqual(self.memory.values, before)
        self.assertFalse(any(command[0] == "EVAL" for command in self.memory.commands))

    def test_lost_ack_is_resolved_by_exact_readback_without_retry(self):
        self.memory.lose_ack_once.add("issue")
        issued, error = self.issue()
        self.assertIsNone(error)
        self.assertIsNotNone(issued)
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "EVAL"]),
            1,
        )

        self.memory.lose_ack_once.add("accept")
        self.clock.value += 1
        accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(), token=issued["rawToken"]
        )
        self.assertIsNone(accept_error)
        self.assertEqual(accepted["member"]["status"], "active")
        accept_evals = [
            command
            for command in self.memory.commands
            if command[0] == "EVAL"
            and command[1] == authority.ATOMIC_MUTATION_SCRIPTS["accept"]
        ]
        self.assertEqual(len(accept_evals), 1)

    def test_false_positive_ack_fails_closed_when_exact_readback_disagrees(self):
        self.memory.false_applied_once.add("issue")
        issued, error = self.issue()
        self.assertIsNone(issued)
        self.assertEqual(error["code"], "team_authority_unavailable")
        self.assertEqual(self.memory.values, {})

    def test_stale_and_malformed_transition_results_fail_closed_without_writes(self):
        issued, _error = self.issue()
        raw_token = issued["rawToken"]

        self.memory.forced_result_once["accept"] = "stale"
        accepted, accept_error = self.runtime.accept_invitation(
            actor=recipient(), token=raw_token
        )
        self.assertIsNone(accepted)
        self.assertEqual(accept_error["code"], "team_authority_unavailable")

        self.memory.forced_result_once["decline"] = "malformed"
        declined, decline_error = self.runtime.decline_invitation(
            actor=recipient(), token=raw_token
        )
        self.assertIsNone(declined)
        self.assertEqual(decline_error["code"], "team_authority_unavailable")
        self.assertFalse(
            any(key.startswith("cuevion:team:v1:member:") for key in self.memory.values)
        )
        token_record = json.loads(
            next(
                value
                for key, value in self.memory.values.items()
                if key.startswith("cuevion:team:v2:invite-token:")
            )
        )
        self.assertEqual(token_record["status"], "invited")

    def test_accept_vs_cancel_has_one_eval_cas_winner_in_either_order(self):
        issued_accept_first, _error = self.issue()
        accepted_at = self.clock.value + 1
        accept_command = self.accept_eval_command(
            issued_accept_first,
            recipient(),
            accepted_at=accepted_at,
        )
        cancel_command = self.terminal_eval_command(
            issued_accept_first,
            "cancelled",
            transitioned_at=accepted_at,
        )

        self.assertEqual(self.memory(accept_command), {"result": "applied"})
        self.assertEqual(self.memory(cancel_command), {"result": "stale"})
        accepted_token_key = str(accept_command[3])
        self.assertEqual(
            json.loads(self.memory.values[accepted_token_key])["status"],
            "accepted",
        )

        issued_cancel_first, _error = self.issue("cancel-first@example.test")
        cancel_first_recipient = recipient("cancel-first@example.test")
        accept_command = self.accept_eval_command(
            issued_cancel_first,
            cancel_first_recipient,
            accepted_at=accepted_at,
        )
        cancel_command = self.terminal_eval_command(
            issued_cancel_first,
            "cancelled",
            transitioned_at=accepted_at,
        )
        self.assertEqual(self.memory(cancel_command), {"result": "applied"})
        self.assertEqual(self.memory(accept_command), {"result": "stale"})
        cancelled_token_key = str(cancel_command[3])
        self.assertEqual(
            json.loads(self.memory.values[cancelled_token_key])["status"],
            "cancelled",
        )

    def test_accept_vs_decline_has_one_eval_cas_winner_in_either_order(self):
        issued_accept_first, _error = self.issue()
        transitioned_at = self.clock.value + 1
        accept_command = self.accept_eval_command(
            issued_accept_first,
            recipient(),
            accepted_at=transitioned_at,
        )
        decline_command = self.terminal_eval_command(
            issued_accept_first,
            "declined",
            transitioned_at=transitioned_at,
        )
        self.assertEqual(self.memory(accept_command), {"result": "applied"})
        self.assertEqual(self.memory(decline_command), {"result": "stale"})

        issued_decline_first, _error = self.issue("decline-first@example.test")
        accept_command = self.accept_eval_command(
            issued_decline_first,
            recipient("decline-first@example.test"),
            accepted_at=transitioned_at,
        )
        decline_command = self.terminal_eval_command(
            issued_decline_first,
            "declined",
            transitioned_at=transitioned_at,
        )
        self.assertEqual(self.memory(decline_command), {"result": "applied"})
        self.assertEqual(self.memory(accept_command), {"result": "stale"})

    def test_double_accept_eval_cas_creates_one_membership(self):
        issued, _error = self.issue()
        accept_command = self.accept_eval_command(
            issued,
            recipient(),
            accepted_at=self.clock.value + 1,
        )
        competing_command = list(accept_command)

        self.assertEqual(self.memory(accept_command), {"result": "applied"})
        self.assertEqual(self.memory(competing_command), {"result": "stale"})
        member_index = json.loads(
            self.memory.values["cuevion:team:v1:members-index:workspace-a"]
        )
        self.assertEqual(member_index, ["recipient@example.test"])
        member_keys = [
            key
            for key in self.memory.values
            if key.startswith("cuevion:team:v1:member:workspace-a:")
        ]
        self.assertEqual(len(member_keys), 1)

    def test_distinct_accept_eval_commands_retain_both_member_index_entries(self):
        first, _error = self.issue("first@example.test")
        second, _error = self.issue("second@example.test")
        transitioned_at = self.clock.value + 1

        first_command = self.accept_eval_command(
            first,
            recipient("first@example.test", user_id="user-first"),
            accepted_at=transitioned_at,
        )
        second_command = self.accept_eval_command(
            second,
            recipient("second@example.test", user_id="user-second"),
            accepted_at=transitioned_at,
        )
        self.assertEqual(self.memory(first_command), {"result": "applied"})
        self.assertEqual(self.memory(second_command), {"result": "applied"})

        member_index = json.loads(
            self.memory.values["cuevion:team:v1:members-index:workspace-a"]
        )
        self.assertEqual(
            member_index,
            ["first@example.test", "second@example.test"],
        )
        first_member = json.loads(
            self.memory.values[
                "cuevion:team:v1:member:workspace-a:first@example.test"
            ]
        )
        second_member = json.loads(
            self.memory.values[
                "cuevion:team:v1:member:workspace-a:second@example.test"
            ]
        )
        self.assertEqual(first_member["memberUserId"], "user-first")
        self.assertEqual(second_member["memberUserId"], "user-second")

    def test_distinct_email_accepts_for_same_user_have_one_pointer_cas_winner(self):
        first, _error = self.issue("first@example.test")
        second, _error = self.issue("second@example.test")
        transitioned_at = self.clock.value + 1
        same_user_id = "user-same-recipient"
        first_command = self.accept_eval_command(
            first,
            recipient("first@example.test", user_id=same_user_id),
            accepted_at=transitioned_at,
        )
        second_command = self.accept_eval_command(
            second,
            recipient("second@example.test", user_id=same_user_id),
            accepted_at=transitioned_at,
        )

        self.assertEqual(self.memory(first_command), {"result": "applied"})
        self.assertEqual(self.memory(second_command), {"result": "member_active"})
        self.assertEqual(
            json.loads(self.memory.values["cuevion:team:v1:members-index:workspace-a"]),
            ["first@example.test"],
        )
        pointer_key = f"cuevion:team:v2:member-user:workspace-a:{same_user_id}"
        self.assertEqual(
            json.loads(self.memory.values[pointer_key])["email"],
            "first@example.test",
        )
        second_token_key = str(second_command[3])
        self.assertEqual(
            json.loads(self.memory.values[second_token_key])["status"],
            "invited",
        )
        self.assertIn(
            second["invite"]["id"],
            json.loads(self.memory.values["cuevion:team:v2:pending-index:workspace-a"]),
        )

    def test_stored_digest_is_not_a_bearer_and_expiration_is_enforced(self):
        issued, _error = self.issue()
        raw_token = issued["rawToken"]
        invitation_id = issued["invite"]["id"]
        token_key = next(
            key for key in self.memory.values if key.startswith("cuevion:team:v2:invite-token:")
        )
        token_digest = token_key.rsplit(":", 1)[1]

        leaked_digest_result, leaked_digest_error = self.runtime.lookup_invitation(
            token=f"{invitation_id}.{token_digest}"
        )
        self.assertIsNone(leaked_digest_result)
        self.assertEqual(leaked_digest_error["code"], "invalid_invite")

        self.clock.value = NOW_MS + authority.TEAM_INVITE_TTL_MS
        expired, expired_error = self.runtime.accept_invitation(
            actor=recipient(), token=raw_token
        )
        self.assertIsNone(expired)
        self.assertEqual(expired_error["code"], "expired_invite")

    def test_accept_revalidates_inviter_owner_before_atomic_transition(self):
        issued, _error = self.issue()
        denied_runtime = authority.build_runtime_team_authority(
            {},
            command_transport=self.memory,
            now_ms=self.clock,
            random_bytes=FixedRandom(),
            inviter_owner_validator=lambda _user_id, _workspace_id: "not_authorized",
        )
        before_evals = len([command for command in self.memory.commands if command[0] == "EVAL"])
        accepted, error = denied_runtime.accept_invitation(
            actor=recipient(), token=issued["rawToken"]
        )
        self.assertIsNone(accepted)
        self.assertEqual(error["code"], "cancelled_invite")
        after_evals = len([command for command in self.memory.commands if command[0] == "EVAL"])
        self.assertEqual(after_evals, before_evals)

    def test_inviter_user_cannot_self_accept_through_an_alias_email(self):
        alias_email = "owner+alias@example.test"
        issued, issue_error = self.runtime.issue_invitation(
            actor=owner(),
            invitee_email=alias_email,
            invitee_name="Owner Alias",
            access_level="Shared",
        )
        self.assertIsNone(issue_error)
        owner_validation_calls: list[tuple[str, str]] = []

        def must_not_revalidate(user_id: str, workspace_id: str):
            owner_validation_calls.append((user_id, workspace_id))
            raise AssertionError("self-accept must stop before owner revalidation")

        rejecting_runtime = authority.build_runtime_team_authority(
            {},
            command_transport=self.memory,
            now_ms=self.clock,
            random_bytes=FixedRandom(),
            inviter_owner_validator=must_not_revalidate,
        )
        before_evals = len(
            [command for command in self.memory.commands if command[0] == "EVAL"]
        )
        alias_actor = AuthenticatedMemberContext(
            user_id="user-owner",
            email=alias_email,
            name="Owner Alias",
            workspace_id="owner-other-context",
            membership_role="member",
        )

        accepted, error = rejecting_runtime.accept_invitation(
            actor=alias_actor,
            token=issued["rawToken"],
        )

        self.assertIsNone(accepted)
        self.assertEqual(error["code"], "wrong_recipient")
        self.assertEqual(owner_validation_calls, [])
        self.assertEqual(
            len([command for command in self.memory.commands if command[0] == "EVAL"]),
            before_evals,
        )
        self.assertFalse(
            any(key.startswith("cuevion:team:v1:member:") for key in self.memory.values)
        )


class RuntimeTransportTests(unittest.TestCase):
    def test_upstash_atomic_command_posts_eval_to_rest_root(self):
        captured: dict[str, object] = {}

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self, _limit: int):
                return b'{"result":"applied"}'

        def fake_urlopen(request, *, timeout):
            captured["url"] = request.full_url
            captured["method"] = request.get_method()
            captured["body"] = json.loads(request.data.decode("utf-8"))
            captured["timeout"] = timeout
            return Response()

        with patch.object(authority, "urlopen", side_effect=fake_urlopen):
            runtime = authority.build_runtime_team_authority(
                {
                    "KV_REST_API_URL": "https://redis.example.test/",
                    "KV_REST_API_TOKEN": "test-rest-token",
                }
            )
            result, error = runtime._atomic(
                "update_access",
                ["member-key"],
                ["expected-record", "replacement-record"],
            )

        self.assertIsNone(error)
        self.assertEqual(result, "applied")
        self.assertEqual(captured["url"], "https://redis.example.test")
        self.assertEqual(captured["method"], "POST")
        self.assertEqual(captured["body"][0], "EVAL")
        self.assertEqual(captured["body"][2], 1)
        self.assertEqual(captured["body"][3], "member-key")
        self.assertNotIn("/set/", str(captured["url"]))

    def test_lua_indices_preserve_json_array_shape_when_the_last_item_is_removed(self):
        for operation in ("accept", "decline", "cancel", "remove", "prune_pending"):
            with self.subTest(operation=operation):
                script = authority.ATOMIC_MUTATION_SCRIPTS[operation]
                self.assertIn("encode_index", script)
                self.assertIn("return '[]'", script)
                self.assertNotIn("cjson.encode(pending)", script)
                self.assertNotIn("cjson.encode(members)", script)


if __name__ == "__main__":
    unittest.main()
