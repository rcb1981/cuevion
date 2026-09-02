from __future__ import annotations

import copy
import unittest

from .models import (
    MAX_V2_EXTERNAL_GUESTS,
    build_v2_guest_thread_dto,
    generate_v2_bearer_secret,
    generate_v2_opaque_id,
    hash_v2_secret,
    decode_v2_wire_record,
    encode_v2_wire_record,
    normalize_v2_invite_record,
    normalize_v2_external_guest_projection,
    normalize_v2_external_guest_projection_item,
    normalize_v2_thread_record,
    build_v2_guest_shared_reply,
    build_v2_owner_internal_message,
)

SEC = 1_800_000_000
MS = SEC * 1000
WORKSPACE_ID = "wsp_" + "W" * 22


def sample_thread() -> dict:
    return {
        "v": 2,
        "collaborationId": "A" * 22,
        "ownerEmail": "owner@example.com",
        "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1",
        "sourceRef": {"provider": "google", "providerMessageId": "gmail-1"},
        "sourceMessage": {
            "subject": "Quarterly review",
            "senderDisplay": "Sender",
            "fromDisplay": "Sender <sender@example.com>",
            "timestamp": "Tue, 14 Jul 2026 10:00:00 +0200",
            "bodyText": "Shared source body",
        },
        "state": "needs_review",
        "messages": [
            {
                "id": "B" * 22,
                "authorKind": "owner",
                "authorDisplayName": "Owner",
                "text": "Internal plan",
                "visibility": "internal",
                "createdAt": MS + 100,
            },
            {
                "id": "C" * 22,
                "authorKind": "guest",
                "authorDisplayName": "Reviewer",
                "text": "Shared answer",
                "visibility": "shared",
                "createdAt": MS + 101,
            },
        ],
        "createdAt": MS + 100,
        "updatedAt": MS + 101,
    }


def sample_invite() -> dict:
    return {
        "v": 2,
        "inviteId": "D" * 22,
        "tokenHash": "a" * 64,
        "ownerEmail": "owner@example.com",
        "workspaceId": WORKSPACE_ID,
        "mailboxId": "mailbox-1",
        "collaborationId": "A" * 22,
        "invitedEmail": "reviewer@example.com",
        "identityAssurance": "link_possession",
        "allowedActions": ["read", "reply"],
        "visibility": "shared_only",
        "createdBy": {"ownerEmail": "owner@example.com", "displayName": "Owner"},
        "createdAt": SEC + 100,
        "expiresAt": SEC + 200,
        "status": "active",
        "exchangedAt": None,
        "exchangeCount": 0,
        "revokedAt": None,
        "revokedBy": None,
    }


def sample_participant_thread() -> dict:
    return {
        **sample_thread(),
        "workspaceId": "wsp_" + "W" * 22,
        "ownerUserId": "usr_" + "A" * 22,
        "ownerDisplayName": "Owner",
        "participants": [
            {
                "userId": "usr_" + "C" * 21 + "A",
                "membershipRef": "tinv_second",
                "displayName": "Second",
            },
            {
                "userId": "usr_" + "B" * 21 + "A",
                "membershipRef": "tinv_first",
                "displayName": "First",
            },
        ],
    }


