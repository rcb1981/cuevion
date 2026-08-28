from __future__ import annotations

import json
import unittest
from dataclasses import replace

from . import candidate_store as candidate_module
from .authority import PriorityMessageIdentity
from .candidate_store import (
    CANDIDATE_ABSOLUTE_TTL_SECONDS,
    CANDIDATE_BASE_TTL_SECONDS,
    CANDIDATE_MAX_MAILBOX_RECORDS,
    CANDIDATE_MAX_PAGE_RECORDS,
    CANDIDATE_MAX_SERIALIZED_RECORD_BYTES,
    CANDIDATE_MAX_SNIPPET_BYTES,
    CANDIDATE_MAX_USER_RECORDS,
    CANDIDATE_PROVIDER_FAILURE_GRACE_SECONDS,
    CandidateCapacityExceeded,
    CandidateNamespaceInvalidated,
    CandidateReferenceRejected,
    CandidateStoreUnavailable,
    CandidateVersionConflict,
    PriorityCandidateConversation,
    PriorityCandidateMailboxScope,
    PriorityCandidateProviderAuthority,
    PriorityCandidateRender,
    PriorityCandidateRouting,
    PriorityCandidateScope,
    PriorityCandidateSnapshot,
    PriorityCandidateStore,
    derive_candidate_identity_digest,
    derive_candidate_mailbox_scope_digest,
    derive_candidate_scope_digest,
    derive_candidate_user_scope_digest,
)


SECRET = "priority-candidate-test-secret-with-more-than-thirty-two-bytes"
DAY_SECONDS = 24 * 60 * 60


