from __future__ import annotations

import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from . import application, authorization, models, mutations, owner_http, owner_rate_limit, redis_store
from . import test_owner_http as http_fixtures
from . import test_guest_http as guest_fixtures
from .test_owner_idempotency import AUTHOR_ID, COLLABORATION_ID, IDEMPOTENCY_KEY, MS, WORKSPACE_ID, _thread


TEAM_ID = "usr_" + "B" * 21 + "A"
OTHER_ID = "usr_" + "C" * 21 + "A"
OWNER_LABEL = "Owner Person"
TEAM_LABEL = "Emma Stone"


def capability(action="reply", *, participant=False):
    return authorization._InternalCollaborationCapability(
        authorization._INTERNAL_CAPABILITY_SENTINEL,
        "owner@example.com", WORKSPACE_ID, "mailbox-1", "google", COLLABORATION_ID,
        action, "internal" if participant else "owner",
        TEAM_LABEL if participant else OWNER_LABEL, TEAM_ID if participant else AUTHOR_ID,
        "participant" if participant else "owner", AUTHOR_ID, OWNER_LABEL,
    )


def thread():
    return {
        **_thread(), "ownerUserId": AUTHOR_ID, "ownerDisplayName": OWNER_LABEL,
        "participants": [{"userId": TEAM_ID, "membershipRef": "tinv_original", "displayName": "Old Emma"}],
    }


def membership(user_id=TEAM_ID, *, label=TEAM_LABEL, reference="tinv_original"):
    return {"memberUserId": user_id, "sourceInvitationId": reference, "displayName": label}


def mention(user_id=TEAM_ID, label=TEAM_LABEL, *, start=0):
    display = "@" + label
    return {"userId": user_id, "start": start, "end": start + len(display), "displayText": display}


