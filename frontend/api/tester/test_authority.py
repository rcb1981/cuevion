from __future__ import annotations

import json
import unittest

from api.tester import authority

NOW = 1_800_000_000_000
ADMIN = "usr_" + "A" * 21 + "A"
OTHER = "usr_" + "B" * 21 + "A"
USER = "usr_" + "C" * 21 + "A"
WORKSPACE = "wsp_" + "D" * 21 + "A"


class Clock:
    def __init__(self, value=NOW):
        self.value = value
    def __call__(self):
        return self.value


class FixedRandom:
    def __init__(self):
        self.counter = 1
    def __call__(self, size):
        value = bytes([self.counter]) * size
        self.counter += 1
        return value


class MemoryRedis:
    def __init__(self):
        self.values: dict[str, str] = {}
        self.lose_ack_once: set[str] = set()

    def __call__(self, command):
        if command[:2] == ["EVAL", authority._PRIMARY_SNAPSHOT_LUA]:
            count = int(command[2])
            return {"result": [
                self.values.get(str(key), False)
                for key in command[3:3+count]
            ]}
        if command[:2] == ["EVAL", authority._ISSUE_INVITATION_LUA]:
            count = int(command[2])
            keys = [str(v) for v in command[3:3+count]]
            wire = str(command[3+count])
            now = int(command[4+count])
            current_raw = self.values.get(keys[2])
            if current_raw is not None:
                current = json.loads(current_raw)
                status = current.get("status")
                if status == "provisioned":
                    return {"result": "already_provisioned"}
                if status == "invited":
                    expires_at = current.get("expiresAt")
                    if type(expires_at) is not int:
                        return {"result": "malformed"}
                    if expires_at > now:
                        return {"result": "invite_live"}
                elif status != "cancelled":
                    return {"result": "malformed"}
            if keys[0] in self.values or keys[1] in self.values:
                return {"result": "collision"}
            for key in keys:
                self.values[key] = wire
            if "issue" in self.lose_ack_once:
                self.lose_ack_once.remove("issue")
                raise RuntimeError("lost ack")
            return {"result": "applied"}
        if command[:2] == ["EVAL", authority._TRANSITION_INVITATION_LUA]:
            count = int(command[2])
            keys = [str(v) for v in command[3:3+count]]
            old = str(command[3+count])
            new = str(command[4+count])
            if any(self.values.get(key) != old for key in keys):
                return {"result": "stale"}
            for key in keys:
                self.values[key] = new
            if "transition" in self.lose_ack_once:
                self.lose_ack_once.remove("transition")
                raise RuntimeError("lost ack")
            return {"result": "applied"}
        raise AssertionError(command[:3])


def build(clock=None):
    memory = MemoryRedis()
    runtime = authority.RuntimeTesterInviteAuthority(
        memory,
        environment={
            "CUEVION_TESTER_ADMIN_USER_IDS": ADMIN,
            "VERCEL_ENV": "production",
        },
        now_ms=clock or Clock(),
        random_bytes=FixedRandom(),
    )
    return runtime, memory