class MemoryRedis:
    """Small command-boundary double for candidate-store scripts."""

    def __init__(self, *, current_ms: int = 1_800_000_000_000) -> None:
        self.current_ms = current_ms
        self.values: dict[str, str] = {}
        self.sorted_sets: dict[str, dict[str, int]] = {}
        self.expires_at: dict[str, int] = {}
        self.commands: list[list[object]] = []

    def advance(self, seconds: int) -> None:
        self.current_ms += seconds * 1_000

    def _expire(self) -> None:
        for key, expires_at in list(self.expires_at.items()):
            if expires_at <= self.current_ms:
                self.values.pop(key, None)
                self.expires_at.pop(key, None)

    def __call__(self, command: list[object]) -> dict[str, object]:
        self._expire()
        self.commands.append(list(command))
        operation = command[0]
        if operation == "GET":
            return {"result": self.values.get(command[1])}
        if operation == "MGET":
            return {"result": [self.values.get(key) for key in command[1:]]}
        if operation != "EVAL":
            raise AssertionError("unexpected candidate-store command")
        script = command[1]
        key_count = int(command[2])
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        if script == candidate_module._UPSERT_CONFIRMED_SCRIPT:
            return {"result": self._upsert(keys, args)}
        if script == candidate_module._SET_POSITIVE_REFERENCE_SCRIPT:
            return {"result": self._set_reference(keys, args)}
        if script == candidate_module._MARK_VALIDATION_FAILURE_SCRIPT:
            return {"result": self._mark_failure(keys, args)}
        if script == candidate_module._READ_ONE_SCRIPT:
            return {"result": self._read_one(keys, args)}
        if script == candidate_module._READ_MAILBOX_PAGE_SCRIPT:
            return {"result": self._read_page(keys, args)}
        if script == candidate_module._REMOVE_CANDIDATE_SCRIPT:
            return {"result": self._remove(keys, args)}
        if script == candidate_module._INVALIDATE_IMAP_NAMESPACE_SCRIPT:
            return {"result": self._invalidate_namespace(keys, args)}
        if script == candidate_module._CLEAR_INCOMPLETE_SCRIPT:
            value = self.values.get(keys[0])
            if value is None:
                return {"result": 0}
            if value != args[0]:
                return {"result": -1}
            self.values.pop(keys[0], None)
            return {"result": 1}
        raise AssertionError("unexpected candidate-store script")

    @staticmethod
    def _encode(payload: dict[str, object]) -> str:
        return json.dumps(
            payload,
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )

    def _set_record(
        self,
        keys: list[object],
        member: str,
        payload: dict[str, object],
        expires_at: int,
    ) -> str:
        encoded = self._encode(payload)
        self.values[keys[0]] = encoded
        self.expires_at[keys[0]] = expires_at
        for key in keys[1:4]:
            self.sorted_sets.setdefault(key, {})[member] = expires_at
        return encoded

    def _upsert(self, keys: list[object], args: list[object]) -> object:
        expected_raw, missing, expected_version, template, member = args[:5]
        if self.values.get(keys[6]) is not None:
            return args[18]
        current = self.values.get(keys[0])
        if expected_raw == missing:
            if current is not None:
                return args[15]
        elif current != expected_raw:
            return args[15]
        for key in keys[1:4]:
            values = self.sorted_sets.setdefault(key, {})
            for existing_member, score in list(values.items()):
                if score <= self.current_ms:
                    values.pop(existing_member)
        memberships = [member in self.sorted_sets[key] for key in keys[1:4]]
        if current is not None and memberships != [True, True, True]:
            return args[14]
        if current is not None and (
            len({self.sorted_sets[key][member] for key in keys[1:4]}) != 1
            or self.sorted_sets[keys[1]][member] != int(args[19])
        ):
            return args[14]
        if current is None and any(memberships):
            return args[14]
        if current is None:
            if len(self.sorted_sets[keys[1]]) >= int(args[9]):
                self.values[keys[4]] = args[13]
                return args[16]
            if len(self.sorted_sets[keys[2]]) >= int(args[10]):
                self.values[keys[5]] = args[13]
                return args[17]
        payload = json.loads(template)
        prior_version = json.loads(current)["version"] if current is not None else 0
        if prior_version != int(expected_version):
            return args[15]
        now = self.current_ms
        base = now + int(args[6]) * 1_000
        absolute = now + int(args[7]) * 1_000
        positive = 0
        for kind, expires_at in payload["positiveReferences"].items():
            expires_at = 0 if expires_at <= now else min(expires_at, absolute)
            payload["positiveReferences"][kind] = expires_at
            positive = max(positive, expires_at)
        payload.update(
            {
                "providerObservedAt": now,
                "providerValidatedAt": now,
                "baseExpiresAt": base,
                "absoluteExpiresAt": absolute,
                "graceExpiresAt": 0,
                "state": "provider_confirmed",
                "version": prior_version + 1,
                "updatedAt": now,
            }
        )
        expires_at = min(max(base, positive), absolute)
        if len(self._encode(payload).encode("ascii")) > int(args[11]):
            return args[14]
        return self._set_record(keys, member, payload, expires_at)

    def _set_reference(self, keys: list[object], args: list[object]) -> object:
        current = self.values.get(keys[0])
        if current is None:
            return args[11]
        if current != args[0]:
            return args[10]
        scores = [self.sorted_sets.get(key, {}).get(args[5]) for key in keys[1:4]]
        if (
            any(score is None for score in scores)
            or len(set(scores)) != 1
            or scores[0] != int(args[13])
        ):
            return args[9]
        payload = json.loads(current)
        if payload["version"] != int(args[1]):
            return args[10]
        remaining = int(args[3])
        if remaining > int(args[4]):
            return args[9]
        if remaining > 0 and (
            payload["state"] != "provider_confirmed"
            or self.current_ms >= payload["baseExpiresAt"]
        ):
            return args[12]
        absolute = payload["absoluteExpiresAt"]
        if self.current_ms >= absolute:
            return args[12]
        payload["positiveReferences"][args[2]] = (
            0
            if remaining == 0
            else min(self.current_ms + remaining * 1_000, absolute)
        )
        positive = 0
        for kind, expires_at in payload["positiveReferences"].items():
            if expires_at <= self.current_ms:
                payload["positiveReferences"][kind] = 0
            positive = max(positive, payload["positiveReferences"][kind])
        expires_at = min(max(payload["baseExpiresAt"], positive), absolute)
        if payload["state"] == "provider_validation_grace":
            expires_at = min(expires_at, payload["graceExpiresAt"])
        if expires_at <= self.current_ms:
            self._delete_record(keys, args[5])
            return args[11]
        payload["version"] += 1
        payload["updatedAt"] = self.current_ms
        return self._set_record(keys, args[5], payload, expires_at)

    def _mark_failure(self, keys: list[object], args: list[object]) -> object:
        current = self.values.get(keys[0])
        if current is None:
            return args[9]
        if current != args[0]:
            return args[8]
        scores = [self.sorted_sets.get(key, {}).get(args[2]) for key in keys[1:4]]
        if (
            any(score is None for score in scores)
            or len(set(scores)) != 1
            or scores[0] != int(args[10])
        ):
            return args[7]
        payload = json.loads(current)
        if payload["version"] != int(args[1]):
            return args[8]
        positive = max(payload["positiveReferences"].values())
        grace = payload["providerValidatedAt"] + int(args[3]) * 1_000
        expires_at = min(
            max(payload["baseExpiresAt"], positive),
            payload["absoluteExpiresAt"],
            grace,
        )
        if expires_at <= self.current_ms:
            self._delete_record(keys, args[2])
            return args[9]
        payload["state"] = "provider_validation_grace"
        payload["graceExpiresAt"] = grace
        payload["version"] += 1
        payload["updatedAt"] = self.current_ms
        return self._set_record(keys, args[2], payload, expires_at)

    def _read_one(self, keys: list[object], args: list[object]) -> list[object]:
        value = self.values.get(keys[0])
        scores = [self.sorted_sets.get(key, {}).get(args[0]) for key in keys[1:4]]
        if value is None:
            if all(score is None for score in scores) or (
                all(score is not None for score in scores)
                and all(int(score) <= self.current_ms for score in scores)
            ):
                return [self.current_ms, args[2]]
            return [args[1]]
        if any(score is None for score in scores) or len(set(scores)) != 1:
            return [args[1]]
        return [self.current_ms, value, str(scores[0])]

    def _read_page(self, keys: list[object], args: list[object]) -> list[object]:
        mailbox_marker = self.values.get(keys[1])
        user_marker = self.values.get(keys[2])
        if mailbox_marker not in {None, args[2]} or user_marker not in {None, args[2]}:
            return [args[3]]
        active = [
            (member, score)
            for member, score in self.sorted_sets.get(keys[0], {}).items()
            if score > self.current_ms
        ]
        active.sort(key=lambda item: (item[1], item[0]))
        offset = int(args[0])
        limit = int(args[1])
        selected = active[offset : offset + limit]
        result: list[object] = [
            self.current_ms,
            len(active),
            int(mailbox_marker is not None),
            int(user_marker is not None),
        ]
        for member, score in selected:
            result.extend((member, str(score)))
        return result

    def _delete_record(self, keys: list[object], member: str) -> None:
        self.values.pop(keys[0], None)
        self.expires_at.pop(keys[0], None)
        for key in keys[1:4]:
            self.sorted_sets.get(key, {}).pop(member, None)

    def _remove(self, keys: list[object], args: list[object]) -> int:
        value = self.values.get(keys[0])
        scores = [self.sorted_sets.get(key, {}).get(args[0]) for key in keys[1:4]]
        if value is None and all(score is None for score in scores):
            return 0
        if value is None and all(
            score is not None and score <= self.current_ms for score in scores
        ):
            self._delete_record(keys, args[0])
            return 0
        if value is None or any(score is None for score in scores):
            return -1
        if len(set(scores)) != 1:
            return -1
        self._delete_record(keys, args[0])
        return 1

    def _invalidate_namespace(
        self, keys: list[object], args: list[object]
    ) -> int:
        namespace = self.sorted_sets.get(keys[2], {})
        if len(namespace) > int(args[1]):
            self.values[keys[4]] = args[2]
            self.values[keys[5]] = args[2]
            return -1
        members = list(namespace)
        for member in members:
            record_key = args[0] + member
            scores = (
                self.sorted_sets.get(keys[0], {}).get(member),
                self.sorted_sets.get(keys[1], {}).get(member),
                namespace.get(member),
            )
            if record_key in self.values:
                if any(score is None for score in scores) or len(set(scores)) != 1:
                    return -1
            elif any(
                score is not None and score > self.current_ms for score in scores
            ):
                return -1
        for member in members:
            record_key = args[0] + member
            self.values.pop(record_key, None)
            self.expires_at.pop(record_key, None)
            self.sorted_sets.get(keys[0], {}).pop(member, None)
            self.sorted_sets.get(keys[1], {}).pop(member, None)
        self.sorted_sets.pop(keys[2], None)
        self.values[keys[3]] = args[2]
        return len(members)


