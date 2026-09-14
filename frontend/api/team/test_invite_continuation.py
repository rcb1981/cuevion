"""Continuation boundary tests using the existing Team acceptance authority."""

from __future__ import annotations

import json
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from unittest.mock import patch

from api.auth import runtime, session_store
from api.team import authority, invite


NOW = 1_800_000_000
SECRET = "continuation-test-secret-" + "x" * 40
ENVIRONMENT = {"CUEVION_AUTH_SESSION_SECRET": SECRET}
OWNER = runtime.AuthenticatedMemberContext(
    "usr_" + "B" * 21 + "Q", "owner@example.com", "Owner",
    "wsp_" + "A" * 22, "owner",
)
MEMBER = runtime.AuthenticatedMemberContext(
    "usr_" + "A" * 22, "member@example.com", "Member", OWNER.workspace_id, "member",
)
SESSION = runtime.AuthenticatedMemberSessionContext(
    MEMBER, 1, "https://issuer.example/", "subject-member", "A" * 43,
    "B" * 43, NOW - 30, NOW + 3600,
)


def binding_for(session=SESSION):
    return session_store.TeamInviteContinuationBinding(
        session.session_id, session.member.user_id, session.member.workspace_id,
        session.issuer, session.subject, session.issued_at, session.expires_at,
    )


class MemoryTeamTransport:
    """Deterministic primary snapshots and CAS outcomes for the real authority."""

    def __init__(self, invitation):
        self.lock = threading.Lock()
        self.applied_accepts = 0
        self.values = {}
        wire = authority._canonical_json(invitation)
        workspace_id, invitation_id, email = invitation["workspaceId"], invitation["id"], invitation["inviteeEmail"]
        for key in (
            authority._invitation_token_key(invitation_id, invitation["tokenDigest"]),
            authority._workspace_invitation_key(workspace_id, invitation_id),
            authority._workspace_recipient_invitation_key(workspace_id, email),
        ):
            self.values[key] = wire
        self.values[authority._pending_index_key(workspace_id)] = authority._canonical_json([invitation_id])
        self.values[authority._members_index_key(workspace_id)] = "[]"

    def __call__(self, command):
        with self.lock:
            if command[0] == "GET":
                return {"result": self.values.get(command[1])}
            if command[0] != "EVAL":
                raise AssertionError("unexpected command")
            script, key_count = command[1], command[2]
            keys = command[3:3 + key_count]
            arguments = command[3 + key_count:]
            if script == "return redis.call('GET', KEYS[1])":
                return {"result": self.values.get(keys[0])}
            if script == authority._PROVISIONING_SNAPSHOT_LUA:
                return {"result": [self.values.get(key, False) for key in keys]}
            if script != authority.ATOMIC_MUTATION_SCRIPTS["accept"]:
                raise AssertionError("unexpected mutation")
            if any(self.values.get(key) != arguments[0] for key in keys[:3]):
                return {"result": "stale"}
            current = json.loads(arguments[0])
            if current["status"] != "invited":
                return {"result": current["status"]}
            if current["expiresAt"] <= int(arguments[3]):
                return {"result": "expired"}
            if keys[3] in self.values or keys[6] in self.values:
                return {"result": "member_active"}
            for key in keys[:3]:
                self.values[key] = arguments[1]
            self.values[keys[3]] = arguments[2]
            self.values[keys[4]] = authority._canonical_json([arguments[4]])
            self.values[keys[5]] = "[]"
            self.values[keys[6]] = arguments[8]
            self.applied_accepts += 1
            return {"result": "applied"}


class MemoryContinuationStore:
    def __init__(self, record):
        self.record = record
        self.lock = threading.Lock()
        self.completions = 0
        self.fail_completion = False
        self.completion_response_lost = False

    def get_team_invite_continuation(self, binding, *, secret, now):
        with self.lock:
            record = self.record
            if record is None or record.binding != binding or not record.created_at <= now < record.expires_at:
                return None
            return record

    def complete_team_invite_continuation(self, record, binding, *, secret, now):
        with self.lock:
            if self.fail_completion:
                self.fail_completion = False
                raise session_store.SessionStoreUnavailable()
            terminal = replace(record, status="complete", raw_invite_token=None)
            if self.record == terminal:
                return True
            if self.record != record or binding != record.binding or not record.created_at <= now < record.expires_at:
                return False
            self.record = terminal
            self.completions += 1
            if self.completion_response_lost:
                self.completion_response_lost = False
                raise session_store.SessionStoreUnavailable()
            return True


