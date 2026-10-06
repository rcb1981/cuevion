from __future__ import annotations

import json
import unittest

from api.auth.runtime import AuthenticatedMemberContext
from cuevion_tester import authority


NOW_MS = 1_800_000_000_000
ADMIN_USER_ID = "usr_" + "A" * 21 + "A"
ADMIN_WORKSPACE_ID = "wsp_" + "B" * 21 + "A"
PROVISIONED_USER_ID = "usr_" + "C" * 21 + "A"
PROVISIONED_WORKSPACE_ID = "wsp_" + "D" * 21 + "A"
ADMIN = AuthenticatedMemberContext(
    user_id=ADMIN_USER_ID,
    email="admin@example.com",
    name="Admin",
    workspace_id=ADMIN_WORKSPACE_ID,
    membership_role="owner",
)


class MemoryTransport:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.commands: list[list[object]] = []

    def __call__(self, command: list[object]) -> dict[str, object]:
        self.commands.append(command)
        operation = command[0]

        if operation != "EVAL":
            raise AssertionError(operation)

        script = command[1]
        key_count = int(command[2])
        keys = [str(value) for value in command[3 : 3 + key_count]]
        args = command[3 + key_count :]

        if script == "return redis.call('GET', KEYS[1])":
            return {"result": self.values.get(keys[0])}

        if script == authority._SNAPSHOT_LUA:
            return {"result": [self.values.get(key, False) for key in keys]}

        if script == authority.ATOMIC_MUTATION_SCRIPTS["issue"]:
            current_raw = self.values.get(keys[2])
            if current_raw is not None:
                current = json.loads(current_raw)
                if current["status"] == "invited" and current["expiresAt"] > int(args[1]):
                    return {"result": "invite_live"}
                if current["status"] == "provisioned":
                    return {"result": "account_provisioned"}
            if keys[0] in self.values or keys[1] in self.values:
                return {"result": "collision"}
            for key in keys:
                self.values[key] = str(args[0])
            return {"result": "applied"}

        if script == authority.ATOMIC_MUTATION_SCRIPTS["transition"]:
            expected, replacement, now_ms = str(args[0]), str(args[1]), int(args[2])
            if any(self.values.get(key) != expected for key in keys):
                return {"result": "stale"}
            current = json.loads(expected)
            if current["status"] != "invited":
                return {"result": current["status"]}
            if current["expiresAt"] <= now_ms:
                return {"result": "expired"}
            for key in keys:
                self.values[key] = replacement
            return {"result": "applied"}

        raise AssertionError("unknown script")


class DeterministicRandom:
    def __init__(self) -> None:
        self.counter = 0

    def __call__(self, length: int) -> bytes:
        if length not in {
            authority.TESTER_INVITE_ID_BYTES,
            authority.TESTER_INVITE_TOKEN_BYTES,
        }:
            raise AssertionError(length)
        self.counter += 1
        return bytes([self.counter % 251]) * length