def google_scope(
    *,
    message_id: str = "gmail-message-1",
    workspace_id: str = "workspace-a",
    user_id: str = "user-a",
    mailbox_id: str = "mailbox-a",
    account: str = "owner@example.test",
) -> PriorityCandidateScope:
    return PriorityCandidateScope(
        workspace_id=workspace_id,
        user_id=user_id,
        mailbox_id=mailbox_id,
        mailbox_account_identity=account,
        provider="google",
        identity=PriorityMessageIdentity(
            provider="google", provider_message_id=message_id
        ),
    )


def imap_scope(
    *,
    uid: str = "41",
    uid_validity: str = "7",
    mailbox_id: str = "mailbox-imap",
) -> PriorityCandidateScope:
    return PriorityCandidateScope(
        workspace_id="workspace-a",
        user_id="user-a",
        mailbox_id=mailbox_id,
        mailbox_account_identity="imap-owner@example.test",
        provider="custom_imap",
        identity=PriorityMessageIdentity(
            provider="custom_imap",
            provider_folder="INBOX",
            uid_validity=uid_validity,
            imap_uid=uid,
        ),
    )


def ready_routing() -> PriorityCandidateRouting:
    return PriorityCandidateRouting(
        signal=None,
        ui_signal="REPLY",
        internal_classification="reply",
        category="reply",
        final_visibility=None,
        action=None,
        v7_final_priority=None,
        noise_disposition="none",
        noise_confidence="low",
        noise_reasons=(),
        classifier_version="test-classifier-v1",
        routing_version="test-routing-v1",
    )