class InviteContinuationTests(unittest.TestCase):
    def setUp(self):
        self.invitation_id = authority.generate_invitation_id(random_bytes=lambda count: b"i" * count)
        self.token, digest = authority.generate_invitation_token(self.invitation_id, random_bytes=lambda count: b"t" * count)
        invitation = authority.build_invitation_record(
            invitation_id=self.invitation_id, actor=OWNER, invitee_email=MEMBER.email,
            invitee_name=MEMBER.name, access_level="Shared", token_digest=digest,
            now_ms=(NOW - 60) * 1000,
        )
        self.team_transport = MemoryTeamTransport(invitation)
        self.team = authority.RuntimeTeamAuthority(
            self.team_transport, environment={}, now_ms=lambda: NOW * 1000,
            inviter_owner_validator=lambda *_args: True,
        )
        self.pending = session_store.TeamInviteContinuationRecord(
            binding_for(), self.invitation_id, digest, OWNER.user_id, MEMBER.email,
            invitation["expiresAt"], self.token, NOW, NOW + 120,
        )
        self.store = MemoryContinuationStore(self.pending)
        self.session = SESSION
        self.resolution_calls = 0

    def request(self, *, method="POST", path="/api/team/invite?op=continue", body=b"{}", headers=None, now=NOW):
        if headers is None:
            headers = (
                ("host", "app.cuevion.com"), ("origin", "https://app.cuevion.com"),
                ("sec-fetch-site", "same-origin"), ("content-type", "application/json"),
                ("content-length", str(len(body))),
            )
        def resolver(_headers, **_kwargs):
            self.resolution_calls += 1
            return runtime.AuthenticatedMemberSessionResolution(
                runtime.MemberResolutionOutcome.AUTHENTICATED, self.session,
            )
        return invite.team_invite_continuation_response(
            method, path, headers, body, environment=ENVIRONMENT, now=now,
            session_resolver=resolver, session_store_factory=lambda _env: self.store,
            team_authority_factory=lambda _env: self.team,
        )

    def assert_accepted(self, response):
        self.assertEqual(response.status, 200)
        self.assertEqual(json.loads(response.body), {"ok": True, "status": "accepted"})
        self.assertNotIn(self.token.encode(), response.body)
        self.assertNotIn(SECRET.encode(), response.body)

    def test_exact_session_accepts_and_terminal_replay_never_mutates(self):
        self.assert_accepted(self.request())
        self.assertEqual(self.team_transport.applied_accepts, 1)
        self.assertEqual(self.store.record.status, "complete")
        self.assertIsNone(self.store.record.raw_invite_token)
        with patch.object(self.team, "read_provisioning_invitation", side_effect=AssertionError("must not re-read")):
            self.assert_accepted(self.request())
        self.assertEqual(self.team_transport.applied_accepts, 1)
        self.assertEqual(self.store.completions, 1)

    def test_all_session_binding_components_are_required(self):
        changes = (
            {"session_id": "C" * 43}, {"issuer": "https://different.example/"},
            {"subject": "other-subject"}, {"issued_at": NOW - 29}, {"expires_at": NOW + 3599},
            {"member": replace(MEMBER, user_id="usr_" + "C" * 21 + "Q")},
            {"member": replace(MEMBER, workspace_id="wsp_" + "C" * 22)},
        )
        for change in changes:
            with self.subTest(change=tuple(change)):
                self.session = replace(SESSION, **change)
                self.assertEqual(self.request().status, 404)
        self.assertEqual(self.team_transport.applied_accepts, 0)
        self.assertEqual(self.store.record.status, "pending")

    def test_canonical_recipient_and_member_role_required(self):
        for member in (replace(MEMBER, email="other@example.com"), replace(MEMBER, membership_role="owner"), replace(MEMBER, membership_role="admin")):
            with self.subTest(role=member.membership_role, recipient=member.email == MEMBER.email):
                self.session = replace(SESSION, member=member)
                self.assertEqual(self.request().status, 403)
        self.assertEqual(self.team_transport.applied_accepts, 0)

    def test_every_server_invitation_incarnation_fact_is_reproved(self):
        original = self.team.read_provisioning_invitation(self.token)
        changes = (
            {"invitation_id": "tinv_other"}, {"token_digest": "0" * 64},
            {"workspace_id": "wsp_" + "C" * 22}, {"inviter_user_id": MEMBER.user_id},
            {"email": "other@example.com"}, {"expires_at": original.expires_at + 1},
            {"status": "cancelled"},
        )
        for change in changes:
            with self.subTest(change=tuple(change)), patch.object(self.team, "read_provisioning_invitation", return_value=replace(original, **change)):
                self.assertEqual(self.request().status, 409)
        self.assertEqual(self.team_transport.applied_accepts, 0)

    def test_missing_expired_malformed_and_foreign_store_records_fail_closed(self):
        self.store.record = None
        self.assertEqual(self.request().status, 404)
        self.store.record = self.pending
        self.assertEqual(self.request(now=NOW + 120).status, 404)
        with patch.object(self.store, "get_team_invite_continuation", side_effect=ValueError(self.token + SECRET)):
            response = self.request()
            self.assertEqual(response.status, 503)
            self.assertNotIn(self.token.encode(), response.body)
            self.assertNotIn(SECRET.encode(), response.body)
        with patch.object(self.store, "get_team_invite_continuation", return_value=replace(self.pending, binding=replace(binding_for(), subject="different"))):
            self.assertEqual(self.request().status, 409)
        self.assertEqual(self.team_transport.applied_accepts, 0)

    def test_crash_before_accept_leaves_retryable_pending(self):
        with patch.object(self.team, "accept_invitation", side_effect=RuntimeError(self.token)):
            self.assertEqual(self.request().status, 503)
        self.assertEqual(self.store.record.status, "pending")
        self.assertEqual(self.team_transport.applied_accepts, 0)
        self.assert_accepted(self.request())

    def test_successful_accept_response_loss_is_recovered_by_exact_proof(self):
        accept = self.team.accept_invitation
        def response_lost(**kwargs):
            accept(**kwargs)
            raise RuntimeError(self.token)
        with patch.object(self.team, "accept_invitation", side_effect=response_lost):
            self.assert_accepted(self.request())
        self.assertEqual(self.team_transport.applied_accepts, 1)

    def test_crash_between_acceptance_and_completion_recovers_without_accepting_again(self):
        self.store.fail_completion = True
        self.assertEqual(self.request().status, 503)
        self.assertEqual(self.store.record.status, "pending")
        self.assertEqual(self.team_transport.applied_accepts, 1)
        with patch.object(self.team, "accept_invitation", side_effect=AssertionError("must not replay acceptance")):
            self.assert_accepted(self.request())
        self.assertEqual(self.team_transport.applied_accepts, 1)

    def test_terminal_response_loss_is_safe_to_retry(self):
        self.store.completion_response_lost = True
        self.assertEqual(self.request().status, 503)
        self.assertEqual(self.store.record.status, "complete")
        self.assert_accepted(self.request())
        self.assertEqual(self.team_transport.applied_accepts, 1)

    def test_completion_requires_strict_acceptance_proof_and_exact_cas(self):
        with patch.object(self.team, "prove_provisioning_acceptance", return_value=1):
            self.assertEqual(self.request().status, 409)
        self.assertEqual(self.store.record.status, "pending")
        with patch.object(self.store, "complete_team_invite_continuation", return_value=False):
            self.assertEqual(self.request().status, 409)
        self.assertEqual(self.store.record.status, "pending")
        self.assert_accepted(self.request())

    def test_concurrent_requests_share_one_atomic_accept_and_terminal(self):
        barrier = threading.Barrier(2)
        original = self.team.read_provisioning_invitation
        def synchronized_read(*args, **kwargs):
            result = original(*args, **kwargs)
            barrier.wait(timeout=5)
            return result
        with patch.object(self.team, "read_provisioning_invitation", side_effect=synchronized_read), ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _index: self.request(), range(2)))
        for response in responses:
            self.assert_accepted(response)
        self.assertEqual(self.team_transport.applied_accepts, 1)
        self.assertEqual(self.store.completions, 1)

    def test_removed_membership_or_replaced_invitation_cannot_complete(self):
        self.team.accept_invitation(actor=MEMBER, token=self.token)
        membership_key = authority._member_key(MEMBER.workspace_id, MEMBER.email)
        del self.team_transport.values[membership_key]
        self.assertNotEqual(self.request().status, 200)
        self.assertEqual(self.store.record.status, "pending")

    def test_replaced_recipient_invitation_cannot_complete_old_incarnation(self):
        self.team.accept_invitation(actor=MEMBER, token=self.token)
        key = authority._workspace_recipient_invitation_key(MEMBER.workspace_id, MEMBER.email)
        changed = json.loads(self.team_transport.values[key])
        changed["id"] = "tinv_replacement"
        self.team_transport.values[key] = authority._canonical_json(changed)
        self.assertEqual(self.request().status, 409)
        self.assertEqual(self.store.record.status, "pending")

    def test_another_user_accepting_same_email_never_proves_this_session(self):
        another_user = replace(MEMBER, user_id="usr_" + "C" * 21 + "Q")
        self.team.accept_invitation(actor=another_user, token=self.token)
        self.assertEqual(self.request().status, 403)
        self.assertEqual(self.store.record.status, "pending")
        self.assertEqual(self.store.completions, 0)

    def test_authentication_resolution_fails_before_continuation_storage(self):
        headers = (("host", "app.cuevion.com"), ("origin", "https://app.cuevion.com"), ("content-type", "application/json"), ("content-length", "2"))
        for outcome, status in ((runtime.MemberResolutionOutcome.UNAUTHENTICATED, 401), (runtime.MemberResolutionOutcome.UNAVAILABLE, 503)):
            with self.subTest(outcome=outcome):
                cookie = "__Host-cuevion_session=; Path=/; Max-Age=0; Secure; HttpOnly"
                resolution = runtime.AuthenticatedMemberSessionResolution(outcome, None, (cookie,))
                response = invite.team_invite_continuation_response(
                    "POST", "/api/team/invite?op=continue", headers, b"{}",
                    session_resolver=lambda *_args, **_kwargs: resolution,
                    session_store_factory=lambda _env: self.fail("must not read continuation"),
                )
                self.assertEqual(response.status, status)
                self.assertIn(("Set-Cookie", cookie), response.headers)
        self.assertEqual(self.team_transport.applied_accepts, 0)

    def test_only_exact_post_query_and_empty_json_object_are_accepted(self):
        for method in ("GET", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"):
            with self.subTest(method=method):
                self.assertEqual(self.request(method=method).status, 405)
        for suffix in ("&token=secret", "&workspaceId=other", "&op=continue", "&return_to=/", "#fragment"):
            with self.subTest(query=suffix):
                self.assertEqual(self.request(path="/api/team/invite?op=continue" + suffix).status, 400)
        for body in (b"", b"[]", b"null", b'{"token":"secret"}', b'{"x":1,"x":2}', b"{", b"NaN", b"\xff", b" " * 129):
            with self.subTest(body=body[:10]):
                self.assertEqual(self.request(body=body).status, 400)
        self.assertEqual(self.resolution_calls, 0)
        self.assertEqual(self.team_transport.applied_accepts, 0)

    def test_cross_origin_and_ambiguous_body_headers_do_not_resolve_session(self):
        base = (("host", "app.cuevion.com"), ("origin", "https://app.cuevion.com"), ("sec-fetch-site", "same-origin"), ("content-type", "application/json"), ("content-length", "2"))
        variants = (
            tuple((name, "https://evil.example" if name == "origin" else value) for name, value in base),
            tuple(pair for pair in base if pair[0] != "origin"),
            base + (("origin", "https://app.cuevion.com"),),
            base + (("content-length", "2"),),
            base + (("transfer-encoding", "chunked"),),
            tuple((name, "3" if name == "content-length" else value) for name, value in base),
            tuple((name, "text/plain" if name == "content-type" else value) for name, value in base),
        )
        for headers in variants:
            with self.subTest(headers=tuple(name for name, _value in headers)):
                self.assertNotEqual(self.request(headers=headers).status, 200)
        self.assertEqual(self.resolution_calls, 0)


if __name__ == "__main__":
    unittest.main()