class TesterInviteAuthorityTests(unittest.TestCase):
    def build(self, transport: MemoryTransport, *, admin=True):
        return authority.RuntimeTesterInviteAuthority(
            transport,
            now_ms=lambda: NOW_MS,
            random_bytes=DeterministicRandom(),
            admin_validator=lambda actor: admin and actor.user_id == ADMIN_USER_ID,
        )

    def test_token_namespace_digest_and_redaction(self):
        random_bytes = DeterministicRandom()
        invitation_id = authority.generate_invitation_id(random_bytes=random_bytes)
        raw_token, digest = authority.generate_invitation_token(
            invitation_id,
            random_bytes=random_bytes,
        )
        self.assertTrue(invitation_id.startswith("tsti_"))
        self.assertRegex(raw_token, r"^tsti_[A-Za-z0-9_-]{1,64}\.[A-Za-z0-9_-]{43}$")
        self.assertRegex(digest, r"^[0-9a-f]{64}$")
        self.assertTrue(authority.verify_invitation_token(raw_token, digest))
        self.assertFalse(
            authority.verify_invitation_token(
                raw_token.replace(".", ".X", 1),
                digest,
            )
        )
        projected = authority.ProvisioningTesterInvite(
            invitation_id,
            "tester@example.com",
            "Tester",
            digest,
            "invited",
            NOW_MS + authority.TESTER_INVITE_TTL_MS,
            "direct",
        )
        self.assertNotIn(digest, repr(projected))
        self.assertNotIn(raw_token, repr(projected))

    def test_issue_requires_global_admin_and_never_stores_raw_bearer(self):
        transport = MemoryTransport()
        result, error = self.build(transport).issue_invitation(
            actor=ADMIN,
            invitee_email=" Tester@Example.com ",
            invitee_name=" Tester ",
        )
        self.assertIsNone(error)
        self.assertIsNotNone(result)
        raw_token = result["rawToken"]
        invite = result["invite"]
        self.assertEqual(invite["inviteeEmail"], "tester@example.com")
        self.assertEqual(invite["status"], "invited")
        self.assertEqual(invite["source"], "direct")
        self.assertNotIn("tokenDigest", invite)
        stored = "\n".join(transport.values.values())
        self.assertNotIn(raw_token, stored)
        self.assertNotIn("rawToken", stored)

        denied, denied_error = self.build(
            MemoryTransport(),
            admin=False,
        ).issue_invitation(
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        self.assertIsNone(denied)
        self.assertEqual(denied_error["code"], "forbidden")

    def test_direct_and_early_access_provenance_are_closed(self):
        random_bytes = DeterministicRandom()
        invitation_id = authority.generate_invitation_id(random_bytes=random_bytes)
        _raw, digest = authority.generate_invitation_token(
            invitation_id,
            random_bytes=random_bytes,
        )
        direct = authority.build_invitation_record(
            invitation_id=invitation_id,
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
            token_digest=digest,
            now_ms=NOW_MS,
            source="direct",
        )
        self.assertNotIn("sourceRequestId", direct)

        early = authority.build_invitation_record(
            invitation_id=invitation_id,
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
            token_digest=digest,
            now_ms=NOW_MS,
            source="early_access",
            source_request_id="request-001",
        )
        self.assertEqual(early["sourceRequestId"], "request-001")

        with self.assertRaises(ValueError):
            authority.build_invitation_record(
                invitation_id=invitation_id,
                actor=ADMIN,
                invitee_email="tester@example.com",
                invitee_name="Tester",
                token_digest=digest,
                now_ms=NOW_MS,
                source="direct",
                source_request_id="request-001",
            )

    def test_second_live_invite_for_same_email_is_rejected(self):
        transport = MemoryTransport()
        service = self.build(transport)
        first, first_error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        self.assertIsNone(first_error)
        second, second_error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester Again",
        )
        self.assertIsNone(second)
        self.assertEqual(second_error["code"], "live_invitation_exists")
        self.assertIsNotNone(first)

    def test_lookup_public_projection_excludes_authority_and_email(self):
        transport = MemoryTransport()
        service = self.build(transport)
        issued, error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        self.assertIsNone(error)
        public, public_error = service.lookup_invitation(token=issued["rawToken"])
        self.assertIsNone(public_error)
        self.assertEqual(
            public,
            {
                "displayName": "Tester",
                "status": "invited",
                "expiresAt": NOW_MS + authority.TESTER_INVITE_TTL_MS,
            },
        )
        serialized = json.dumps(public)
        self.assertNotIn("tester@example.com", serialized)
        self.assertNotIn(ADMIN_USER_ID, serialized)
        self.assertNotIn("token", serialized.lower())

    def test_cancel_and_provision_are_exact_atomic_terminal_transitions(self):
        transport = MemoryTransport()
        service = self.build(transport)

        cancelled_issue, error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="cancel@example.com",
            invitee_name="Cancel",
        )
        self.assertIsNone(error)
        cancelled, cancel_error = service.cancel_invitation(
            actor=ADMIN,
            invitation_id=cancelled_issue["invite"]["id"],
        )
        self.assertIsNone(cancel_error)
        self.assertEqual(cancelled["status"], "cancelled")
        with self.assertRaises(authority.TesterInviteAuthorityError) as cancelled_read:
            service.read_provisioning_invitation(cancelled_issue["rawToken"])
        self.assertEqual(cancelled_read.exception.code, "cancelled_invite")

        provision_issue, error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="owner@example.com",
            invitee_name="Owner",
        )
        self.assertIsNone(error)
        provisioned = service.mark_provisioned(
            token=provision_issue["rawToken"],
            user_id=PROVISIONED_USER_ID,
            workspace_id=PROVISIONED_WORKSPACE_ID,
        )
        self.assertEqual(provisioned.status, "provisioned")
        self.assertEqual(provisioned.provisioned_user_id, PROVISIONED_USER_ID)
        self.assertEqual(
            provisioned.provisioned_workspace_id,
            PROVISIONED_WORKSPACE_ID,
        )
        replay = service.mark_provisioned(
            token=provision_issue["rawToken"],
            user_id=PROVISIONED_USER_ID,
            workspace_id=PROVISIONED_WORKSPACE_ID,
        )
        self.assertEqual(replay, provisioned)
        with self.assertRaises(authority.TesterInviteAuthorityError) as mismatch:
            service.mark_provisioned(
                token=provision_issue["rawToken"],
                user_id=PROVISIONED_USER_ID,
                workspace_id="wsp_" + "E" * 21 + "A",
            )
        self.assertEqual(mismatch.exception.code, "conflict")

        next_invite, next_error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="owner@example.com",
            invitee_name="Owner",
        )
        self.assertIsNone(next_invite)
        self.assertEqual(next_error["code"], "account_already_provisioned")

    def test_malformed_store_and_wrong_namespace_fail_closed(self):
        transport = MemoryTransport()
        service = self.build(transport)
        with self.assertRaises(authority.TesterInviteAuthorityError) as wrong:
            service.read_provisioning_invitation(
                "tinv_" + "A" * 22 + "." + "B" * 43
            )
        self.assertEqual(wrong.exception.code, "invalid_invite")

        issued, error = service.issue_invitation(
            actor=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        self.assertIsNone(error)
        token = issued["rawToken"]
        invitation_id = issued["invite"]["id"]
        digest = authority.hashlib.sha256(token.encode("ascii")).hexdigest()
        transport.values[
            authority._invitation_token_key(invitation_id, digest)
        ] = "{malformed"
        with self.assertRaises(authority.TesterInviteAuthorityError):
            service.read_provisioning_invitation(token)


if __name__ == "__main__":
    unittest.main()