def snapshot(
    *,
    provider: str = "google",
    snippet: str = "bounded preview",
    routing_state: str = "unresolved",
    routing: PriorityCandidateRouting | None = None,
) -> PriorityCandidateSnapshot:
    return PriorityCandidateSnapshot(
        conversation=PriorityCandidateConversation(
            conversation_id="conversation-1",
            authority_kind="gmail" if provider == "google" else "rfc",
            provider_thread_id="thread-1" if provider == "google" else None,
            rfc_root_message_id=None if provider == "google" else "<root@example.test>",
            rfc_message_id=None if provider == "google" else "<message@example.test>",
        ),
        render=PriorityCandidateRender(
            sender_display="Sender",
            sender_address="sender@example.test",
            subject="Candidate subject",
            snippet=snippet,
            created_at="2027-01-15T08:00:00.000Z",
            unread=True,
            flagged=False,
        ),
        routing_state=routing_state,
        routing=routing,
        provider_authority=PriorityCandidateProviderAuthority(
            folder="INBOX",
            labels=("INBOX", "IMPORTANT") if provider == "google" else (),
        ),
    )


class CandidateIdentityAndCodecTests(unittest.TestCase):
    def test_routing_state_invariant_and_ready_round_trip(self) -> None:
        unresolved = snapshot()
        self.assertEqual(unresolved.routing_state, "unresolved")
        self.assertIsNone(unresolved.routing)

        complete_routing = ready_routing()
        with self.assertRaises(ValueError):
            replace(unresolved, routing=complete_routing)
        with self.assertRaises(ValueError):
            replace(unresolved, routing_state="ready")

        ready = replace(
            unresolved,
            routing_state="ready",
            routing=complete_routing,
        )
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        record = store.upsert_confirmed(google_scope(), ready, expected_version=0)
        self.assertEqual(record.snapshot, ready)
        raw = next(value for key, value in redis.values.items() if ":record:" in key)
        payload = json.loads(raw)
        for unknown_field in ("unexpected", "priorityScore"):
            malformed = json.loads(raw)
            malformed["routing"][unknown_field] = 0
            self.assertIsNone(
                candidate_module._decode_candidate_record(
                    json.dumps(malformed),
                    secret=SECRET,
                    expected_mailbox_scope=record.scope.mailbox_scope(),
                    expected_member_digest=derive_candidate_scope_digest(
                        SECRET, record.scope
                    ),
                )
            )
        self.assertNotIn("priorityScore", payload["routing"])

    def test_ready_routing_uses_only_strict_server_domains(self) -> None:
        routing = ready_routing()
        self.assertIsNone(routing.signal)
        self.assertIsNone(routing.v7_final_priority)
        self.assertIsNone(routing.final_visibility)
        self.assertIsNone(routing.action)

        for signal in (None, "Priority", "For review"):
            self.assertEqual(replace(routing, signal=signal).signal, signal)
        for final_priority in (None, "PRIORITY", "REVIEW", "NORMAL", "LOW"):
            self.assertEqual(
                replace(routing, v7_final_priority=final_priority).v7_final_priority,
                final_priority,
            )
        for visibility in (
            None,
            "show_priority",
            "show_normal",
            "show_low",
            "hide",
            "delete",
        ):
            self.assertEqual(
                replace(routing, final_visibility=visibility).final_visibility,
                visibility,
            )
        for action in (
            None,
            "show_in_priority",
            "show_in_main_feed",
            "show_in_quiet_view",
            "archive_candidate",
            "delete_or_archive",
        ):
            self.assertEqual(replace(routing, action=action).action, action)
        for confidence in ("low", "medium", "high"):
            self.assertEqual(
                replace(routing, noise_confidence=confidence).noise_confidence,
                confidence,
            )

        for invalid_priority in ("MEDIUM", "priority", False, 0):
            with self.assertRaises(ValueError):
                replace(routing, v7_final_priority=invalid_priority)
        for invalid_confidence in (0, 0.5, 1, "unknown"):
            with self.assertRaises(ValueError):
                replace(routing, noise_confidence=invalid_confidence)
        for invalid_category in ("Primary", "Promo", "Updates"):
            with self.assertRaises(ValueError):
                replace(routing, category=invalid_category)
        for field_name in ("classifier_version", "routing_version"):
            for invalid_version in ("", "unknown", "fake", "fallback"):
                with self.assertRaises(ValueError):
                    replace(routing, **{field_name: invalid_version})
        with self.assertRaises(ValueError):
            replace(routing, signal="normal")
        with self.assertRaises(ValueError):
            replace(routing, noise_reasons=("unknown_reason",))
        with self.assertRaises(TypeError):
            PriorityCandidateRouting(
                **{
                    **{
                        field_name: getattr(routing, field_name)
                        for field_name in routing.__dataclass_fields__
                    },
                    "priority_score": 0,
                }
            )

    def test_v2_schema_namespace_and_v1_isolation(self) -> None:
        self.assertEqual(candidate_module.CANDIDATE_STORE_SCHEMA_VERSION, 2)
        self.assertEqual(
            candidate_module._CANDIDATE_KEY_PREFIX,
            "cuevion:priority:candidate:v2:",
        )

        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope()
        store.upsert_confirmed(scope, snapshot(), expected_version=0)
        record_key = store._scope_keys(scope)["record"]
        self.assertTrue(record_key.startswith("cuevion:priority:candidate:v2:"))
        payload = json.loads(redis.values[record_key])
        self.assertEqual(payload["schemaVersion"], 2)
        v1_payload = dict(payload)
        v1_payload["schemaVersion"] = 1
        self.assertIsNone(
            candidate_module._decode_candidate_record(
                json.dumps(v1_payload),
                secret=SECRET,
                expected_mailbox_scope=scope.mailbox_scope(),
                expected_member_digest=derive_candidate_scope_digest(SECRET, scope),
            )
        )

        isolated_redis = MemoryRedis()
        isolated_store = PriorityCandidateStore(isolated_redis, hmac_secret=SECRET)
        v1_key = record_key.replace(
            "cuevion:priority:candidate:v2:",
            "cuevion:priority:candidate:v1:",
        )
        isolated_redis.values[v1_key] = json.dumps(v1_payload)
        self.assertIsNone(isolated_store.read_candidate(scope))
        self.assertIn(v1_key, isolated_redis.values)
        accessed_keys = " ".join(
            str(value)
            for command in isolated_redis.commands
            for value in command[3 : 3 + int(command[2])]
            if command[0] == "EVAL"
        )
        self.assertNotIn("cuevion:priority:candidate:v1:", accessed_keys)

    def test_canonical_identities_and_all_scope_isolation(self) -> None:
        gmail = google_scope()
        imap = imap_scope()
        gmail.canonical_bytes()
        imap.canonical_bytes()
        with self.assertRaises(ValueError):
            PriorityMessageIdentity(provider="google", provider_message_id="")
        with self.assertRaises(ValueError):
            PriorityMessageIdentity(
                provider="custom_imap",
                provider_folder="INBOX",
                uid_validity="0",
                imap_uid="1",
            )
        variants = (
            replace(gmail, workspace_id="workspace-b"),
            replace(gmail, user_id="user-b"),
            replace(gmail, mailbox_id="mailbox-b"),
            replace(gmail, mailbox_account_identity="other@example.test"),
            imap,
        )
        digest = derive_candidate_scope_digest(SECRET, gmail)
        self.assertEqual(
            len(
                {
                    digest,
                    *(
                        derive_candidate_scope_digest(SECRET, item)
                        for item in variants
                    ),
                }
            ),
            6,
        )
        self.assertNotEqual(
            derive_candidate_mailbox_scope_digest(SECRET, gmail.mailbox_scope()),
            derive_candidate_mailbox_scope_digest(SECRET, variants[2].mailbox_scope()),
        )
        self.assertNotEqual(
            derive_candidate_user_scope_digest(SECRET, gmail.mailbox_scope()),
            derive_candidate_user_scope_digest(SECRET, variants[0].mailbox_scope()),
        )
        self.assertNotEqual(
            digest, derive_candidate_identity_digest(SECRET, gmail)
        )

    def test_round_trip_keys_and_record_exclude_prohibited_content(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope()
        record = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        self.assertEqual(store.read_candidate(scope), record)
        raw = next(value for key, value in redis.values.items() if ":record:" in key)
        payload = json.loads(raw)
        self.assertEqual(set(payload), candidate_module._ROOT_FIELDS)
        self.assertEqual(payload["routingState"], "unresolved")
        self.assertIsNone(payload["routing"])
        self.assertLessEqual(
            len(raw.encode("ascii")), CANDIDATE_MAX_SERIALIZED_RECORD_BYTES
        )
        serialized_keys = set()

        def collect_keys(value: object) -> None:
            if isinstance(value, dict):
                serialized_keys.update(value)
                for child in value.values():
                    collect_keys(child)
            elif isinstance(value, list):
                for child in value:
                    collect_keys(child)

        collect_keys(payload)
        self.assertTrue(
            {"body", "bodyText", "html", "attachments"}.isdisjoint(serialized_keys)
        )
        for command in redis.commands:
            if command[0] not in {"GET", "MGET", "EVAL"}:
                continue
            key_count = int(command[2]) if command[0] == "EVAL" else len(command) - 1
            keys = command[3 : 3 + key_count] if command[0] == "EVAL" else command[1:]
            key_text = " ".join(str(key) for key in keys)
            for raw_identity in (
                scope.mailbox_account_identity,
                scope.identity.provider_message_id,
                record.snapshot.render.subject,
                record.snapshot.render.sender_address,
            ):
                self.assertNotIn(raw_identity, key_text)

    def test_strict_codec_rejects_unknown_missing_prohibited_and_corrupt(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope()
        store.upsert_confirmed(scope, snapshot(), expected_version=0)
        raw = next(value for key, value in redis.values.items() if ":record:" in key)
        payload = json.loads(raw)
        cases: list[str] = []
        unknown = dict(payload)
        unknown["unknown"] = True
        cases.append(json.dumps(unknown))
        missing = dict(payload)
        missing.pop("render")
        cases.append(json.dumps(missing))
        prohibited = dict(payload)
        prohibited["bodyText"] = "never"
        cases.append(json.dumps(prohibited))
        wrong_schema = dict(payload)
        wrong_schema["schemaVersion"] = 1
        cases.append(json.dumps(wrong_schema))
        cases.extend(("{", '{"schemaVersion":2,"schemaVersion":2}'))
        for case in cases:
            decoded = candidate_module._decode_candidate_record(
                case,
                secret=SECRET,
                expected_mailbox_scope=scope.mailbox_scope(),
                expected_member_digest=derive_candidate_scope_digest(SECRET, scope),
            )
            self.assertIsNone(decoded)
        content = "top-secret-subject"
        redis.values[next(key for key in redis.values if ":record:" in key)] = content
        with self.assertRaises(CandidateStoreUnavailable) as raised:
            store.read_candidate(scope)
        self.assertNotIn(content, str(raised.exception))

    def test_snippet_and_serialized_record_bounds_reject_without_truncation(self) -> None:
        accepted = "é" * (CANDIDATE_MAX_SNIPPET_BYTES // 2)
        self.assertEqual(
            PriorityCandidateRender(
                sender_display="",
                sender_address="",
                subject="",
                snippet=accepted,
                created_at="2027-01-01T00:00:00Z",
                unread=False,
                flagged=False,
            ).snippet,
            accepted,
        )
        with self.assertRaises(ValueError):
            replace(snapshot().render, snippet=accepted + "a")
        with self.assertRaises(ValueError):
            replace(ready_routing(), routing_version="r" * 129)
        large_routing = replace(
            ready_routing(),
            noise_reasons=candidate_module._ROUTING_NOISE_REASON_VALUES,
            classifier_version="c" * 128,
            routing_version="r" * 128,
        )
        large_snapshot = replace(
            snapshot(),
            conversation=replace(
                snapshot().conversation,
                conversation_id="c" * 1_024,
                authority_kind="a" * 64,
                provider_thread_id="t" * 256,
            ),
            render=replace(
                snapshot().render,
                sender_display="d" * 256,
                sender_address="a" * 320,
                subject="s" * 998,
                snippet="p" * CANDIDATE_MAX_SNIPPET_BYTES,
            ),
            routing_state="ready",
            routing=large_routing,
        )
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        with self.assertRaises(ValueError):
            store.upsert_confirmed(google_scope(), large_snapshot, expected_version=0)
        self.assertFalse(any(command[0] == "EVAL" for command in redis.commands))
        self.assertEqual(CANDIDATE_MAX_SERIALIZED_RECORD_BYTES, 4_096)

        ready_redis = MemoryRedis()
        ready_store = PriorityCandidateStore(ready_redis, hmac_secret=SECRET)
        ready_store.upsert_confirmed(
            google_scope(),
            snapshot(routing_state="ready", routing=ready_routing()),
            expected_version=0,
        )
        ready_raw = next(
            value for key, value in ready_redis.values.items() if ":record:" in key
        )
        self.assertLessEqual(
            len(ready_raw.encode("ascii")), CANDIDATE_MAX_SERIALIZED_RECORD_BYTES
        )


class CandidateIndexAndTimeTests(unittest.TestCase):
    def test_deterministic_deduplicated_bounded_pages_and_missing_record_failure(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scopes = tuple(google_scope(message_id=f"message-{index}") for index in range(3))
        records = [
            store.upsert_confirmed(scope, snapshot(), expected_version=0)
            for scope in scopes
        ]
        page_one = store.read_mailbox_page(scopes[0].mailbox_scope(), limit=2)
        page_two = store.read_mailbox_page(
            scopes[0].mailbox_scope(), offset=2, limit=2
        )
        expected = sorted(
            records, key=lambda record: derive_candidate_scope_digest(SECRET, record.scope)
        )
        self.assertEqual(page_one.records + page_two.records, tuple(expected))
        self.assertEqual(page_one.total, 3)
        updated = store.upsert_confirmed(scopes[0], snapshot(), expected_version=1)
        self.assertEqual(updated.version, 2)
        self.assertEqual(store.read_mailbox_page(scopes[0].mailbox_scope()).total, 3)
        updated_keys = store._scope_keys(scopes[0])
        redis.sorted_sets[updated_keys["mailbox_index"]][updated_keys["member"]] += 1
        with self.assertRaises(CandidateStoreUnavailable):
            store.upsert_confirmed(scopes[0], snapshot(), expected_version=2)
        redis.sorted_sets[updated_keys["mailbox_index"]][updated_keys["member"]] -= 1
        with self.assertRaises(ValueError):
            store.read_mailbox_page(
                scopes[0].mailbox_scope(), limit=CANDIDATE_MAX_PAGE_RECORDS + 1
            )
        record_key = next(
            key
            for key in redis.values
            if key.endswith(derive_candidate_scope_digest(SECRET, scopes[1]))
        )
        redis.values.pop(record_key)
        with self.assertRaises(CandidateStoreUnavailable):
            store.read_mailbox_page(scopes[0].mailbox_scope())
        self.assertFalse(any(command[0] == "SCAN" for command in redis.commands))

    def test_mailbox_and_user_caps_mark_incomplete_without_eviction(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        existing_scope = google_scope(message_id="existing")
        existing = store.upsert_confirmed(
            existing_scope, snapshot(), expected_version=0
        )
        existing = store.set_positive_reference(
            existing_scope,
            reference_kind="manual_priority",
            remaining_lifetime_seconds=CANDIDATE_ABSOLUTE_TTL_SECONDS,
            expected_version=existing.version,
        )
        assert existing is not None
        keys = store._scope_keys(existing_scope)
        mailbox_index = redis.sorted_sets[keys["mailbox_index"]]
        for index in range(CANDIDATE_MAX_MAILBOX_RECORDS - 1):
            mailbox_index[f"{index:064x}"] = redis.current_ms + DAY_SECONDS * 1_000
        with self.assertRaises(CandidateCapacityExceeded) as raised:
            store.upsert_confirmed(
                google_scope(message_id="overflow"), snapshot(), expected_version=0
            )
        self.assertEqual(raised.exception.scope_kind, "mailbox")
        self.assertEqual(store.read_candidate(existing_scope), existing)
        mailbox_index.clear()
        mailbox_index[keys["member"]] = existing.logical_expires_at()
        page = store.read_mailbox_page(existing_scope.mailbox_scope(), limit=1)
        self.assertTrue(page.mailbox_incomplete)
        self.assertTrue(store.clear_mailbox_incomplete(existing_scope.mailbox_scope()))

        user_redis = MemoryRedis()
        user_store = PriorityCandidateStore(user_redis, hmac_secret=SECRET)
        scope = google_scope()
        user_keys = user_store._scope_keys(scope)
        user_redis.sorted_sets[user_keys["user_index"]] = {
            f"{index:064x}": user_redis.current_ms + DAY_SECONDS * 1_000
            for index in range(CANDIDATE_MAX_USER_RECORDS)
        }
        with self.assertRaises(CandidateCapacityExceeded) as user_raised:
            user_store.upsert_confirmed(scope, snapshot(), expected_version=0)
        self.assertEqual(user_raised.exception.scope_kind, "user")
        self.assertTrue(user_store.clear_user_incomplete(scope.mailbox_scope()))

    def test_redis_time_and_monotonic_versions_reject_stale_writes(self) -> None:
        redis = MemoryRedis(current_ms=1_900_000_000_123)
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope()
        first = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        self.assertEqual(first.version, 1)
        self.assertEqual(first.provider_observed_at, redis.current_ms)
        self.assertEqual(first.provider_validated_at, redis.current_ms)
        self.assertEqual(first.updated_at, redis.current_ms)
        self.assertNotEqual(first.snapshot.render.created_at, str(redis.current_ms))
        record_key = store._scope_keys(scope)["record"]
        physical_expiry = redis.expires_at[record_key]
        self.assertEqual(store.read_candidate(scope), first)
        self.assertEqual(redis.expires_at[record_key], physical_expiry)
        redis.advance(5)
        with self.assertRaises(CandidateVersionConflict):
            store.upsert_confirmed(scope, snapshot(), expected_version=0)
        second = store.upsert_confirmed(scope, snapshot(), expected_version=1)
        self.assertEqual(second.version, 2)
        self.assertEqual(second.updated_at, redis.current_ms)
        self.assertGreater(second.updated_at, first.updated_at)


class CandidateRetentionAndInvalidationTests(unittest.TestCase):
    def test_base_positive_reference_bounds_absolute_cap_and_no_negative_reference(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        base_scope = google_scope(message_id="base")
        base = store.upsert_confirmed(base_scope, snapshot(), expected_version=0)
        self.assertEqual(
            base.logical_expires_at(),
            redis.current_ms + CANDIDATE_BASE_TTL_SECONDS * 1_000,
        )
        redis.advance(CANDIDATE_BASE_TTL_SECONDS)
        self.assertIsNone(store.read_candidate(base_scope))

        for kind, lifetime in (("waiting", 14), ("semantic_promotion", 30)):
            case_redis = MemoryRedis()
            case_store = PriorityCandidateStore(case_redis, hmac_secret=SECRET)
            scope = google_scope(message_id=kind)
            record = case_store.upsert_confirmed(scope, snapshot(), expected_version=0)
            case_redis.advance(29 * DAY_SECONDS)
            extended = case_store.set_positive_reference(
                scope,
                reference_kind=kind,
                remaining_lifetime_seconds=lifetime * DAY_SECONDS,
                expected_version=record.version,
            )
            assert extended is not None
            self.assertEqual(
                extended.positive_reference_expires_at(kind),
                case_redis.current_ms + lifetime * DAY_SECONDS * 1_000,
            )
            self.assertLessEqual(
                extended.logical_expires_at(),
                extended.provider_observed_at
                + CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000,
            )

        manual_redis = MemoryRedis()
        manual_store = PriorityCandidateStore(manual_redis, hmac_secret=SECRET)
        manual_scope = google_scope(message_id="manual")
        manual = manual_store.upsert_confirmed(
            manual_scope, snapshot(), expected_version=0
        )
        manual_redis.advance(29 * DAY_SECONDS)
        manual = manual_store.set_positive_reference(
            manual_scope,
            reference_kind="manual_priority",
            remaining_lifetime_seconds=CANDIDATE_ABSOLUTE_TTL_SECONDS,
            expected_version=manual.version,
        )
        assert manual is not None
        self.assertEqual(manual.logical_expires_at(), manual.absolute_expires_at)
        command_count = len(manual_redis.commands)
        for negative in ("removed", "cleared", "dismissed"):
            with self.assertRaises(ValueError):
                manual_store.set_positive_reference(
                    manual_scope,
                    reference_kind=negative,
                    remaining_lifetime_seconds=DAY_SECONDS,
                    expected_version=manual.version,
                )
        self.assertEqual(len(manual_redis.commands), command_count)

    def test_provider_failure_grace_is_degraded_bounded_and_not_extendable(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope(message_id="grace")
        record = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        redis.advance(DAY_SECONDS)
        grace = store.mark_provider_validation_failure(
            scope, expected_version=record.version
        )
        assert grace is not None
        self.assertEqual(grace.state, "provider_validation_grace")
        self.assertEqual(
            grace.grace_expires_at,
            grace.provider_validated_at
            + CANDIDATE_PROVIDER_FAILURE_GRACE_SECONDS * 1_000,
        )
        self.assertTrue(store.read_mailbox_page(scope.mailbox_scope()).incomplete)
        with self.assertRaises(CandidateReferenceRejected):
            store.set_positive_reference(
                scope,
                reference_kind="manual_priority",
                remaining_lifetime_seconds=DAY_SECONDS,
                expected_version=grace.version,
            )
        redis.advance(6 * DAY_SECONDS)
        self.assertIsNone(store.read_candidate(scope))

    def test_single_and_bounded_imap_namespace_invalidation(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        first = imap_scope(uid="41", uid_validity="7")
        second = imap_scope(uid="42", uid_validity="7")
        unrelated = imap_scope(uid="43", uid_validity="8")
        for scope in (first, second, unrelated):
            store.upsert_confirmed(
                scope, snapshot(provider="custom_imap"), expected_version=0
            )
        self.assertTrue(store.remove_candidate(first))
        self.assertFalse(store.remove_candidate(first))
        store.upsert_confirmed(first, snapshot(provider="custom_imap"), expected_version=0)
        count = store.invalidate_imap_namespace(
            first.mailbox_scope(), provider_folder="INBOX", uid_validity="7"
        )
        self.assertEqual(count, 2)
        self.assertIsNone(store.read_candidate(first))
        self.assertIsNone(store.read_candidate(second))
        self.assertIsNotNone(store.read_candidate(unrelated))
        with self.assertRaises(CandidateNamespaceInvalidated):
            store.upsert_confirmed(
                first, snapshot(provider="custom_imap"), expected_version=0
            )
        self.assertFalse(any(command[0] == "SCAN" for command in redis.commands))


class CandidateDormancyTests(unittest.TestCase):
    def test_import_and_construction_do_not_mutate_storage(self) -> None:
        redis = MemoryRedis()
        PriorityCandidateStore(redis, hmac_secret=SECRET)
        self.assertEqual(redis.commands, [])
        self.assertEqual(redis.values, {})
        self.assertEqual(redis.sorted_sets, {})


if __name__ == "__main__":
    unittest.main()