class CollaborationV2ModelTests(unittest.TestCase):
    def test_external_guest_projection_normalizes_pending_and_active(self):
        pending = {
            "inviteId": "P" * 22,
            "invitedEmail": "reviewer@example.com",
            "status": "pending",
            "expiresAt": SEC + 100,
        }
        active = {
            "inviteId": "A" * 22,
            "displayName": "Trusted Reviewer",
            "status": "active",
            "expiresAt": SEC + 200,
        }
        self.assertEqual(normalize_v2_external_guest_projection_item(pending), pending)
        self.assertEqual(normalize_v2_external_guest_projection_item(active), active)
        self.assertEqual(
            [entry["inviteId"] for entry in normalize_v2_external_guest_projection([pending, active])],
            ["A" * 22, "P" * 22],
        )

    def test_external_guest_projection_rejects_malformed_public_fields(self):
        valid = {"inviteId": "P" * 22, "status": "pending", "expiresAt": SEC + 100}
        for field, value in (
            ("inviteId", "short"),
            ("invitedEmail", "not-an-email"),
            ("status", "invited"),
            ("expiresAt", "1800000100"),
        ):
            changed = {**valid, field: value}
            self.assertIsNone(normalize_v2_external_guest_projection_item(changed), field)
        for secret in ("tokenHash", "sessionHash", "csrfTokenHash", "ownerEmail", "workspaceId"):
            self.assertIsNone(
                normalize_v2_external_guest_projection_item({**valid, secret: "secret"}),
                secret,
            )
        self.assertIsNone(
            normalize_v2_external_guest_projection_item({**valid, "displayName": "Too early"})
        )

    def test_external_guest_projection_is_bounded_and_deduplicated(self):
        maximum = [
            {
                "inviteId": f"{index:022d}",
                "status": "pending",
                "expiresAt": SEC + 100,
            }
            for index in range(MAX_V2_EXTERNAL_GUESTS)
        ]
        self.assertEqual(len(normalize_v2_external_guest_projection(maximum)), MAX_V2_EXTERNAL_GUESTS)
        self.assertIsNone(
            normalize_v2_external_guest_projection(
                maximum + [{**maximum[0], "inviteId": "Z" * 22}]
            )
        )
        self.assertIsNone(normalize_v2_external_guest_projection([maximum[0], maximum[0]]))

    def test_external_guest_projection_never_enters_participant_authority(self):
        participant_thread = sample_participant_thread()
        participant_thread["participants"][0] = {
            "userId": "I" * 22,
            "membershipRef": "tinv_guest",
            "displayName": "Guest",
        }
        self.assertIsNone(normalize_v2_thread_record(participant_thread))
        normalized = normalize_v2_thread_record(sample_participant_thread())
        self.assertNotIn("externalGuests", normalized)

    def test_participant_authority_is_backward_compatible_and_deterministic(self):
        old = normalize_v2_thread_record(sample_thread())
        self.assertIsNotNone(old)
        self.assertNotIn("participants", old)

        normalized = normalize_v2_thread_record(sample_participant_thread())
        self.assertIsNotNone(normalized)
        self.assertEqual(
            [participant["displayName"] for participant in normalized["participants"]],
            ["First", "Second"],
        )
        self.assertEqual(
            normalize_v2_thread_record(copy.deepcopy(normalized)),
            normalized,
        )

    def test_participant_authority_rejects_duplicates_owner_and_malformed_values(self):
        duplicate = sample_participant_thread()
        duplicate["participants"][1]["userId"] = duplicate["participants"][0]["userId"]
        self.assertIsNone(normalize_v2_thread_record(duplicate))

        owner_duplicate = sample_participant_thread()
        owner_duplicate["participants"][0]["userId"] = owner_duplicate["ownerUserId"]
        self.assertIsNone(normalize_v2_thread_record(owner_duplicate))

        malformed_id = sample_participant_thread()
        malformed_id["participants"][0]["userId"] = "user-email@example.test"
        self.assertIsNone(normalize_v2_thread_record(malformed_id))

        malformed_provenance = sample_participant_thread()
        malformed_provenance["participants"][0]["membershipRef"] = "invitation@example.test"
        self.assertIsNone(normalize_v2_thread_record(malformed_provenance))

        partial = sample_participant_thread()
        del partial["ownerDisplayName"]
        self.assertIsNone(normalize_v2_thread_record(partial))

    def test_participant_authority_enforces_fifteen_explicit_people(self):
        maximum = sample_participant_thread()
        maximum["participants"] = [
            {
                "userId": "usr_" + chr(66 + index) * 21 + "A",
                "membershipRef": f"tinv_{index}",
                "displayName": f"Person {index}",
            }
            for index in range(15)
        ]
        self.assertIsNotNone(normalize_v2_thread_record(maximum))
        maximum["participants"].append(
            {
                "userId": "usr_" + "z" * 21 + "A",
                "membershipRef": "tinv_overflow",
                "displayName": "Overflow",
            }
        )
        self.assertIsNone(normalize_v2_thread_record(maximum))

    def test_thread_requires_canonical_owner_identity_and_matching_workspace(self):
        normalized = normalize_v2_thread_record(sample_thread())
        self.assertIsNotNone(normalized)
        self.assertEqual(normalized["ownerEmail"], "owner@example.com")
        changed = sample_thread()
        changed["ownerEmail"] = "Owner@example.com"
        self.assertIsNone(normalize_v2_thread_record(changed))
        changed = sample_thread()
        changed["workspaceId"] = "attacker@example.com"
        self.assertIsNone(normalize_v2_thread_record(changed))

    def test_message_visibility_is_required_on_the_canonical_record(self):
        record = sample_thread()
        del record["messages"][0]["visibility"]
        self.assertIsNone(normalize_v2_thread_record(record))

    def test_guest_dto_is_an_exact_allowlist_and_strips_internal_identifiers(self):
        original = sample_thread()
        dto = build_v2_guest_thread_dto(original)
        self.assertEqual(
            set(dto),
            {"collaborationId", "state", "updatedAt", "allowedActions", "sharedSource", "messages"},
        )
        self.assertEqual(
            set(dto["sharedSource"]),
            {"subject", "senderDisplay", "fromDisplay", "timestamp", "bodyText"},
        )
        serialized = repr(dto)
        for forbidden in ("owner@example.com", "mailbox-1", "gmail-1", "Internal plan"):
            self.assertNotIn(forbidden, serialized)
        self.assertEqual(dto["messages"][0]["authorRole"], "Guest reviewer")
        dto["messages"][0]["text"] = "changed"
        self.assertEqual(original["messages"][1]["text"], "Shared answer")

    def test_thread_rejects_html_attachments_unknown_fields_and_oversized_text(self):
        for field, value in (
            ("bodyHtml", "<b>secret</b>"),
            ("attachments", [{"id": "secret"}]),
        ):
            record = sample_thread()
            record["sourceMessage"][field] = value
            self.assertIsNone(normalize_v2_thread_record(record))
        record = sample_thread()
        record["messages"][0]["text"] = "x" * 16_385
        self.assertIsNone(normalize_v2_thread_record(record))

    def test_source_and_message_count_boundaries(self):
        for size, accepted in ((131_071, True), (131_072, True), (131_073, False)):
            record = sample_thread()
            record["sourceMessage"]["bodyText"] = "x" * size
            self.assertEqual(normalize_v2_thread_record(record) is not None, accepted)
        record = sample_thread()
        record["messages"] = [
            {"id": f"{index:022d}", "authorKind": "owner", "authorDisplayName": "Owner", "text": "x", "createdAt": MS + 100, "visibility": "internal"}
            for index in range(500)
        ]
        self.assertIsNotNone(normalize_v2_thread_record(record))
        record["messages"].append({"id": "Z" * 22, "authorKind": "owner", "authorDisplayName": "Owner", "text": "x", "createdAt": MS + 100, "visibility": "internal"})
        self.assertIsNone(normalize_v2_thread_record(record))

    def test_imap_source_identity_is_bounded_and_uidvalidity_is_required(self):
        record = sample_thread()
        record["sourceRef"] = {
            "provider": "custom_imap",
            "folder": "INBOX",
            "uidValidity": "99",
            "imapUid": "7",
        }
        self.assertIsNotNone(normalize_v2_thread_record(record))
        record["sourceRef"]["folder"] = "Archive"
        self.assertIsNone(normalize_v2_thread_record(record))

    def test_imap_identifiers_reject_noncanonical_types_and_controls(self):
        for uid in (7, 7.0, "07", "+7", " 7", "0", "7\n"):
            record = sample_thread()
            record["sourceRef"] = {
                "provider": "custom_imap", "folder": "INBOX",
                "uidValidity": "99", "imapUid": uid,
            }
            self.assertIsNone(normalize_v2_thread_record(record))
        record = sample_thread()
        record["mailboxId"] = "mailbox\rInjected"
        self.assertIsNone(normalize_v2_thread_record(record))

    def test_invite_lifetime_boundary_and_status_linkage(self):
        below = sample_invite()
        below["expiresAt"] = below["createdAt"] + 86_399
        self.assertIsNotNone(normalize_v2_invite_record(below))
        exact = sample_invite()
        exact["expiresAt"] = exact["createdAt"] + 86_400
        self.assertIsNotNone(normalize_v2_invite_record(exact))
        above = sample_invite()
        above["expiresAt"] = above["createdAt"] + 86_401
        self.assertIsNone(normalize_v2_invite_record(above))
        exchanged = sample_invite()
        exchanged.update(status="exchanged", exchangeCount=1, exchangedAt=SEC + 101, activeSessionHash="b" * 64)
        self.assertIsNotNone(normalize_v2_invite_record(exchanged))
        del exchanged["activeSessionHash"]
        self.assertIsNone(normalize_v2_invite_record(exchanged))

    def test_server_message_constructors_require_exact_opaque_capabilities(self):
        context = {"action": "internal_note", "user": {"name": "Owner"}}
        self.assertIsNone(build_v2_owner_internal_message(context, "Safe text"))
        self.assertIsNone(build_v2_guest_shared_reply(
            {"status": "active", "allowedActions": ["read", "reply"], "visibility": "shared_only", "guestDisplayName": "Guest"},
            "Reply",
        ))
        self.assertIsNone(build_v2_guest_shared_reply({"status": "active"}, "Reply"))

    def test_v2_versions_safe_integers_unicode_and_email_are_canonical(self):
        for version in (True, 2.0, "2"):
            thread = sample_thread()
            thread["v"] = version
            self.assertIsNone(normalize_v2_thread_record(thread))
            invite = sample_invite()
            invite["v"] = version
            self.assertIsNone(normalize_v2_invite_record(invite))
        boundary = sample_thread()
        boundary["updatedAt"] = 4_102_444_800_999
        self.assertIsNotNone(normalize_v2_thread_record(boundary))
        boundary["updatedAt"] = 2**53
        self.assertIsNone(normalize_v2_thread_record(boundary))
        for value in (float("nan"), float("inf"), True):
            thread = sample_thread()
            thread["updatedAt"] = value
            self.assertIsNone(normalize_v2_thread_record(thread))
        for hidden in ("Owner\u202e", "Owner\u200b", "Owner\ud800"):
            thread = sample_thread()
            thread["messages"][0]["authorDisplayName"] = hidden
            self.assertIsNone(normalize_v2_thread_record(thread))
        international = sample_thread()
        international["ownerEmail"] = international["workspaceId"] = "rütger@example.com"
        self.assertIsNone(normalize_v2_thread_record(international))
        decomposed = sample_thread()
        decomposed["mailboxId"] = "mailbox-e\u0301"
        self.assertIsNone(normalize_v2_thread_record(decomposed))
        multiline = sample_thread()
        multiline["messages"][0]["text"] = "first\nsecond"
        multiline["sourceMessage"]["bodyText"] = "source\nbody"
        self.assertIsNotNone(normalize_v2_thread_record(multiline))

    def test_invitation_schema_enforces_possession_capabilities_and_state(self):
        normalized = normalize_v2_invite_record(sample_invite())
        self.assertEqual(normalized["invitedEmail"], "reviewer@example.com")
        self.assertEqual(normalized["workspaceId"], WORKSPACE_ID)
        self.assertNotEqual(normalized["workspaceId"], normalized["ownerEmail"])
        for malformed_workspace in (
            "owner@example.com",
            "wsp_short",
            "wsp_" + "W" * 21 + ".",
        ):
            malformed = sample_invite()
            malformed["workspaceId"] = malformed_workspace
            self.assertIsNone(normalize_v2_invite_record(malformed))
        for field, value in (
            ("allowedActions", ["read", "reply", "resolve"]),
            ("visibility", "all"),
            ("exchangeCount", 1),
        ):
            record = copy.deepcopy(sample_invite())
            record[field] = value
            self.assertIsNone(normalize_v2_invite_record(record))

    def test_wire_integer_schema_is_canonical_and_round_trips_without_coercion(self):
        thread = normalize_v2_thread_record(sample_thread())
        invite = normalize_v2_invite_record(sample_invite())
        self.assertIsNotNone(thread)
        self.assertIsNotNone(invite)
        thread_wire = encode_v2_wire_record(thread, "thread")
        invite_wire = encode_v2_wire_record(invite, "invite")
        self.assertEqual(thread_wire["v"], "2")
        self.assertEqual(thread_wire["messages"][0]["createdAt"], str(MS + 100))
        self.assertEqual(invite_wire["exchangeCount"], "0")
        self.assertEqual(decode_v2_wire_record(thread_wire, "thread"), thread)
        self.assertEqual(decode_v2_wire_record(invite_wire, "invite"), invite)

        for malformed in (2, 2.0, True, "02", "+2", "-0", "2.0", "2e0"):
            changed = copy.deepcopy(thread_wire)
            changed["v"] = malformed
            self.assertIsNone(decode_v2_wire_record(changed, "thread"), malformed)

        for optional in ("invitedEmail", "activeSessionHash"):
            changed = sample_invite()
            changed[optional] = None
            self.assertIsNone(normalize_v2_invite_record(changed), optional)

    def test_utf8_byte_limits_match_the_wire_schema(self):
        record = sample_thread()
        record["messages"][0]["text"] = "é" * (16_384 // 2)
        self.assertIsNotNone(normalize_v2_thread_record(record))
        record["messages"][0]["text"] += "é"
        self.assertIsNone(normalize_v2_thread_record(record))

        aggregate = sample_thread()
        message = aggregate["messages"][0]
        aggregate["messages"] = [
            {
                **message,
                "id": f"{index:022d}",
                "text": "é" * (16_384 // 2),
            }
            for index in range(1, 16)
        ]
        self.assertIsNotNone(normalize_v2_thread_record(aggregate))
        aggregate["messages"].append(
            {
                **message,
                "id": f"{16:022d}",
                "text": "é" * (16_384 // 2),
            }
        )
        self.assertIsNone(normalize_v2_thread_record(aggregate))

    def test_revocation_actor_is_exactly_the_canonical_owner(self):
        for actor, accepted in (
            ("owner@example.com", True),
            ("other@example.com", False),
            ("Owner@example.com", False),
            ("Owner", False),
        ):
            invite = sample_invite()
            invite.update(
                status="revoked",
                revokedAt=SEC + 101,
                revokedBy=actor,
            )
            self.assertEqual(normalize_v2_invite_record(invite) is not None, accepted)

    def test_generated_ids_and_bearers_have_required_entropy_and_hash_cleanly(self):
        opaque = generate_v2_opaque_id()
        bearer = generate_v2_bearer_secret()
        self.assertGreaterEqual(len(opaque), 22)
        self.assertGreaterEqual(len(bearer), 43)
        self.assertRegex(hash_v2_secret(bearer), r"^[0-9a-f]{64}$")
        with self.assertRaises(ValueError):
            generate_v2_opaque_id(15)
        with self.assertRaises(ValueError):
            generate_v2_bearer_secret(31)


if __name__ == "__main__":
    unittest.main()