class MentionMutationTests(unittest.TestCase):
    def append(self, body, mentions, *, participant=False, visibility="shared", record=None,
               member=None, saver=None):
        current = thread() if record is None else record
        calls = []

        def save(replacement, expected, **kwargs):
            calls.append((replacement, expected, kwargs))
            if saver is not None:
                return saver(replacement, expected, **kwargs)
            if kwargs.get("fresh_mention_error"):
                code = kwargs["fresh_mention_error"]
                return {"status": "error", "error": {"code": code}}
            message = replacement["messages"][-1]
            return redis_store._V2OwnerAppendResult(message, message["createdAt"], False)

        resolver = Mock(return_value=(membership(), None) if member is None else member)
        with patch.object(authorization, "_resolve_active_team_member", resolver), patch.object(
            mutations.time, "time_ns", return_value=(MS + 10) * 1_000_000,
        ):
            result = mutations.append_owner_v2_message_idempotently(
                capability("reply" if visibility == "shared" else "internal_note", participant=participant),
                body, mentions=mentions, visibility=visibility, idempotency_key=IDEMPOTENCY_KEY,
                thread_loader=lambda *_args, **_kwargs: redis_store._V2RecordResult(current),
                thread_saver=save,
            )
        return result, calls, resolver

    def test_owner_and_team_can_mention_current_team_or_owner_in_both_visibilities(self):
        for participant in (False, True):
            for visibility in ("shared", "internal"):
                body = "@Emma Stone and @Owner Person"
                spans = [mention(AUTHOR_ID, OWNER_LABEL, start=16), mention()]
                with self.subTest(participant=participant, visibility=visibility):
                    result, calls, resolver = self.append(body, spans, participant=participant, visibility=visibility)
                    self.assertEqual(result["status"], "ok", result)
                    self.assertEqual(result["message"]["text"], body)
                    self.assertEqual(result["message"]["mentions"], list(reversed(spans)))
                    self.assertEqual(calls[0][2]["requested_mentions"], list(reversed(spans)))
                    self.assertIsNone(calls[0][2]["fresh_mention_error"])
                    self.assertEqual(calls[0][2]["recipient_user_ids"], [AUTHOR_ID] if participant else [TEAM_ID])
                    resolver.assert_called_once_with(WORKSPACE_ID, TEAM_ID)

    def test_repeated_team_mentions_share_one_current_authority_lookup_with_recipients(self):
        spans = [mention(), mention(start=12)]
        result, calls, resolver = self.append("@Emma Stone @Emma Stone", spans)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls[0][2]["recipient_user_ids"], [TEAM_ID])
        resolver.assert_called_once_with(WORKSPACE_ID, TEAM_ID)

    def test_owner_mention_needs_no_team_authority_without_team_recipients(self):
        current = {**thread(), "participants": []}
        result, calls, resolver = self.append("@Owner Person", [mention(AUTHOR_ID, OWNER_LABEL)], record=current)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(calls[0][2]["recipient_user_ids"], [])
        resolver.assert_not_called()

    def test_stored_participant_label_or_forged_display_text_cannot_replace_current_team_label(self):
        for label in ("Old Emma", "Administrator"):
            with self.subTest(label=label):
                result, calls, _ = self.append("@" + label, [mention(label=label)])
                self.assertEqual(result["error"], {"code": "invalid_request"})
                self.assertEqual(calls[0][2]["fresh_mention_error"], "invalid_request")

    def test_removed_reinvited_or_nonparticipant_targets_fail_the_entire_fresh_append(self):
        for member in ((None, "not_active"), (membership(reference="tinv_reinvited"), None)):
            with self.subTest(member=member):
                result, calls, _ = self.append("@Emma Stone", [mention()], member=member)
                self.assertEqual(result["error"], {"code": "forbidden"})
                self.assertEqual(calls[0][2]["fresh_mention_error"], "forbidden")
                self.assertEqual(calls[0][2]["recipient_user_ids"], [])
        current = {**thread(), "participants": []}
        result, calls, resolver = self.append("@Emma Stone", [mention()], record=current)
        self.assertEqual(result["error"], {"code": "forbidden"})
        self.assertEqual(calls[0][2]["fresh_mention_error"], "forbidden")
        resolver.assert_not_called()

    def test_unknown_target_after_valid_owner_mention_still_rejects_whole_message(self):
        body = "@Owner Person @Unknown"
        spans = [mention(AUTHOR_ID, OWNER_LABEL), mention(OTHER_ID, "Unknown", start=14)]
        result, calls, _ = self.append(body, spans)
        self.assertEqual(result["error"], {"code": "forbidden"})
        self.assertEqual(calls[0][2]["requested_mentions"], spans)

    def test_cross_workspace_thread_cannot_reach_target_lookup_or_save(self):
        current = {**thread(), "workspaceId": "wsp_" + "x" * 22}
        result, calls, resolver = self.append("@Emma Stone", [mention()], record=current)
        self.assertEqual(result["error"], {"code": "forbidden"})
        self.assertEqual(calls, [])
        resolver.assert_not_called()

    def test_unavailable_or_malformed_target_authority_fails_closed_before_save(self):
        for member in ((None, "unavailable"), (membership(user_id=OTHER_ID), None),
                       (membership(reference="bad"), None), (membership(label=""), None)):
            with self.subTest(member=member):
                result, calls, _ = self.append("@Emma Stone", [mention()], member=member)
                self.assertEqual(result["error"], {"code": "storage_unavailable"})
                self.assertEqual(calls, [])

    def test_body_and_span_errors_are_deferred_but_not_stored_in_candidate(self):
        invalid = (
            {**mention(), "displayText": "@False Name"},
            {**mention(), "start": 1},
            {**mention(), "start": -1},
            {**mention(), "end": 0},
            {**mention(), "end": 99},
        )
        for span in invalid:
            with self.subTest(span=span):
                result, calls, _ = self.append("@Emma Stone", [span])
                self.assertEqual(result["error"], {"code": "invalid_request"})
                self.assertEqual(calls[0][2]["fresh_mention_error"], "invalid_request")
                self.assertEqual(calls[0][2]["requested_mentions"], [span])
                self.assertNotIn("mentions", calls[0][0]["messages"][-1])

    def test_changed_body_mismatching_metadata_reaches_existing_key_conflict(self):
        expected = mutations._owner_mutation_fingerprint(capability(), "@Emma Stone", "shared", [mention()])

        def conflict(_replacement, _expected, **kwargs):
            self.assertNotEqual(kwargs["fingerprint"], expected)
            self.assertEqual(kwargs["fresh_mention_error"], "invalid_request")
            return {"status": "conflict", "error": {"code": "idempotency_conflict"}}

        for span in ({**mention(), "displayText": "@Changed"}, {**mention(), "start": 1}):
            result, calls, _ = self.append("@Emma Stone", [span], saver=conflict)
            self.assertEqual(result["error"], {"code": "idempotency_conflict"})
            self.assertEqual(len(calls), 1)

    def test_matching_committed_retry_after_removal_and_resolve_recovers_original(self):
        original = {
            "id": "M" * 22, "authorKind": "owner", "authorDisplayName": OWNER_LABEL,
            "authorUserId": AUTHOR_ID, "text": "@Emma Stone", "visibility": "shared",
            "createdAt": MS + 1, "mentions": [mention()],
        }
        current = {**thread(), "state": "resolved", "updatedAt": MS + 2, "messages": [original]}

        def recover(_replacement, _expected, **kwargs):
            self.assertEqual(kwargs["fresh_mention_error"], "forbidden")
            return redis_store._V2OwnerAppendResult(original, MS + 1, True)

        result, calls, _ = self.append("@Emma Stone", [mention()], record=current,
                                      member=(None, "not_active"), saver=recover)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(result["message"]["id"], original["id"])
        self.assertEqual(result["updatedAt"], original["createdAt"])
        self.assertEqual(len(calls), 1)

    def test_legacy_empty_fingerprint_bytes_and_new_canonical_order(self):
        context = capability()
        canonical = {
            "action": "reply", "actorDisplayName": OWNER_LABEL, "actorKind": "owner",
            "actorUserId": AUTHOR_ID, "collaborationId": COLLABORATION_ID,
            "domain": "cuevion-collaboration-v2/owner-append-fingerprint-v1",
            "mailboxId": "mailbox-1", "mailboxProvider": "google", "ownerEmail": "owner@example.com",
            "text": "plain @text", "visibility": "shared", "workspaceId": WORKSPACE_ID,
        }
        legacy = hashlib.sha256(json.dumps(canonical, allow_nan=False, ensure_ascii=True,
            separators=(",", ":"), sort_keys=True).encode("utf-8")).hexdigest()
        self.assertEqual(mutations._owner_mutation_fingerprint(context, "plain @text", "shared"), legacy)
        self.assertEqual(mutations._owner_mutation_fingerprint(context, "plain @text", "shared", []), legacy)
        spans = [mention(), mention(start=12)]
        body = "@Emma Stone @Emma Stone"
        self.assertEqual(mutations._owner_mutation_fingerprint(context, body, "shared", spans),
                         mutations._owner_mutation_fingerprint(context, body, "shared", list(reversed(spans))))