class AuthorityTests(unittest.TestCase):
    def test_admin_only_issue_and_digest_only_storage(self):
        runtime, memory = build()
        with self.assertRaises(authority.TesterInviteAuthorityError):
            runtime.issue_invitation(
                actor_user_id=OTHER,
                invitee_email="tester@example.com",
                invitee_name="Tester",
            )
        issued = runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="TESTER@example.com",
            invitee_name="Tester",
        )
        token = str(issued["rawToken"])
        self.assertRegex(token, r"^tsti_[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}$")
        self.assertTrue(memory.values)
        for wire in memory.values.values():
            self.assertNotIn(token, wire)
            self.assertIn('"tokenDigest":', wire)

    def test_one_live_invite_per_email(self):
        runtime, _ = build()
        runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        with self.assertRaises(authority.TesterInviteAuthorityError) as error:
            runtime.issue_invitation(
                actor_user_id=ADMIN,
                invitee_email="TESTER@example.com",
                invitee_name="Again",
            )
        self.assertEqual(error.exception.code, "live_invitation_exists")

    def test_issue_recovers_lost_ack(self):
        runtime, memory = build()
        memory.lose_ack_once.add("issue")
        issued = runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        self.assertEqual(
            runtime.read_provisioning_invitation(
                str(issued["rawToken"])
            ).status,
            "invited",
        )

    def test_cancel_is_terminal(self):
        runtime, _ = build()
        issued = runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        runtime.cancel_invitation(
            actor_user_id=ADMIN,
            invitation_id=str(issued["invite"]["invitationId"]),
        )
        with self.assertRaises(authority.TesterInviteAuthorityError) as error:
            runtime.read_provisioning_invitation(str(issued["rawToken"]))
        self.assertEqual(error.exception.code, "cancelled_invite")

    def test_provision_is_exact_idempotent_and_recovers_lost_ack(self):
        runtime, memory = build()
        issued = runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        token = str(issued["rawToken"])
        memory.lose_ack_once.add("transition")
        result = runtime.mark_provisioned(
            raw_token=token,
            user_id=USER,
            workspace_id=WORKSPACE,
        )
        self.assertEqual(result.status, "provisioned")
        replay = runtime.mark_provisioned(
            raw_token=token,
            user_id=USER,
            workspace_id=WORKSPACE,
        )
        self.assertEqual(replay, result)
        with self.assertRaises(authority.TesterInviteAuthorityError):
            runtime.mark_provisioned(
                raw_token=token,
                user_id=OTHER,
                workspace_id=WORKSPACE,
            )

    def test_missing_or_mismatched_environment_namespace_fails_closed(self):
        memory = MemoryRedis()
        for environment in (
            {"CUEVION_TESTER_ADMIN_USER_IDS": ADMIN},
            {
                "CUEVION_TESTER_ADMIN_USER_IDS": ADMIN,
                "VERCEL_ENV": "preview",
                "CUEVION_TESTER_AUTHORITY_NAMESPACE": "production",
            },
        ):
            runtime = authority.RuntimeTesterInviteAuthority(
                memory,
                environment=environment,
                now_ms=Clock(),
                random_bytes=FixedRandom(),
            )
            with self.assertRaises(authority.TesterInviteAuthorityError) as error:
                runtime.issue_invitation(
                    actor_user_id=ADMIN,
                    invitee_email="tester@example.com",
                    invitee_name="Tester",
                )
            self.assertEqual(
                error.exception.code,
                "tester_authority_unavailable",
            )
        self.assertEqual(memory.values, {})

    def test_platform_namespaces_isolate_shared_kv_store(self):
        memory = MemoryRedis()
        production = authority.RuntimeTesterInviteAuthority(
            memory,
            environment={
                "CUEVION_TESTER_ADMIN_USER_IDS": ADMIN,
                "VERCEL_ENV": "production",
            },
            now_ms=Clock(),
            random_bytes=FixedRandom(),
        )
        preview = authority.RuntimeTesterInviteAuthority(
            memory,
            environment={
                "CUEVION_TESTER_ADMIN_USER_IDS": ADMIN,
                "VERCEL_ENV": "preview",
            },
            now_ms=Clock(),
            random_bytes=FixedRandom(),
        )

        production_issue = production.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        preview_issue = preview.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )

        self.assertEqual(
            production_issue["rawToken"],
            preview_issue["rawToken"],
        )
        self.assertTrue(
            any(
                key.startswith("cuevion:tester:v1:production:")
                for key in memory.values
            )
        )
        self.assertTrue(
            any(
                key.startswith("cuevion:tester:v1:preview:")
                for key in memory.values
            )
        )

    def test_expiry_fails_closed(self):
        clock = Clock()
        runtime, _ = build(clock)
        issued = runtime.issue_invitation(
            actor_user_id=ADMIN,
            invitee_email="tester@example.com",
            invitee_name="Tester",
        )
        clock.value = NOW + authority.TESTER_INVITE_TTL_MS
        with self.assertRaises(authority.TesterInviteAuthorityError) as error:
            runtime.read_provisioning_invitation(str(issued["rawToken"]))
        self.assertEqual(error.exception.code, "expired_invite")


if __name__ == "__main__":
    unittest.main()
