from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

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
                self.sorted_sets.pop(key, None)
                self.expires_at.pop(key, None)

    def _key_type(self, key: object) -> str:
        if key in self.values:
            return "string"
        if key in self.sorted_sets:
            return "zset"
        return "none"

    def __call__(self, command: list[object]) -> dict[str, object]:
        self._expire()
        self.commands.append(list(command))
        operation = command[0]
        if operation == "GET":
            return {"result": self.values.get(command[1])}
        if operation == "MGET":
            return {"result": [self.values.get(key) for key in command[1:]]}
        if operation == "EXISTS":
            return {
                "result": sum(
                    self._key_type(key) != "none" for key in command[1:]
                )
            }
        if operation == "TYPE":
            return {"result": self._key_type(command[1])}
        if operation == "TIME":
            return {
                "result": [
                    str(self.current_ms // 1_000),
                    str((self.current_ms % 1_000) * 1_000),
                ]
            }
        if operation == "ZMSCORE":
            if command[1] in self.values:
                raise AssertionError("wrong type for candidate-store ZMSCORE")
            members = self.sorted_sets.get(command[1], {})
            return {
                "result": [
                    None if member not in members else str(members[member])
                    for member in command[2:]
                ]
            }
        if operation != "EVAL":
            raise AssertionError("unexpected candidate-store command")
        script = command[1]
        key_count = int(command[2])
        keys = command[3 : 3 + key_count]
        args = command[3 + key_count :]
        if script == candidate_module._PREPARE_CONFIRMED_SCRIPT:
            return {"result": self._prepare(args)}
        if script == candidate_module._PREPARE_WORKFLOW_REFERENCES_SCRIPT:
            return {"result": self._prepare_workflow_references(args)}
        if script == candidate_module._UPSERT_CONFIRMED_SCRIPT:
            return {"result": self._commit(keys, args)}
        if script == candidate_module._RECONCILE_WORKFLOW_REFERENCES_SCRIPT:
            return {"result": self._reconcile_workflow_references(keys, args)}
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
        if script == candidate_module._BATCH_CONFIRM_UNCHANGED_SCRIPT:
            return {"result": self._batch_confirm_unchanged(keys, args)}
        if script == candidate_module._CLEAR_INCOMPLETE_SCRIPT:
            value = self.values.get(keys[0])
            if value is None:
                return {"result": 0}
            if value != args[0]:
                return {"result": -1}
            self.values.pop(keys[0], None)
            return {"result": 1}
        raise AssertionError("unexpected candidate-store script")

    def _batch_confirm_unchanged(
        self,
        keys: list[object],
        args: list[object],
    ) -> object:
        structural = (
            args[8]
            if len(args) > 8
            else candidate_module._BATCH_CONFIRMATION_STRUCTURAL_INVALID_SENTINEL
        )
        try:
            count = int(args[0])
            observed_at = int(args[1])
            maximum_bytes = int(args[2])
            maximum = int(args[3])
            base_seconds = int(args[4])
            absolute_seconds = int(args[5])
            index_ttl = int(args[6])
            incomplete_value = args[7]
            refresh_indexes = args[9]
        except (IndexError, TypeError, ValueError):
            return structural
        if (
            not 1 <= count <= candidate_module.CANDIDATE_CONFIRMATION_COMMIT_CHUNK_SIZE
            or len(keys) != count * 8
            or len(args) != 10 + count * 12
            or observed_at < 0
            or maximum_bytes < 1
            or maximum < 0
            or base_seconds < 1
            or absolute_seconds < base_seconds
            or index_ttl < 1
            or incomplete_value != candidate_module._INCOMPLETE_VALUE
            or refresh_indexes not in {0, 1}
        ):
            return structural

        statuses = [True] * count
        prepared_values: list[str | None] = [None] * count
        logical_expiries = [0] * count
        for index in range(count):
            offset = 10 + index * 12
            expected_digest = args[offset]
            expected_length = args[offset + 1]
            prepared = args[offset + 2]
            expected_expiry = args[offset + 4]
            expected_version = args[offset + 5]
            increment = args[offset + 6]
            expected_workflow = args[offset + 7]
            marker_values = args[offset + 8 : offset + 11]
            workflow_deadline = args[offset + 11]

            current = self.values.get(keys[index])
            if (
                type(current) is not str
                or type(expected_digest) is not str
                or type(expected_length) is not int
                or len(current.encode("utf-8")) != expected_length
                or hashlib.sha1(
                    current.encode("utf-8"),
                    usedforsecurity=False,
                ).hexdigest()
                != expected_digest
            ):
                statuses[index] = False

            workflow_key = keys[count + index]
            if expected_workflow == "":
                if self._key_type(workflow_key) != "none":
                    statuses[index] = False
            elif self.values.get(workflow_key) != expected_workflow:
                statuses[index] = False

            marker_start = 2 * count + index * 3
            for marker_key, expected_marker in zip(
                keys[marker_start : marker_start + 3],
                marker_values,
                strict=True,
            ):
                if expected_marker == "":
                    if self._key_type(marker_key) != "none":
                        return structural
                elif (
                    expected_marker != incomplete_value
                    or self.values.get(marker_key) != expected_marker
                ):
                    return structural

            if not statuses[index]:
                continue
            if self.current_ms < observed_at or self.current_ms > maximum:
                return structural
            try:
                old_payload = json.loads(current)
                next_payload = json.loads(prepared)
            except Exception:
                return structural
            if (
                type(old_payload) is not dict
                or type(next_payload) is not dict
                or type(expected_expiry) is not int
                or type(expected_version) is not int
                or increment not in {1, 2}
                or type(workflow_deadline) is not int
                or workflow_deadline < 0
                or workflow_deadline > maximum
                or old_payload.get("version") != expected_version
                or next_payload.get("version") != expected_version + increment
                or next_payload.get("scopeDigest")
                != old_payload.get("scopeDigest")
                or next_payload.get("identityDigest")
                != old_payload.get("identityDigest")
                or next_payload.get("providerObservedAt") != observed_at
                or next_payload.get("providerValidatedAt") != observed_at
                or next_payload.get("updatedAt") != observed_at
                or next_payload.get("baseExpiresAt")
                != observed_at + base_seconds * 1_000
                or next_payload.get("absoluteExpiresAt")
                != observed_at + absolute_seconds * 1_000
                or next_payload.get("graceExpiresAt") != 0
                or next_payload.get("state") != "provider_confirmed"
                or len(prepared.encode("utf-8")) > maximum_bytes
                or (increment == 1 and expected_workflow != "")
                or (increment == 2 and expected_workflow == "")
            ):
                return structural
            if (
                expected_expiry <= self.current_ms
                or (
                    workflow_deadline > 0
                    and self.current_ms >= workflow_deadline
                )
            ):
                statuses[index] = False
                continue
            references = next_payload.get("positiveReferences")
            if (
                type(references) is not dict
                or set(references) != set(candidate_module._POSITIVE_REFERENCE_KINDS)
                or any(
                    type(expires_at) is not int
                    or expires_at < 0
                    or expires_at > next_payload["absoluteExpiresAt"]
                    for expires_at in references.values()
                )
            ):
                return structural
            logical_expiry = min(
                max(
                    next_payload["baseExpiresAt"],
                    max(references.values()),
                ),
                next_payload["absoluteExpiresAt"],
            )
            if logical_expiry <= self.current_ms:
                statuses[index] = False
                continue
            if increment == 2:
                next_payload["updatedAt"] = self.current_ms
                prepared = self._encode(next_payload)
            if len(prepared.encode("utf-8")) > maximum_bytes:
                return structural
            prepared_values[index] = prepared
            logical_expiries[index] = logical_expiry

        for index, status in enumerate(statuses):
            if not status:
                continue
            offset = 10 + index * 12
            member = args[offset + 3]
            expected_expiry = args[offset + 4]
            index_start = 5 * count + index * 3
            for index_key in keys[index_start : index_start + 3]:
                score = self.sorted_sets.get(index_key, {}).get(member)
                if score != expected_expiry:
                    statuses[index] = False
                    break

        for index, status in enumerate(statuses):
            if not status:
                continue
            offset = 10 + index * 12
            member = args[offset + 3]
            prepared = prepared_values[index]
            assert prepared is not None
            expiry = logical_expiries[index]
            record_key = keys[index]
            self.values[record_key] = prepared
            ttl_seconds = (expiry - self.current_ms + 999) // 1_000
            self.expires_at[record_key] = self.current_ms + ttl_seconds * 1_000
            index_start = 5 * count + index * 3
            for index_key in keys[index_start : index_start + 3]:
                self.sorted_sets.setdefault(index_key, {})[member] = expiry
                if refresh_indexes == 1:
                    self.expires_at[index_key] = (
                        self.current_ms + index_ttl * 1_000
                    )
        return [self.current_ms, *(1 if status else 0 for status in statuses)]

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

    @staticmethod
    def _collection_failure(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return "provider_authority_shape"
        provider_authority = payload.get("providerAuthority")
        if (
            not isinstance(provider_authority, dict)
            or set(provider_authority) != {"folder", "labels"}
        ):
            return "provider_authority_shape"
        provider_labels = provider_authority["labels"]
        if provider_labels is not None and not (
            isinstance(provider_labels, list)
            and bool(provider_labels)
            and all(type(label) is str for label in provider_labels)
        ):
            return "labels_collection"
        routing_state = payload.get("routingState")
        if routing_state == "unresolved":
            return (
                None
                if payload.get("routing") is None
                else "unresolved_routing_null"
            )
        if routing_state != "ready":
            return "routing_state"
        routing = payload.get("routing")
        if (
            not isinstance(routing, dict)
            or set(routing) != candidate_module._ROUTING_FIELDS
        ):
            return "ready_routing_shape"
        noise_reasons = routing["noiseReasons"]
        if noise_reasons is None or (
            isinstance(noise_reasons, list)
            and bool(noise_reasons)
            and all(type(reason) is str for reason in noise_reasons)
        ):
            return None
        return "noise_reasons_collection"

    @classmethod
    def _canonical_collections(cls, payload: object) -> bool:
        return cls._collection_failure(payload) is None

    def _prepare(self, args: list[object]) -> object:
        expected_version, base_seconds, absolute_seconds, maximum = args[:4]
        if (
            type(expected_version) is not int
            or expected_version < 0
            or expected_version >= int(maximum)
        ):
            return args[11]
        reference_values = args[4:10]
        if any(
            type(expires_at) is not int
            or expires_at < 0
            or expires_at > int(maximum)
            for expires_at in reference_values
        ):
            return args[10]
        now = self.current_ms
        base = now + int(base_seconds) * 1_000
        absolute = now + int(absolute_seconds) * 1_000
        if base > int(maximum) or absolute > int(maximum):
            return args[11]
        normalized = [
            0 if expires_at <= now else min(expires_at, absolute)
            for expires_at in reference_values
        ]
        return [now, base, absolute, expected_version + 1, *normalized]

    def _set_encoded_record(
        self,
        keys: list[object],
        member: str,
        encoded: str,
        expires_at: int,
    ) -> str:
        self.values[keys[0]] = encoded
        self.expires_at[keys[0]] = expires_at
        for key in keys[1:4]:
            self.sorted_sets.setdefault(key, {})[member] = expires_at
        return encoded

    def _prepare_workflow_references(self, args: list[object]) -> object:
        expected_version = args[0]
        state = args[1]
        base = args[2]
        absolute = args[3]
        maximum = args[10]
        if (
            type(expected_version) is not int
            or expected_version < 1
            or expected_version >= int(maximum)
            or type(base) is not int
            or type(absolute) is not int
            or base < 0
            or absolute < base
        ):
            return args[12]
        requested = args[4:7]
        reference_maximums = args[7:10]
        if any(
            type(expires_at) is not int
            or expires_at < 0
            or expires_at > int(maximum)
            for expires_at in requested
        ) or any(
            type(seconds) is not int or seconds < 1
            for seconds in reference_maximums
        ):
            return args[11]
        normalized = [
            0
            if expires_at <= self.current_ms
            else min(
                expires_at,
                absolute,
                self.current_ms + int(seconds) * 1_000,
            )
            for expires_at, seconds in zip(
                requested,
                reference_maximums,
                strict=True,
            )
        ]
        if any(normalized) and (
            state != "provider_confirmed" or self.current_ms >= base
        ):
            return args[13]
        return [self.current_ms, expected_version + 1, *normalized]

    def _reconcile_workflow_references(
        self,
        keys: list[object],
        args: list[object],
    ) -> object:
        current = self.values.get(keys[0])
        if current is None:
            return args[14]
        if current != args[0]:
            return args[13]
        member = args[8]
        scores = [self.sorted_sets.get(key, {}).get(member) for key in keys[1:4]]
        if (
            any(score is None for score in scores)
            or len(set(scores)) != 1
            or scores[0] != int(args[16])
        ):
            return args[18]
        prepared = args[2]
        try:
            old_payload = json.loads(current)
            payload = json.loads(prepared)
        except Exception:
            return args[17]
        old_application = dict(old_payload)
        new_application = dict(payload)
        for application in (old_application, new_application):
            application.pop("version", None)
            application.pop("updatedAt", None)
            application.pop("positiveReferences", None)
        workflow_kinds = candidate_module._WORKFLOW_POSITIVE_REFERENCE_KINDS
        non_workflow_kinds = tuple(
            kind
            for kind in candidate_module._POSITIVE_REFERENCE_KINDS
            if kind not in workflow_kinds
        )
        if (
            old_application != new_application
            or not self._canonical_collections(old_payload)
            or not self._canonical_collections(payload)
            or set(old_payload.get("positiveReferences", {}))
            != set(candidate_module._POSITIVE_REFERENCE_KINDS)
            or set(payload.get("positiveReferences", {}))
            != set(candidate_module._POSITIVE_REFERENCE_KINDS)
            or old_payload.get("version") != int(args[1])
            or payload.get("version") != int(args[3])
            or payload.get("version") != old_payload.get("version") + 1
            or payload.get("updatedAt") != int(args[4])
            or any(
                payload["positiveReferences"][kind]
                != old_payload["positiveReferences"][kind]
                for kind in non_workflow_kinds
            )
            or tuple(
                payload["positiveReferences"][kind]
                for kind in workflow_kinds
            )
            != tuple(int(value) for value in args[5:8])
        ):
            return args[17]
        if len(prepared.encode("utf-8")) > int(args[10]):
            return args[17]
        for index, kind in enumerate(workflow_kinds):
            expires_at = payload["positiveReferences"][kind]
            reference_maximum = int(args[21 + index])
            if expires_at > 0 and (
                expires_at <= self.current_ms
                or expires_at > payload["absoluteExpiresAt"]
                or expires_at > self.current_ms + reference_maximum * 1_000
                or payload["state"] != "provider_confirmed"
                or self.current_ms >= payload["baseExpiresAt"]
            ):
                return args[15]
        positive = max(payload["positiveReferences"].values())
        expires_at = min(
            max(payload["baseExpiresAt"], positive),
            payload["absoluteExpiresAt"],
        )
        if payload["state"] == "provider_validation_grace":
            expires_at = min(expires_at, payload["graceExpiresAt"])
        if expires_at <= self.current_ms:
            self._delete_record(keys, member)
            return args[14]
        return self._set_encoded_record(keys, member, prepared, expires_at)

    def _commit(self, keys: list[object], args: list[object]) -> object:
        mode, expected_digest, expected_length, prepared, member = args[:5]
        if self.values.get(keys[6]) is not None:
            return args[18]
        current = self.values.get(keys[0])
        if expected_digest == "":
            if current is not None:
                return args[15]
        elif (
            current is None
            or len(current.encode("utf-8")) != int(expected_length)
            or hashlib.sha1(
                current.encode("utf-8"),
                usedforsecurity=False,
            ).hexdigest()
            != expected_digest
        ):
            return args[15]
        if mode == "repair":
            try:
                old_references = json.loads(current)["positiveReferences"]
            except Exception:
                return args[20]
            if set(old_references) != set(candidate_module._POSITIVE_REFERENCE_KINDS) or any(
                type(value) is not int or value != 0
                for value in old_references.values()
            ):
                return args[21]
        payload = json.loads(prepared)
        if (
            not self._canonical_collections(payload)
            or payload["version"] != int(args[5])
            or payload["state"] != "provider_confirmed"
        ):
            return args[22]
        for key in keys[1:4]:
            values = self.sorted_sets.setdefault(key, {})
            for existing_member, score in list(values.items()):
                if score <= self.current_ms:
                    values.pop(existing_member)
        memberships = [member in self.sorted_sets[key] for key in keys[1:4]]
        if mode == "normal" and current is not None and memberships != [True, True, True]:
            return args[23]
        if mode == "normal" and current is not None and (
            len({self.sorted_sets[key][member] for key in keys[1:4]}) != 1
            or self.sorted_sets[keys[1]][member] != int(args[19])
        ):
            return args[23]
        if mode == "normal" and current is None and any(memberships):
            return args[23]
        if not memberships[0]:
            if len(self.sorted_sets[keys[1]]) >= int(args[9]):
                self.values[keys[4]] = args[13]
                return args[16]
        if not memberships[1]:
            if len(self.sorted_sets[keys[2]]) >= int(args[10]):
                self.values[keys[5]] = args[13]
                return args[17]
        expires_at = min(
            max(payload["baseExpiresAt"], max(payload["positiveReferences"].values())),
            payload["absoluteExpiresAt"],
        )
        if len(prepared.encode("utf-8")) > int(args[11]):
            return args[22]
        if expires_at <= self.current_ms:
            return args[24]
        return self._set_encoded_record(keys, member, prepared, expires_at)

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


def imap_v2_snapshot(*, authority_kind: str = "rfc") -> PriorityCandidateSnapshot:
    intended = snapshot(provider="custom_imap")
    if authority_kind == "rfc":
        conversation = replace(
            intended.conversation,
            conversation_id="imap:v2:rfc:mailbox-imap:root%40example.test",
            authority_kind="rfc",
            rfc_root_message_id="root@example.test",
            rfc_message_id="message@example.test",
        )
    else:
        conversation = replace(
            intended.conversation,
            conversation_id="imap:v2:uid:mailbox-imap:INBOX:7:41",
            authority_kind="imap_uid",
            rfc_root_message_id=None,
            rfc_message_id=None,
        )
    return replace(intended, conversation=conversation)


class CandidateIdentityAndCodecTests(unittest.TestCase):
    def test_read_and_upsert_failures_expose_only_fixed_stages(self) -> None:
        scope = google_scope()

        def assert_stage(
            store: PriorityCandidateStore,
            operation,
            expected_stage: str,
        ) -> None:
            with self.assertRaises(CandidateStoreUnavailable) as raised:
                operation(store)
            self.assertEqual(raised.exception.stage, expected_stage)
            self.assertEqual(
                str(raised.exception),
                "Priority candidate storage is unavailable",
            )

        sensitive_transport_error = (
            "https://redis-sensitive.invalid token-sensitive subject-sensitive"
        )
        read_timeout = PriorityCandidateStore(
            lambda _command: (_ for _ in ()).throw(
                TimeoutError(sensitive_transport_error)
            ),
            hmac_secret=SECRET,
        )
        assert_stage(
            read_timeout,
            lambda store: store.read_candidate(scope),
            "store_read_transport",
        )

        malformed_read = PriorityCandidateStore(
            lambda _command: {"result": "malformed-sensitive-result"},
            hmac_secret=SECRET,
        )
        assert_stage(
            malformed_read,
            lambda store: store.read_candidate(scope),
            "store_read_result_invalid",
        )

        corrupt_existing = PriorityCandidateStore(
            lambda _command: {
                "result": [
                    1_800_000_000_000,
                    "corrupt-subject-sensitive",
                    1_800_000_001_000,
                ]
            },
            hmac_secret=SECRET,
        )
        assert_stage(
            corrupt_existing,
            lambda store: store.read_candidate(scope),
            "store_existing_record_invalid",
        )

        inconsistent_redis = MemoryRedis()
        inconsistent_store = PriorityCandidateStore(
            inconsistent_redis,
            hmac_secret=SECRET,
        )
        record = inconsistent_store.upsert_confirmed(
            scope,
            snapshot(),
            expected_version=0,
        )
        keys = inconsistent_store._scope_keys(scope)
        inconsistent_redis.sorted_sets[keys["mailbox_index"]][keys["member"]] = (
            record.logical_expires_at() + 1
        )
        assert_stage(
            inconsistent_store,
            lambda store: store.read_candidate(scope),
            "store_read_postcondition_invalid",
        )

        class UpsertTransportFailure(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    raise TimeoutError(sensitive_transport_error)
                return super().__call__(command)

        upsert_timeout = PriorityCandidateStore(
            UpsertTransportFailure(),
            hmac_secret=SECRET,
        )
        assert_stage(
            upsert_timeout,
            lambda store: store.upsert_confirmed(
                scope,
                snapshot(),
                expected_version=0,
            ),
            "store_upsert_transport",
        )

        class RejectedScript(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {
                        "result": candidate_module._COMMIT_KEY_OR_MARKER_INVALID_SENTINEL
                    }
                return super().__call__(command)

        rejected_script = PriorityCandidateStore(
            RejectedScript(),
            hmac_secret=SECRET,
        )
        assert_stage(
            rejected_script,
            lambda store: store.upsert_confirmed(
                scope,
                snapshot(),
                expected_version=0,
            ),
            "store_commit_key_or_marker_invalid",
        )

        class MalformedUpsertResult(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {"result": ["malformed-sensitive-result"]}
                return super().__call__(command)

        malformed_upsert_redis = MalformedUpsertResult()
        malformed_upsert = PriorityCandidateStore(
            malformed_upsert_redis,
            hmac_secret=SECRET,
        )
        assert_stage(
            malformed_upsert,
            lambda store: store.upsert_confirmed(
                scope,
                snapshot(),
                expected_version=0,
            ),
            "store_commit_ack_invalid",
        )
        self.assertEqual(
            sum(
                command[0] == "EVAL"
                and command[1] == candidate_module._READ_ONE_SCRIPT
                for command in malformed_upsert_redis.commands
            ),
            1,
        )

        class InvalidUpsertPostcondition(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                    and isinstance(result.get("result"), str)
                ):
                    payload = json.loads(result["result"])
                    payload["version"] += 1
                    return {"result": self._encode(payload)}
                return result

        invalid_postcondition_redis = InvalidUpsertPostcondition()
        invalid_postcondition = PriorityCandidateStore(
            invalid_postcondition_redis,
            hmac_secret=SECRET,
        )
        reconciled = invalid_postcondition.upsert_confirmed(
            scope,
            snapshot(),
            expected_version=0,
        )
        self.assertEqual(reconciled, invalid_postcondition.read_candidate(scope))

        for sensitive in (
            "redis-sensitive",
            "token-sensitive",
            "subject-sensitive",
            "corrupt-subject-sensitive",
            "malformed-sensitive-result",
        ):
            self.assertNotIn(sensitive, str(CandidateStoreUnavailable()))
            self.assertNotIn(sensitive, repr(CandidateStoreUnavailable()))
        bounded = CandidateStoreUnavailable("sensitive-arbitrary-stage")
        self.assertEqual(bounded.stage, "store_unexpected")
        self.assertNotIn("sensitive-arbitrary-stage", str(bounded))
        self.assertNotIn("sensitive-arbitrary-stage", repr(bounded))

    def test_prepare_metadata_failures_are_fixed_and_content_free(self) -> None:
        scope = google_scope(message_id="sensitive-provider-message-id")
        intended = replace(
            snapshot(),
            render=replace(
                snapshot().render,
                sender_display="Sensitive Sender",
                sender_address="sensitive-sender@example.test",
                subject="Sensitive Subject",
                snippet="Sensitive Snippet",
            ),
        )

        class PreparedResult(MemoryRedis):
            def __init__(self, mutate) -> None:
                super().__init__()
                self.mutate = mutate

            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._PREPARE_CONFIRMED_SCRIPT
                    and type(result.get("result")) is list
                ):
                    return {"result": self.mutate(result["result"])}
                return result

        def changed(index: int, replacement):
            def mutate(metadata: list[object]) -> list[object]:
                result = list(metadata)
                result[index] = replacement(result[index])
                return result

            return mutate

        cases = (
            (lambda metadata: metadata[:-1], "store_prepare_metadata_invalid"),
            (changed(0, lambda _value: 0.5), "store_prepare_metadata_invalid"),
            (changed(1, lambda value: value + 1), "store_prepare_temporal_invalid"),
            (changed(3, lambda value: value + 1), "store_prepare_temporal_invalid"),
            (changed(4, lambda _value: 1), "store_prepare_reference_invalid"),
        )
        for mutate, expected_stage in cases:
            with self.subTest(stage=expected_stage):
                store = PriorityCandidateStore(
                    PreparedResult(mutate),
                    hmac_secret=SECRET,
                )
                with self.assertRaises(CandidateStoreUnavailable) as raised:
                    store.upsert_confirmed(scope, intended, expected_version=0)
                self.assertEqual(raised.exception.stage, expected_stage)
                output = " ".join(
                    (
                        raised.exception.stage,
                        str(raised.exception),
                        repr(raised.exception),
                    )
                )
                for sensitive in (
                    "sensitive-provider-message-id",
                    "Sensitive Sender",
                    "sensitive-sender@example.test",
                    "Sensitive Subject",
                    "Sensitive Snippet",
                ):
                    self.assertNotIn(sensitive, output)

        redis = MemoryRedis()
        accepted = PriorityCandidateStore(redis, hmac_secret=SECRET).upsert_confirmed(
            scope,
            intended,
            expected_version=0,
        )
        self.assertIs(type(accepted.provider_observed_at), int)
        prepare_command = next(
            command
            for command in redis.commands
            if command[0] == "EVAL"
            and command[1] == candidate_module._PREPARE_CONFIRMED_SCRIPT
        )
        self.assertNotIn("cjson", candidate_module._PREPARE_CONFIRMED_SCRIPT)
        self.assertEqual(len(prepare_command[3:]), 12)
        prepare_boundary = repr(prepare_command[3:])
        for sensitive in (
            "sensitive-provider-message-id",
            "Sensitive Sender",
            "sensitive-sender@example.test",
            "Sensitive Subject",
            "Sensitive Snippet",
        ):
            self.assertNotIn(sensitive, prepare_boundary)
        raw = redis.values[
            PriorityCandidateStore(redis, hmac_secret=SECRET)._scope_keys(scope)[
                "record"
            ]
        ]
        self.assertEqual(
            raw,
            candidate_module._encode_wire(
                candidate_module._record_to_wire(SECRET, accepted)
            ),
        )

        original = candidate_module._record_to_wire

        def different_canonical(secret: str, record) -> dict[str, object]:
            payload = original(secret, record)
            payload["render"]["subject"] = "Different canonical subject"
            return payload

        with patch.object(
            candidate_module,
            "_record_to_wire",
            side_effect=different_canonical,
        ):
            with self.assertRaises(CandidateStoreUnavailable) as canonical:
                PriorityCandidateStore(
                    MemoryRedis(),
                    hmac_secret=SECRET,
                ).upsert_confirmed(scope, intended, expected_version=0)
        self.assertEqual(
            canonical.exception.stage,
            "store_prepare_canonical_invalid",
        )

    def test_divergent_lua_json_rules_cannot_change_application_wire(self) -> None:
        scope = imap_scope(uid="707")
        intended = snapshot(provider="custom_imap")

        class DivergentLuaTransport(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._PREPARE_CONFIRMED_SCRIPT
                ):
                    self.assert_prepare_is_metadata_only(command)
                return super().__call__(command)

            @staticmethod
            def assert_prepare_is_metadata_only(command: list[object]) -> None:
                if len(command[3:]) != 12 or any(
                    type(value) not in {int, str} for value in command[3:]
                ):
                    raise AssertionError("prepare accepted application JSON")
                if any(
                    type(value) is str and ("{" in value or "[" in value)
                    for value in command[3:]
                ):
                    raise AssertionError("prepare accepted application JSON")

        def divergent_json_value(value):
            if isinstance(value, dict):
                return {
                    key: divergent_json_value(item)
                    for key, item in value.items()
                    if item is not None
                }
            if isinstance(value, list):
                return [divergent_json_value(item) for item in value]
            return value

        redis = DivergentLuaTransport()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        written = store.upsert_confirmed(scope, intended, expected_version=0)
        expected = candidate_module._encode_wire(
            candidate_module._record_to_wire(SECRET, written)
        )
        raw = redis.values[store._scope_keys(scope)["record"]]
        divergent = json.dumps(
            divergent_json_value(json.loads(expected)),
            allow_nan=False,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        self.assertNotEqual(divergent, expected)
        self.assertEqual(raw, expected)
        payload = json.loads(raw)
        self.assertIsNone(payload["providerAuthority"]["labels"])
        self.assertIsNone(payload["routing"])
        self.assertIsNone(payload["conversation"]["providerThreadId"])

    def test_lua_sentinels_map_one_to_one_without_merging(self) -> None:
        scope = google_scope(message_id="sentinel-stage")
        prepare_cases = (
            (
                candidate_module._PREPARE_REFERENCE_INVALID_SENTINEL,
                "store_prepare_reference_invalid",
            ),
            (
                candidate_module._PREPARE_TEMPORAL_INVALID_SENTINEL,
                "store_prepare_temporal_invalid",
            ),
        )

        class PrepareSentinel(MemoryRedis):
            def __init__(self, sentinel: str) -> None:
                super().__init__()
                self.sentinel = sentinel

            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._PREPARE_CONFIRMED_SCRIPT
                ):
                    return {"result": self.sentinel}
                return super().__call__(command)

        for sentinel, expected_stage in prepare_cases:
            with self.subTest(stage=expected_stage):
                with self.assertRaises(CandidateStoreUnavailable) as raised:
                    PriorityCandidateStore(
                        PrepareSentinel(sentinel),
                        hmac_secret=SECRET,
                    ).upsert_confirmed(scope, snapshot(), expected_version=0)
                self.assertEqual(raised.exception.stage, expected_stage)

        commit_cases = (
            (
                candidate_module._COMMIT_KEY_OR_MARKER_INVALID_SENTINEL,
                "store_commit_key_or_marker_invalid",
            ),
            (
                candidate_module._REPAIR_SOURCE_INVALID_SENTINEL,
                "store_repair_source_invalid",
            ),
            (
                candidate_module._REPAIR_REFERENCE_PROOF_INVALID_SENTINEL,
                "store_repair_reference_proof_invalid",
            ),
            (
                candidate_module._COMMIT_PREPARED_INVALID_SENTINEL,
                "store_commit_prepared_invalid",
            ),
            (
                candidate_module._COMMIT_INDEX_INVALID_SENTINEL,
                "store_commit_index_invalid",
            ),
            (
                candidate_module._COMMIT_EXPIRY_INVALID_SENTINEL,
                "store_commit_expiry_invalid",
            ),
        )

        class CommitSentinel(MemoryRedis):
            def __init__(self, sentinel: str) -> None:
                super().__init__()
                self.sentinel = sentinel

            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {"result": self.sentinel}
                return super().__call__(command)

        for sentinel, expected_stage in commit_cases:
            with self.subTest(stage=expected_stage):
                with self.assertRaises(CandidateStoreUnavailable) as raised:
                    PriorityCandidateStore(
                        CommitSentinel(sentinel),
                        hmac_secret=SECRET,
                    ).upsert_confirmed(scope, snapshot(), expected_version=0)
                self.assertEqual(raised.exception.stage, expected_stage)

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
        self.assertIsNone(payload["routing"]["noiseReasons"])
        malformed_empty_reasons = json.loads(raw)
        malformed_empty_reasons["routing"]["noiseReasons"] = []
        self.assertIsNone(
            candidate_module._decode_candidate_record(
                json.dumps(malformed_empty_reasons),
                secret=SECRET,
                expected_mailbox_scope=record.scope.mailbox_scope(),
                expected_member_digest=derive_candidate_scope_digest(
                    SECRET, record.scope
                ),
            )
        )
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

        imap_redis = MemoryRedis()
        imap_store = PriorityCandidateStore(imap_redis, hmac_secret=SECRET)
        imap_candidate_scope = imap_scope()
        imap_record = imap_store.upsert_confirmed(
            imap_candidate_scope,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        imap_raw = imap_redis.values[
            imap_store._scope_keys(imap_candidate_scope)["record"]
        ]
        imap_payload = json.loads(imap_raw)
        self.assertIsNone(imap_payload["providerAuthority"]["labels"])
        imap_payload["providerAuthority"]["labels"] = []
        self.assertIsNone(
            candidate_module._decode_candidate_record(
                json.dumps(imap_payload),
                secret=SECRET,
                expected_mailbox_scope=imap_candidate_scope.mailbox_scope(),
                expected_member_digest=derive_candidate_scope_digest(
                    SECRET, imap_record.scope
                ),
            )
        )

    def test_ambiguous_commit_acknowledgement_reconciles_once(self) -> None:
        scope = google_scope(message_id="ack-reconcile")
        intended = snapshot()

        class AmbiguousAfterCommit(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {"unexpected": "content-free"}
                return result

        redis = AmbiguousAfterCommit()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        accepted = store.upsert_confirmed(scope, intended, expected_version=0)
        self.assertEqual(
            sum(
                command[0] == "EVAL"
                and command[1] == candidate_module._READ_ONE_SCRIPT
                for command in redis.commands
            ),
            1,
        )
        self.assertEqual(accepted, store.read_candidate(scope))
        commit_commands = [
            command
            for command in redis.commands
            if command[0] == "EVAL"
            and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
        ]
        self.assertEqual(len(commit_commands), 1)

        class TransportLostAfterCommit(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    raise TimeoutError("content-free")
                return result

        transport_redis = TransportLostAfterCommit()
        transport_store = PriorityCandidateStore(
            transport_redis,
            hmac_secret=SECRET,
        )
        transport_accepted = transport_store.upsert_confirmed(
            google_scope(message_id="transport-reconcile"),
            intended,
            expected_version=0,
        )
        self.assertEqual(transport_accepted.version, 1)

    def test_ambiguous_acknowledgement_fails_without_exact_matching_commit(self) -> None:
        scope = google_scope(message_id="ack-no-commit")

        class NoCommit(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {"unexpected": "content-free"}
                return super().__call__(command)

        with self.assertRaises(CandidateStoreUnavailable) as missing:
            PriorityCandidateStore(NoCommit(), hmac_secret=SECRET).upsert_confirmed(
                scope,
                snapshot(),
                expected_version=0,
            )
        self.assertEqual(missing.exception.stage, "store_upsert_result_invalid")

        class DifferentCommit(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    keys = command[3:10]
                    payload = json.loads(self.values[keys[0]])
                    payload["render"]["subject"] = "Different current snapshot"
                    self.values[keys[0]] = self._encode(payload)
                    return {"unexpected": "content-free"}
                return result

        with self.assertRaises(CandidateStoreUnavailable) as different:
            PriorityCandidateStore(
                DifferentCommit(), hmac_secret=SECRET
            ).upsert_confirmed(
                google_scope(message_id="ack-different"),
                snapshot(),
                expected_version=0,
            )
        self.assertEqual(different.exception.stage, "store_upsert_result_invalid")

        class DifferentVersion(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    keys = command[3:10]
                    payload = json.loads(self.values[keys[0]])
                    payload["version"] += 1
                    self.values[keys[0]] = self._encode(payload)
                    return {"unexpected": "content-free"}
                return result

        with self.assertRaises(CandidateStoreUnavailable) as different_version:
            PriorityCandidateStore(
                DifferentVersion(), hmac_secret=SECRET
            ).upsert_confirmed(
                google_scope(message_id="ack-different-version"),
                snapshot(),
                expected_version=0,
            )
        self.assertEqual(
            different_version.exception.stage,
            "store_upsert_result_invalid",
        )

        class ReconciliationReadFails(MemoryRedis):
            def __call__(self, command: list[object]) -> dict[str, object]:
                result = super().__call__(command)
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                ):
                    return {"unexpected": "content-free"}
                if (
                    command[0] == "EVAL"
                    and command[1] == candidate_module._READ_ONE_SCRIPT
                ):
                    raise TimeoutError("content-free")
                return result

        with self.assertRaises(CandidateStoreUnavailable) as read_failure:
            PriorityCandidateStore(
                ReconciliationReadFails(), hmac_secret=SECRET
            ).upsert_confirmed(
                google_scope(message_id="ack-read-failure"),
                snapshot(),
                expected_version=0,
            )
        self.assertEqual(read_failure.exception.stage, "store_upsert_result_invalid")

    def test_invalid_canonical_collection_is_rejected_before_commit(self) -> None:
        scope = google_scope(message_id="precommit-invalid")
        ready = snapshot(routing_state="ready", routing=ready_routing())
        original = candidate_module._routing_to_wire

        def ambiguous(value):
            result = original(value)
            assert result is not None
            result["noiseReasons"] = []
            return result

        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        with patch.object(candidate_module, "_routing_to_wire", side_effect=ambiguous):
            with self.assertRaises(CandidateStoreUnavailable) as raised:
                store.upsert_confirmed(scope, ready, expected_version=0)
        self.assertEqual(raised.exception.stage, "store_prepare_canonical_invalid")
        keys = store._scope_keys(scope)
        self.assertNotIn(keys["record"], redis.values)
        for index_key in (
            keys["mailbox_index"],
            keys["user_index"],
            keys["namespace_index"],
        ):
            self.assertNotIn(keys["member"], redis.sorted_sets.get(index_key, {}))
        self.assertFalse(
            any(
                command[0] == "EVAL"
                and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                for command in redis.commands
            )
        )

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
        with self.assertRaises(CandidateStoreUnavailable) as oversized:
            store.upsert_confirmed(google_scope(), large_snapshot, expected_version=0)
        self.assertEqual(oversized.exception.stage, "store_prepare_size_invalid")
        self.assertEqual(
            sum(
                command[0] == "EVAL"
                and command[1] == candidate_module._PREPARE_CONFIRMED_SCRIPT
                for command in redis.commands
            ),
            1,
        )
        self.assertFalse(
            any(
                command[0] == "EVAL"
                and command[1] == candidate_module._UPSERT_CONFIRMED_SCRIPT
                for command in redis.commands
            )
        )
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
    def test_atomic_workflow_reference_reconciliation_preserves_other_authorities(
        self,
    ) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        scope = google_scope(message_id="workflow-atomic")
        record = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        for kind, lifetime in (
            ("semantic_promotion", 3 * DAY_SECONDS),
            ("collaboration_priority", 4 * DAY_SECONDS),
            ("assigned_review", 5 * DAY_SECONDS),
        ):
            updated = store.set_positive_reference(
                scope,
                reference_kind=kind,
                remaining_lifetime_seconds=lifetime,
                expected_version=record.version,
            )
            assert updated is not None
            record = updated
        preserved = {
            kind: record.positive_reference_expires_at(kind)
            for kind in (
                "semantic_promotion",
                "collaboration_priority",
                "assigned_review",
            )
        }
        provider_times = (
            record.provider_observed_at,
            record.provider_validated_at,
            record.base_expires_at,
            record.absolute_expires_at,
        )
        previous_version = record.version
        reconciled = store.reconcile_workflow_positive_references(
            scope,
            manual_priority_expires_at=(
                record.absolute_expires_at + DAY_SECONDS * 1_000
            ),
            waiting_expires_at=redis.current_ms + DAY_SECONDS * 1_000,
            returned_reply_expires_at=0,
            expected_version=record.version,
        )
        assert reconciled is not None
        self.assertEqual(reconciled.version, previous_version + 1)
        self.assertEqual(
            reconciled.positive_reference_expires_at("manual_priority"),
            reconciled.absolute_expires_at,
        )
        self.assertEqual(
            reconciled.positive_reference_expires_at("waiting"),
            redis.current_ms + DAY_SECONDS * 1_000,
        )
        self.assertEqual(
            reconciled.positive_reference_expires_at("returned_reply"),
            0,
        )
        self.assertEqual(
            {
                kind: reconciled.positive_reference_expires_at(kind)
                for kind in preserved
            },
            preserved,
        )
        self.assertEqual(
            (
                reconciled.provider_observed_at,
                reconciled.provider_validated_at,
                reconciled.base_expires_at,
                reconciled.absolute_expires_at,
            ),
            provider_times,
        )
        keys = store._scope_keys(scope)
        scores = tuple(
            redis.sorted_sets[key][keys["member"]]
            for key in (
                keys["mailbox_index"],
                keys["user_index"],
                keys["namespace_index"],
            )
        )
        self.assertEqual(scores, (reconciled.logical_expires_at(),) * 3)
        self.assertEqual(
            redis.expires_at[keys["record"]],
            reconciled.logical_expires_at(),
        )
        raw_before_conflict = redis.values[keys["record"]]
        with self.assertRaises(CandidateVersionConflict):
            store.reconcile_workflow_positive_references(
                scope,
                manual_priority_expires_at=0,
                waiting_expires_at=0,
                returned_reply_expires_at=0,
                expected_version=previous_version,
            )
        self.assertEqual(redis.values[keys["record"]], raw_before_conflict)
        self.assertNotIn(
            "cjson.encode",
            candidate_module._RECONCILE_WORKFLOW_REFERENCES_SCRIPT,
        )

    def test_workflow_reference_reconciliation_missing_and_ineligible_are_bounded(
        self,
    ) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        missing_scope = google_scope(message_id="workflow-missing")
        self.assertIsNone(
            store.reconcile_workflow_positive_references(
                missing_scope,
                manual_priority_expires_at=0,
                waiting_expires_at=0,
                returned_reply_expires_at=0,
                expected_version=1,
            )
        )
        self.assertEqual(redis.values, {})

        scope = google_scope(message_id="workflow-grace")
        record = store.upsert_confirmed(scope, snapshot(), expected_version=0)
        grace = store.mark_provider_validation_failure(
            scope,
            expected_version=record.version,
        )
        assert grace is not None
        with self.assertRaises(CandidateReferenceRejected):
            store.reconcile_workflow_positive_references(
                scope,
                manual_priority_expires_at=redis.current_ms + DAY_SECONDS * 1_000,
                waiting_expires_at=0,
                returned_reply_expires_at=0,
                expected_version=grace.version,
            )
        self.assertEqual(store.read_candidate(scope), grace)

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


class CustomImapV2CandidateStoreFoundationTests(unittest.TestCase):
    def _stores(
        self,
        redis: MemoryRedis,
    ) -> tuple[PriorityCandidateStore, PriorityCandidateStore]:
        return (
            PriorityCandidateStore(redis, hmac_secret=SECRET),
            PriorityCandidateStore(
                redis,
                hmac_secret=SECRET,
                storage_namespace="custom_imap_v2",
            ),
        )

    def test_same_logical_scope_has_fully_separate_physical_keys(self) -> None:
        redis = MemoryRedis()
        legacy, v2 = self._stores(redis)
        scope = imap_scope()
        legacy_keys = legacy._scope_keys(scope)
        v2_keys = v2._scope_keys(scope)

        self.assertEqual(legacy_keys["member"], v2_keys["member"])
        for key_name in (
            "record",
            "mailbox_index",
            "user_index",
            "namespace_index",
            "namespace_invalid",
            "mailbox_incomplete",
            "user_incomplete",
        ):
            self.assertNotEqual(legacy_keys[key_name], v2_keys[key_name])
        self.assertTrue(
            v2_keys["record"].startswith(
                "cuevion:priority:candidate:custom-imap-conversation-v2:v1:"
            )
        )
        self.assertEqual(
            scope.identity.canonical_bytes(),
            b"custom_imap\x00INBOX\x007\x0041",
        )

    def test_legacy_and_v2_records_and_pages_are_mutually_invisible(self) -> None:
        redis = MemoryRedis()
        legacy, v2 = self._stores(redis)
        scope = imap_scope()
        legacy_record = legacy.upsert_confirmed(
            scope,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )

        self.assertIsNone(v2.read_candidate(scope))
        self.assertEqual(v2.read_mailbox_page(scope.mailbox_scope()).records, ())
        v2_record = v2.upsert_confirmed(
            scope,
            imap_v2_snapshot(),
            expected_version=0,
        )
        self.assertEqual(legacy.read_candidate(scope), legacy_record)
        self.assertEqual(v2.read_candidate(scope), v2_record)
        self.assertEqual(
            legacy.read_mailbox_page(scope.mailbox_scope()).records,
            (legacy_record,),
        )
        self.assertEqual(
            v2.read_mailbox_page(scope.mailbox_scope()).records,
            (v2_record,),
        )

    def test_v2_mode_accepts_only_consistent_custom_imap_v2_authority(self) -> None:
        with self.assertRaises(ValueError):
            PriorityCandidateStore(
                MemoryRedis(),
                hmac_secret=SECRET,
                storage_namespace="custom_imap_v3",
            )
        store = PriorityCandidateStore(
            MemoryRedis(),
            hmac_secret=SECRET,
            storage_namespace="custom_imap_v2",
        )
        scope = imap_scope()
        accepted_rfc = store.upsert_confirmed(
            scope,
            imap_v2_snapshot(),
            expected_version=0,
        )
        self.assertEqual(accepted_rfc.version, 1)

        uid_scope = imap_scope(uid="42")
        accepted_uid = store.upsert_confirmed(
            uid_scope,
            replace(
                imap_v2_snapshot(authority_kind="imap_uid"),
                conversation=replace(
                    imap_v2_snapshot(authority_kind="imap_uid").conversation,
                    conversation_id="imap:v2:uid:mailbox-imap:INBOX:7:42",
                ),
            ),
            expected_version=0,
        )
        self.assertEqual(accepted_uid.version, 1)

        invalid_conversations = (
            replace(
                imap_v2_snapshot().conversation,
                conversation_id="imap:rfc:mailbox-imap:root%40example.test",
            ),
            replace(
                imap_v2_snapshot(authority_kind="imap_uid").conversation,
                conversation_id="imap:uid:mailbox-imap:INBOX:7:43",
            ),
            replace(imap_v2_snapshot().conversation, conversation_id="imap:v2:rfc:"),
            replace(
                imap_v2_snapshot().conversation,
                conversation_id="imap:v2:rfcx:mailbox-imap:root",
            ),
            replace(
                imap_v2_snapshot().conversation,
                conversation_id="imap:v2:rfc:mailbox-imap:root",
                authority_kind="imap_uid",
            ),
            replace(
                imap_v2_snapshot(authority_kind="imap_uid").conversation,
                conversation_id="imap:v2:uid:mailbox-imap:INBOX:7:43",
                rfc_message_id="message@example.test",
            ),
        )
        for index, conversation in enumerate(invalid_conversations, start=100):
            with self.subTest(conversation_id=conversation.conversation_id):
                invalid_scope = imap_scope(uid=str(index))
                with self.assertRaises(ValueError):
                    store.upsert_confirmed(
                        invalid_scope,
                        replace(
                            snapshot(provider="custom_imap"),
                            conversation=conversation,
                        ),
                        expected_version=0,
                    )

        google = google_scope()
        with self.assertRaises(ValueError):
            store.read_candidate(google)
        with self.assertRaises(ValueError):
            store.read_mailbox_page(google.mailbox_scope())
        with self.assertRaises(ValueError):
            store.upsert_confirmed(google, snapshot(), expected_version=0)
        with self.assertRaises(ValueError):
            store.read_candidate(replace(scope, provider="other"))
        with self.assertRaises(ValueError):
            replace(
                imap_v2_snapshot().conversation,
                conversation_id="imap:v2:rfc:" + "x" * 1_024,
            )

    def test_default_store_and_google_key_and_wire_behavior_are_unchanged(self) -> None:
        redis = MemoryRedis()
        store = PriorityCandidateStore(redis, hmac_secret=SECRET)
        google = google_scope()
        google_keys = store._scope_keys(google)
        self.assertEqual(
            google_keys["record"],
            "cuevion:priority:candidate:v2:record:"
            "c43a66e4bc4f66c3e6de3b17f242346d4443b5808c2fd2f729ea5c2cbbcb0b95",
        )
        google_record = store.upsert_confirmed(
            google,
            snapshot(),
            expected_version=0,
        )
        google_wire = json.loads(redis.values[google_keys["record"]])
        self.assertEqual(google_wire["schemaVersion"], 2)
        self.assertEqual(set(google_wire), candidate_module._ROOT_FIELDS)

        for uid, conversation in (
            (
                "901",
                replace(
                    snapshot(provider="custom_imap").conversation,
                    conversation_id="imap:rfc:mailbox-imap:root%40example.test",
                ),
            ),
            (
                "902",
                replace(
                    snapshot(provider="custom_imap").conversation,
                    conversation_id="imap:uid:mailbox-imap:INBOX:7:902",
                    authority_kind="imap_uid",
                    rfc_root_message_id=None,
                    rfc_message_id=None,
                ),
            ),
        ):
            legacy_scope = imap_scope(uid=uid)
            accepted = store.upsert_confirmed(
                legacy_scope,
                replace(snapshot(provider="custom_imap"), conversation=conversation),
                expected_version=0,
            )
            self.assertEqual(accepted.version, 1)
        self.assertEqual(store.read_candidate(google), google_record)

    def test_corruption_cannot_cross_the_namespace_boundary(self) -> None:
        redis = MemoryRedis()
        legacy, v2 = self._stores(redis)
        scope = imap_scope()
        v2_record = v2.upsert_confirmed(
            scope,
            imap_v2_snapshot(),
            expected_version=0,
        )
        legacy_keys = legacy._scope_keys(scope)
        redis.values[legacy_keys["record"]] = "{"
        redis.sorted_sets[legacy_keys["mailbox_index"]] = {"not-a-digest": 1}
        self.assertEqual(v2.read_candidate(scope), v2_record)
        self.assertEqual(
            v2.read_mailbox_page(scope.mailbox_scope()).records,
            (v2_record,),
        )

        isolated_scope = imap_scope(uid="55", mailbox_id="mailbox-imap-isolated")
        legacy_record = legacy.upsert_confirmed(
            isolated_scope,
            snapshot(provider="custom_imap"),
            expected_version=0,
        )
        v2_keys = v2._scope_keys(isolated_scope)
        redis.values[v2_keys["record"]] = "{"
        redis.sorted_sets[v2_keys["mailbox_index"]] = {"not-a-digest": 1}
        self.assertEqual(legacy.read_candidate(isolated_scope), legacy_record)
        self.assertIn(
            legacy_record,
            legacy.read_mailbox_page(isolated_scope.mailbox_scope()).records,
        )


class CandidateBatchConfirmationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.redis = MemoryRedis()
        self.store = PriorityCandidateStore(self.redis, hmac_secret=SECRET)

    @staticmethod
    def _scopes(
        count: int,
        *,
        mailbox_id: str = "mailbox-a",
    ) -> tuple[PriorityCandidateScope, ...]:
        return tuple(
            google_scope(
                mailbox_id=mailbox_id,
                message_id=f"batch-message-{index}",
            )
            for index in range(count)
        )

    def _seed_records(
        self,
        scopes: tuple[PriorityCandidateScope, ...],
        *,
        references: bool = False,
    ) -> tuple[candidate_module.PriorityCandidateRecord, ...]:
        records = []
        for index, scope in enumerate(scopes):
            intended = snapshot(
                routing_state="ready",
                routing=ready_routing(),
                snippet=f"batch snippet {index}",
            )
            record = self.store.upsert_confirmed(
                scope,
                intended,
                expected_version=0,
            )
            if references:
                for reference_index, kind in enumerate(
                    candidate_module._POSITIVE_REFERENCE_KINDS,
                    start=1,
                ):
                    updated = self.store.set_positive_reference(
                        scope,
                        reference_kind=kind,
                        remaining_lifetime_seconds=(reference_index + 1)
                        * DAY_SECONDS,
                        expected_version=record.version,
                    )
                    assert updated is not None
                    record = updated
            records.append(record)
        return tuple(records)

    @staticmethod
    def _workflow_key(scope: PriorityCandidateScope) -> str:
        from .store import PriorityWorkflowScope, derive_workflow_scope_digest

        workflow_scope = PriorityWorkflowScope(
            workspace_id=scope.workspace_id,
            user_id=scope.user_id,
            mailbox_id=scope.mailbox_id,
            identity=scope.identity,
        )
        return (
            "cuevion:priority:workflow:v1:record:"
            + derive_workflow_scope_digest(SECRET, workflow_scope)
        )

    def _confirmations(
        self,
        preflight: candidate_module.PriorityCandidateConfirmationPreflight,
        *,
        persisted_indexes: tuple[int, ...] = (),
    ) -> tuple[candidate_module.PriorityCandidateUnchangedConfirmation, ...]:
        confirmations = []
        for index, evidence in enumerate(preflight.evidence):
            workflow_key = self._workflow_key(evidence.scope)
            workflow_raw = None
            workflow_valid_until = 0
            if index in persisted_indexes:
                workflow_raw = self._encode_workflow(index)
                workflow_valid_until = preflight.observed_at + DAY_SECONDS * 1_000
                self.redis.values[workflow_key] = workflow_raw
            confirmations.append(
                candidate_module.PriorityCandidateUnchangedConfirmation(
                    evidence=evidence,
                    workflow_key=workflow_key,
                    workflow_raw=workflow_raw,
                    workflow_persisted=workflow_raw is not None,
                    workflow_valid_until=workflow_valid_until,
                )
            )
        return tuple(confirmations)

    @staticmethod
    def _encode_workflow(index: int) -> str:
        return json.dumps(
            {"opaqueWorkflowVersion": index + 1},
            separators=(",", ":"),
            sort_keys=True,
        )

    def test_preflight_maximum_duplicate_and_cross_mailbox_fail_before_io(
        self,
    ) -> None:
        accepted_redis = MemoryRedis()
        accepted_store = PriorityCandidateStore(
            accepted_redis,
            hmac_secret=SECRET,
        )
        accepted = accepted_store.preflight_unchanged_confirmations(
            self._scopes(candidate_module.CANDIDATE_CONFIRMATION_MAX_BATCH_SIZE)
        )
        self.assertEqual(
            len(accepted.evidence),
            candidate_module.CANDIDATE_CONFIRMATION_MAX_BATCH_SIZE,
        )
        self.assertTrue(accepted_redis.commands)

        empty_redis = MemoryRedis()
        empty = PriorityCandidateStore(
            empty_redis,
            hmac_secret=SECRET,
        ).preflight_unchanged_confirmations(())
        self.assertEqual((empty.observed_at, empty.evidence), (0, ()))
        self.assertEqual(empty_redis.commands, [])

        duplicate = self._scopes(1)[0]
        invalid_batches = (
            self._scopes(
                candidate_module.CANDIDATE_CONFIRMATION_MAX_BATCH_SIZE + 1
            ),
            (duplicate, duplicate),
            (
                duplicate,
                google_scope(
                    mailbox_id="mailbox-b",
                    message_id="cross-mailbox",
                ),
            ),
        )
        for scopes in invalid_batches:
            with self.subTest(size=len(scopes)):
                redis = MemoryRedis()
                store = PriorityCandidateStore(redis, hmac_secret=SECRET)
                with self.assertRaises(ValueError):
                    store.preflight_unchanged_confirmations(scopes)
                self.assertEqual(redis.commands, [])

    def test_preflight_captures_exact_time_record_marker_and_index_evidence(
        self,
    ) -> None:
        scopes = self._scopes(3)
        records = self._seed_records(scopes)
        keys = tuple(self.store._scope_keys(scope) for scope in scopes)
        expected_raw = tuple(
            self.redis.values[item["record"]] for item in keys
        )
        self.redis.values[keys[0]["mailbox_incomplete"]] = (
            candidate_module._INCOMPLETE_VALUE
        )
        self.redis.values[keys[0]["user_incomplete"]] = (
            candidate_module._INCOMPLETE_VALUE
        )
        self.redis.sorted_sets[keys[1]["mailbox_index"]].pop(
            keys[1]["member"]
        )
        self.redis.values.pop(keys[2]["record"])
        self.redis.sorted_sets[keys[2]["record"]] = {"wrong-type": 1}
        self.redis.advance(7)
        self.redis.commands.clear()

        preflight = self.store.preflight_unchanged_confirmations(scopes)

        self.assertEqual(preflight.observed_at, self.redis.current_ms)
        self.assertEqual(
            preflight.evidence[0],
            candidate_module.PriorityCandidateConfirmationEvidence(
                scope=scopes[0],
                raw=expected_raw[0],
                record=records[0],
                marker_values=(
                    candidate_module._INCOMPLETE_VALUE,
                    candidate_module._INCOMPLETE_VALUE,
                    None,
                ),
                storage_valid=True,
                indexes_valid=True,
            ),
        )
        self.assertEqual(preflight.evidence[1].raw, expected_raw[1])
        self.assertEqual(preflight.evidence[1].record, records[1])
        self.assertTrue(preflight.evidence[1].storage_valid)
        self.assertFalse(preflight.evidence[1].indexes_valid)
        self.assertIsNone(preflight.evidence[2].raw)
        self.assertIsNone(preflight.evidence[2].record)
        self.assertFalse(preflight.evidence[2].storage_valid)
        self.assertFalse(preflight.evidence[2].indexes_valid)
        self.assertEqual(
            preflight.evidence[2].marker_values,
            (candidate_module._INCOMPLETE_VALUE,) * 2 + (None,),
        )
        operations = [command[0] for command in self.redis.commands]
        self.assertEqual(operations.count("TIME"), 1)
        self.assertIn("EXISTS", operations)
        self.assertIn("TYPE", operations)
        self.assertEqual(operations.count("ZMSCORE"), 3)
        self.assertNotIn("SCAN", operations)

    def test_four_row_confirmation_preserves_routing_references_and_parity(
        self,
    ) -> None:
        scopes = self._scopes(4)
        before = self._seed_records(scopes, references=True)
        self.redis.advance(5)
        self.redis.commands.clear()
        preflight = self.store.preflight_unchanged_confirmations(scopes)
        confirmations = self._confirmations(
            preflight,
            persisted_indexes=(2, 3),
        )
        self.redis.advance(1)
        commit_time = self.redis.current_ms

        committed = self.store.confirm_unchanged_batch(
            preflight,
            confirmations,
        )

        self.assertTrue(all(record is not None for record in committed))
        for index, (previous, current) in enumerate(
            zip(before, committed, strict=True)
        ):
            assert current is not None
            self.assertEqual(current.snapshot, previous.snapshot)
            self.assertEqual(current.snapshot.routing_state, "ready")
            self.assertEqual(current.snapshot.routing, ready_routing())
            self.assertEqual(
                current.positive_references,
                previous.positive_references,
            )
            self.assertEqual(
                {reference.kind for reference in current.positive_references},
                set(candidate_module._POSITIVE_REFERENCE_KINDS),
            )
            self.assertTrue(
                all(
                    reference.expires_at > preflight.observed_at
                    for reference in current.positive_references
                )
            )
            increment = 2 if index >= 2 else 1
            self.assertEqual(current.version, previous.version + increment)
            self.assertEqual(
                (
                    current.provider_observed_at,
                    current.provider_validated_at,
                    current.base_expires_at,
                    current.absolute_expires_at,
                    current.grace_expires_at,
                    current.state,
                ),
                (
                    preflight.observed_at,
                    preflight.observed_at,
                    preflight.observed_at
                    + CANDIDATE_BASE_TTL_SECONDS * 1_000,
                    preflight.observed_at
                    + CANDIDATE_ABSOLUTE_TTL_SECONDS * 1_000,
                    0,
                    "provider_confirmed",
                ),
            )
            self.assertEqual(
                current.updated_at,
                commit_time if index >= 2 else preflight.observed_at,
            )
            item_keys = self.store._scope_keys(scopes[index])
            for index_name in (
                "mailbox_index",
                "user_index",
                "namespace_index",
            ):
                self.assertEqual(
                    self.redis.sorted_sets[item_keys[index_name]][
                        item_keys["member"]
                    ],
                    current.logical_expires_at(),
                )
        commit_commands = [
            command
            for command in self.redis.commands
            if command[0] == "EVAL"
            and command[1] == candidate_module._BATCH_CONFIRM_UNCHANGED_SCRIPT
        ]
        self.assertTrue(commit_commands)
        self.assertTrue(
            all(
                candidate_module._redis_request_size(command)
                <= candidate_module.CANDIDATE_CONFIRMATION_MAX_REQUEST_BYTES
                for command in commit_commands
            )
        )

    def test_candidate_workflow_and_index_conflicts_are_row_local(self) -> None:
        scopes = self._scopes(4)
        before = self._seed_records(scopes)
        keys = tuple(self.store._scope_keys(scope) for scope in scopes)
        raw_before = tuple(self.redis.values[item["record"]] for item in keys)
        self.redis.advance(1)
        preflight = self.store.preflight_unchanged_confirmations(scopes)
        confirmations = self._confirmations(preflight, persisted_indexes=(1,))

        self.redis.values[keys[0]["record"]] = raw_before[0] + " "
        self.redis.values[confirmations[1].workflow_key] = (
            "different-workflow-version"
        )
        self.redis.sorted_sets[keys[2]["mailbox_index"]][keys[2]["member"]] += 1

        committed = self.store.confirm_unchanged_batch(
            preflight,
            confirmations,
        )

        self.assertEqual(committed[:3], (None, None, None))
        self.assertIsNotNone(committed[3])
        self.assertEqual(self.redis.values[keys[0]["record"]], raw_before[0] + " ")
        self.assertEqual(self.redis.values[keys[1]["record"]], raw_before[1])
        self.assertEqual(self.redis.values[keys[2]["record"]], raw_before[2])
        successful = committed[3]
        assert successful is not None
        self.assertEqual(successful.version, before[3].version + 1)
        self.assertEqual(successful.snapshot, before[3].snapshot)
        self.assertNotEqual(self.redis.values[keys[3]["record"]], raw_before[3])
        self.assertEqual(
            self.redis.sorted_sets[keys[2]["mailbox_index"]][keys[2]["member"]],
            before[2].logical_expires_at() + 1,
        )

    def test_marker_structure_change_aborts_chunk_without_overwrite(self) -> None:
        scopes = self._scopes(4)
        self._seed_records(scopes)
        keys = tuple(self.store._scope_keys(scope) for scope in scopes)
        raw_before = tuple(self.redis.values[item["record"]] for item in keys)
        indexes_before = {
            key: dict(values) for key, values in self.redis.sorted_sets.items()
        }
        self.redis.advance(1)
        preflight = self.store.preflight_unchanged_confirmations(scopes)
        confirmations = self._confirmations(preflight)
        self.redis.values[keys[0]["mailbox_incomplete"]] = "corrupt-marker"

        committed = self.store.confirm_unchanged_batch(
            preflight,
            confirmations,
        )

        self.assertEqual(committed, (None, None, None, None))
        self.assertEqual(
            tuple(self.redis.values[item["record"]] for item in keys),
            raw_before,
        )
        self.assertEqual(self.redis.sorted_sets, indexes_before)


class CandidateDormancyTests(unittest.TestCase):
    def test_import_and_construction_do_not_mutate_storage(self) -> None:
        redis = MemoryRedis()
        PriorityCandidateStore(redis, hmac_secret=SECRET)
        self.assertEqual(redis.commands, [])
        self.assertEqual(redis.values, {})
        self.assertEqual(redis.sorted_sets, {})


if __name__ == "__main__":
    unittest.main()