class MentionApplicationTests(unittest.TestCase):
    def test_authenticated_app_forwards_canonical_mentions_and_retains_response(self):
        for participant in (False, True):
            for action, service in (("reply", application.append_v2_shared_message_for_verified_owner),
                                    ("internal_note", application.append_v2_internal_note_for_verified_owner)):
                visibility = "shared" if action == "reply" else "internal"
                context = capability(action, participant=participant)
                message = {"id": "M" * 22, "authorDisplayName": context.actor_display_name,
                           "authorRole": "Cuevion user", "authorUserId": context.actor_user_id,
                           "text": "@Emma Stone", "timestamp": MS + 1, "visibility": visibility,
                           "mentions": [mention()]}
                with self.subTest(participant=participant, action=action), patch.object(
                    application, "resolve_verified_owner_collaboration_context",
                    return_value={"status": "ok", "context": context, "error": None},
                ), patch.object(application, "_append_idempotent_v2_owner_message",
                    return_value={"status": "ok", "message": message, "updatedAt": MS + 1, "error": None},
                ) as append:
                    result = service(object(), (), COLLABORATION_ID,
                                     {"text": "@Emma Stone", "mentions": [mention()]},
                                     idempotency_key=IDEMPOTENCY_KEY, owner_security_configuration=object())
                self.assertEqual(result, {"message": message, "updatedAt": MS + 1})
                self.assertEqual(append.call_args.kwargs["mentions"], [mention()])

    def test_fresh_sender_authorization_denial_cannot_reach_retry_lookup(self):
        with patch.object(application, "resolve_verified_owner_collaboration_context",
                          return_value={"status": "forbidden", "error": {"code": "forbidden"}}), patch.object(
            application, "_append_idempotent_v2_owner_message",
        ) as append:
            result = application.append_v2_shared_message_for_verified_owner(
                object(), (), COLLABORATION_ID, {"text": "@Emma Stone", "mentions": [mention()]},
                idempotency_key=IDEMPOTENCY_KEY, owner_security_configuration=object(),
            )
        self.assertEqual(result["error"], {"code": "forbidden"})
        append.assert_not_called()

    def test_explicit_null_and_guest_identity_are_rejected_before_authority(self):
        for value in (None, "@Emma Stone", [{**mention(), "userId": "guest_123"}]):
            with self.subTest(value=value), patch.object(
                application, "resolve_verified_owner_collaboration_context",
            ) as authorize:
                result = application.append_v2_shared_message_for_verified_owner(
                    object(), (), COLLABORATION_ID, {"text": "@Emma Stone", "mentions": value},
                    idempotency_key=IDEMPOTENCY_KEY, owner_security_configuration=object(),
                )
            self.assertEqual(result["error"], {"code": "invalid_request"})
            authorize.assert_not_called()

    def test_authenticated_projection_preserves_historical_spans_guest_only_visible_text(self):
        messages = [{"id": char * 22, "authorKind": "owner", "authorDisplayName": OWNER_LABEL,
                     "authorUserId": AUTHOR_ID, "text": "@Emma Stone", "createdAt": MS,
                     "visibility": visibility, "mentions": [mention()]}
                    for char, visibility in (("M", "shared"), ("N", "internal"))]
        current = {**thread(), "messages": messages}
        for participant in (False, True):
            context = capability("read", participant=participant)
            dto, error = application._build_verified_thread_dto(
                current, context, team_member_resolver=lambda *_args: (None, "not_active"),
                **({"external_guests": []} if not participant else {}),
            )
            self.assertIsNone(error)
            self.assertEqual([entry["mentions"] for entry in dto["messages"]], [[mention()], [mention()]])
        guest = models.build_v2_guest_thread_dto(current)
        self.assertEqual(len(guest["messages"]), 1)
        self.assertEqual(guest["messages"][0]["text"], "@Emma Stone")
        self.assertNotIn("mentions", guest["messages"][0])
        self.assertNotIn("authorUserId", guest["messages"][0])
        self.assertNotIn(TEAM_ID, json.dumps(guest))


class MentionHttpTests(unittest.TestCase):
    def setUp(self):
        self.context = http_fixtures._context()
        context_patch = patch.object(owner_http, "_resolve_context", return_value=self.context)
        limiter_patch = patch.object(owner_rate_limit, "consume_owner_rate_limit",
                                    return_value=owner_rate_limit.OwnerRateLimitDecision("allowed"))
        context_patch.start()
        limiter_patch.start()
        self.addCleanup(context_patch.stop)
        self.addCleanup(limiter_patch.stop)
        config = http_fixtures.parse_owner_security_configuration(
            owner_http._trusted_security_snapshot(http_fixtures._environment()))
        self.csrf = http_fixtures.issue_owner_csrf_token(self.context, config, now=http_fixtures.NOW)[0]

    def test_both_authenticated_append_routes_forward_only_optional_mentions(self):
        for operation, name in (("append_shared", "append_v2_shared_message_for_verified_owner"),
                                ("append_internal", "append_v2_internal_note_for_verified_owner")):
            for spans in ([], [mention()], None):
                payload = {"operation": operation, "collaborationId": COLLABORATION_ID,
                           "text": "@Emma Stone", "mentions": spans}
                with self.subTest(operation=operation, spans=spans), patch.object(
                    owner_http.application, name, return_value={"message": {}, "updatedAt": MS},
                ) as service:
                    response = http_fixtures._invoke(http_fixtures._request(
                        payload, csrf=self.csrf, idempotency_key=IDEMPOTENCY_KEY))
                self.assertEqual(response.status, 200)
                self.assertEqual(service.call_args.args[3], {"text": "@Emma Stone", "mentions": spans})

    def test_nonappend_operation_cannot_smuggle_mentions(self):
        with patch.object(owner_http.application, "add_v2_participant_for_verified_owner") as service:
            response = http_fixtures._invoke(http_fixtures._request(
                {"operation": "add_participant", "collaborationId": COLLABORATION_ID,
                 "participantUserId": TEAM_ID, "mentions": [mention()]}, csrf=self.csrf))
        self.assertEqual(response.status, 400)
        service.assert_not_called()

    def test_integer_offsets_pass_real_application_and_bool_or_null_fail(self):
        body = "😀 @Emma Stone"
        span = mention(start=3)
        context = capability()
        message = {"id": "M" * 22, "authorDisplayName": OWNER_LABEL, "authorRole": "Cuevion user",
                   "authorUserId": AUTHOR_ID, "text": body, "timestamp": MS + 1,
                   "visibility": "shared", "mentions": [span]}
        for spans, status in (([span], 200), ([{**span, "start": True}], 400), (None, 400)):
            with self.subTest(spans=spans), patch.object(
                application, "resolve_verified_owner_collaboration_context",
                return_value={"status": "ok", "context": context, "error": None},
            ), patch.object(application, "_append_idempotent_v2_owner_message",
                return_value={"status": "ok", "message": message, "updatedAt": MS + 1, "error": None},
            ) as append:
                response = http_fixtures._invoke(http_fixtures._request(
                    {"operation": "append_shared", "collaborationId": COLLABORATION_ID,
                     "text": body, "mentions": spans}, csrf=self.csrf, idempotency_key=IDEMPOTENCY_KEY))
                self.assertEqual(response.status, status, response.body)
                if status == 200:
                    self.assertEqual(http_fixtures._json(response)["data"]["message"]["mentions"], [span])
                    self.assertEqual(append.call_args.kwargs["mentions"], [span])
                else:
                    append.assert_not_called()

    def test_numbers_are_rejected_outside_exact_append_offset_paths(self):
        base = {"operation": "append_shared", "collaborationId": COLLABORATION_ID,
                "text": "@Emma Stone", "mentions": [mention()]}
        invalid = [
            {**base, "text": 1}, {**base, "collaborationId": 1}, {**base, "operation": []},
            {**base, "operation": {}}, {"operation": "resolve", "collaborationId": COLLABORATION_ID,
                                       "expectedState": "needs_review", "expectedUpdatedAt": MS},
        ]
        invalid.extend({**base, "mentions": [{**mention(), key: value}]}
                       for key, value in (("start", 0.0), ("start", [0]), ("displayText", 1),
                                          ("userId", 1), ("start", float("inf"))))
        for payload in invalid:
            with self.subTest(payload=payload), patch.object(
                application, "append_v2_shared_message_for_verified_owner",
            ) as service:
                response = http_fixtures._invoke(http_fixtures._request(
                    payload, csrf=self.csrf, idempotency_key=IDEMPOTENCY_KEY))
                self.assertEqual(response.status, 400, response.body)
                service.assert_not_called()

    def test_invalid_signed_integer_spans_reach_fresh_guard_or_committed_conflict(self):
        for span in ({**mention(), "start": -1}, {**mention(), "end": 0},
                     {**mention(), "start": 1}, {**mention(), "displayText": "@Changed"}):
            for conflict, expected_status in ((False, 400), (True, 409)):
                saver = Mock(return_value=(
                    {"status": "conflict", "error": {"code": "idempotency_conflict"}} if conflict else
                    {"status": "error", "error": {"code": "invalid_request"}}))

                def append(context, text, **kwargs):
                    return mutations.append_owner_v2_message_idempotently(
                        context, text, visibility="shared", **kwargs,
                        thread_loader=lambda *_args, **_kwargs: redis_store._V2RecordResult(thread()),
                        thread_saver=saver,
                    )

                with self.subTest(span=span, conflict=conflict), patch.object(
                    application, "resolve_verified_owner_collaboration_context",
                    return_value={"status": "ok", "context": capability(), "error": None},
                ), patch.object(application, "_append_idempotent_v2_owner_message", side_effect=append), patch.object(
                    authorization, "_resolve_active_team_member", return_value=(membership(), None),
                ):
                    response = http_fixtures._invoke(http_fixtures._request(
                        {"operation": "append_shared", "collaborationId": COLLABORATION_ID,
                         "text": "@Emma Stone", "mentions": [span]},
                        csrf=self.csrf, idempotency_key=IDEMPOTENCY_KEY))
                    self.assertEqual(response.status, expected_status, response.body)
                    saver.assert_called_once()
                    self.assertEqual(saver.call_args.kwargs["requested_mentions"], [span])
                    self.assertEqual(saver.call_args.kwargs["fresh_mention_error"], "invalid_request")

    def test_numeric_exception_keeps_duplicate_constant_surrogate_and_utf8_rejection(self):
        body = json.dumps({"operation": "append_shared", "collaborationId": COLLABORATION_ID,
                           "text": "@Emma Stone", "mentions": [mention()]}, separators=(",", ":")).encode("utf-8")
        invalid = [
            body.replace(b'"start":0', b'"start":0,"start":1'),
            body.replace(b'"text":', b'"text":"duplicate","text":'),
            body.replace(b'"start":0', b'"start":NaN'),
            body.replace(b'"start":0', b'"start":1e999'),
            body.replace(b'"start":0', b'"start":' + b"9" * 5000),
            body.replace(b"@Emma Stone", b"\\ud800"),
            body.replace(b"@Emma Stone", b"\xff"),
        ]
        for raw in invalid:
            with self.subTest(raw_length=len(raw)), patch.object(
                application, "append_v2_shared_message_for_verified_owner",
            ) as service:
                request = http_fixtures._Request(raw, headers=[
                    ("Origin", http_fixtures.ORIGIN), ("Content-Type", "application/json"),
                    ("Content-Length", str(len(raw))), ("X-Cuevion-CSRF", self.csrf),
                    ("X-Cuevion-Idempotency-Key", IDEMPOTENCY_KEY),
                ])
                response = http_fixtures._invoke(request)
                self.assertEqual(response.status, 400, response.body)
                service.assert_not_called()

    def test_existing_body_cap_permits_maximum_text_plus_bounded_mention_metadata(self):
        body = "@Emma Stone" + "x" * (models.MAX_V2_MESSAGE_TEXT - len("@Emma Stone"))
        payload = {"operation": "append_shared", "collaborationId": COLLABORATION_ID,
                   "text": body, "mentions": [mention()]}
        self.assertLess(len(json.dumps(payload).encode("utf-8")), owner_http.MAX_OWNER_REQUEST_BYTES)
        with patch.object(application, "append_v2_shared_message_for_verified_owner",
                          return_value={"message": {}, "updatedAt": MS}) as service:
            response = http_fixtures._invoke(http_fixtures._request(
                payload, csrf=self.csrf, idempotency_key=IDEMPOTENCY_KEY))
        self.assertEqual(response.status, 200)
        service.assert_called_once()

    def test_guest_rejects_structured_mentions_and_preserves_plain_at_text(self):
        base = {"operation": "reply", "text": "@Emma Stone", "idempotencyKey": guest_fixtures.IDEMPOTENCY_KEY}
        for payload in ({**base, "mentions": [mention()]}, {**base, "text": 123}):
            with self.subTest(payload=payload), patch.object(
                guest_fixtures.guest_http.application, "append_v2_shared_reply_for_guest",
            ) as service:
                response = guest_fixtures._invoke(guest_fixtures._post(
                    payload, cookie=guest_fixtures._cookie(), csrf=guest_fixtures.CSRF_TOKEN))
                self.assertEqual(response.status, 400)
                service.assert_not_called()
        with patch.object(guest_fixtures.guest_rate_limit, "consume_guest_rate_limit",
                          return_value=guest_fixtures.guest_rate_limit.GuestRateLimitDecision("allowed")), patch.object(
            guest_fixtures.guest_http.application, "append_v2_shared_reply_for_guest",
            return_value={"status": "ok", "collaboration": guest_fixtures._collaboration(), "error": None},
        ) as service:
            response = guest_fixtures._invoke(guest_fixtures._post(
                base, cookie=guest_fixtures._cookie(), csrf=guest_fixtures.CSRF_TOKEN))
            self.assertEqual(response.status, 200, response.body)
            self.assertEqual(service.call_args.args[1], "@Emma Stone")


if __name__ == "__main__":
    unittest.main()
