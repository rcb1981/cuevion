"""Strict provider-neutral types for semantic conversation state."""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from .semantic_errors import SemanticInputError, SemanticProviderResponseError


SEMANTIC_SCHEMA_VERSION = "priority-semantic-state-v1"
CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION = (
    "priority-semantic-state-custom-imap-v2"
)
MAX_REQUEST_TURNS = 3
MAX_TURN_ID_LENGTH = 512
MAX_TIMESTAMP_LENGTH = 64


def semantic_schema_version_for_provider(
    provider: str,
    *,
    custom_imap_v2: bool = False,
) -> str:
    """Resolve a provider-specific semantic version without changing callers."""

    if provider not in {"google", "custom_imap"}:
        raise ValueError("invalid semantic provider")
    if custom_imap_v2:
        if provider != "custom_imap":
            raise ValueError("custom IMAP v2 semantics require custom_imap")
        return CUSTOM_IMAP_V2_SEMANTIC_SCHEMA_VERSION
    return SEMANTIC_SCHEMA_VERSION


class SemanticState(str, Enum):
    NEEDS_USER_ACTION = "needs_user_action"
    WAITING_ON_OTHER = "waiting_on_other"
    RESOLVED = "resolved"
    INFORMATIONAL = "informational"
    UNCERTAIN = "uncertain"


class SemanticReasonCode(str, Enum):
    EXPLICIT_REQUEST = "explicit_request"
    IMPLICIT_REQUEST = "implicit_request"
    MIXED_ACKNOWLEDGEMENT_WITH_REQUEST = "mixed_acknowledgement_with_request"
    USER_OWNS_NEXT_ACTION = "user_owns_next_action"
    EXTERNAL_OWNS_NEXT_ACTION = "external_owns_next_action"
    USER_HANDED_OFF_ACTION = "user_handed_off_action"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    AWAITING_APPROVAL = "awaiting_approval"
    CLOSING_ACKNOWLEDGEMENT = "closing_acknowledgement"
    COMPLETED_CONFIRMATION = "completed_confirmation"
    INFORMATIONAL_UPDATE = "informational_update"
    AMBIGUOUS_CONTEXT = "ambiguous_context"


class SpeakerRole(str, Enum):
    USER = "USER"
    EXTERNAL = "EXTERNAL"


class TurnDirection(str, Enum):
    INCOMING = "INCOMING"
    OUTGOING = "OUTGOING"


_REASONS_BY_STATE: Mapping[SemanticState, frozenset[SemanticReasonCode]] = (
    MappingProxyType(
        {
            SemanticState.NEEDS_USER_ACTION: frozenset(
                {
                    SemanticReasonCode.EXPLICIT_REQUEST,
                    SemanticReasonCode.IMPLICIT_REQUEST,
                    SemanticReasonCode.MIXED_ACKNOWLEDGEMENT_WITH_REQUEST,
                    SemanticReasonCode.USER_OWNS_NEXT_ACTION,
                }
            ),
            SemanticState.WAITING_ON_OTHER: frozenset(
                {
                    SemanticReasonCode.EXTERNAL_OWNS_NEXT_ACTION,
                    SemanticReasonCode.USER_HANDED_OFF_ACTION,
                    SemanticReasonCode.AWAITING_CONFIRMATION,
                    SemanticReasonCode.AWAITING_APPROVAL,
                }
            ),
            SemanticState.RESOLVED: frozenset(
                {
                    SemanticReasonCode.CLOSING_ACKNOWLEDGEMENT,
                    SemanticReasonCode.COMPLETED_CONFIRMATION,
                }
            ),
            SemanticState.INFORMATIONAL: frozenset(
                {SemanticReasonCode.INFORMATIONAL_UPDATE}
            ),
            SemanticState.UNCERTAIN: frozenset(
                {SemanticReasonCode.AMBIGUOUS_CONTEXT}
            ),
        }
    )
)


@dataclass(frozen=True, slots=True)
class SemanticTurn:
    """One already-authorized provider-neutral conversation turn.

    Provider tokens, mailbox credentials, raw MIME, headers, attachments, and
    remote-image payloads are deliberately not representable here.
    """

    turn_id: str
    speaker: SpeakerRole
    direction: TurnDirection
    text: str
    timestamp: str | None = None

    def __post_init__(self) -> None:
        if (
            type(self.turn_id) is not str
            or not self.turn_id.strip()
            or len(self.turn_id) > MAX_TURN_ID_LENGTH
        ):
            raise SemanticInputError("A bounded turn identity is required.")
        if not isinstance(self.speaker, SpeakerRole):
            raise SemanticInputError("A valid speaker role is required.")
        if not isinstance(self.direction, TurnDirection):
            raise SemanticInputError("A valid turn direction is required.")
        if type(self.text) is not str:
            raise SemanticInputError("Turn text must be a string.")
        if self.timestamp is not None and (
            type(self.timestamp) is not str
            or len(self.timestamp) > MAX_TIMESTAMP_LENGTH
        ):
            raise SemanticInputError("Turn timestamp is invalid.")


@dataclass(frozen=True, slots=True)
class SemanticAssessmentRequest:
    turns: tuple[SemanticTurn, ...]

    def __post_init__(self) -> None:
        if type(self.turns) is not tuple:
            raise SemanticInputError("Semantic turns must be an immutable tuple.")
        if not self.turns:
            raise SemanticInputError("At least one conversation turn is required.")
        if len(self.turns) > MAX_REQUEST_TURNS:
            raise SemanticInputError("Too many conversation turns were supplied.")
        if any(not isinstance(turn, SemanticTurn) for turn in self.turns):
            raise SemanticInputError("Conversation turns are invalid.")


@dataclass(frozen=True, slots=True)
class SemanticAssessment:
    state: SemanticState
    confidence: float
    reason_code: SemanticReasonCode

    def __post_init__(self) -> None:
        if not isinstance(self.state, SemanticState):
            raise SemanticProviderResponseError("Semantic state is invalid.")
        if type(self.confidence) not in (int, float):
            raise SemanticProviderResponseError("Semantic confidence is invalid.")
        try:
            normalized_confidence = float(self.confidence)
        except (OverflowError, TypeError, ValueError):
            raise SemanticProviderResponseError(
                "Semantic confidence is invalid."
            ) from None
        if not math.isfinite(normalized_confidence) or not 0.0 <= normalized_confidence <= 1.0:
            raise SemanticProviderResponseError("Semantic confidence is invalid.")
        object.__setattr__(self, "confidence", normalized_confidence)
        if not isinstance(self.reason_code, SemanticReasonCode):
            raise SemanticProviderResponseError("Semantic reason code is invalid.")
        if self.reason_code not in _REASONS_BY_STATE[self.state]:
            raise SemanticProviderResponseError(
                "Semantic state and reason code are inconsistent."
            )

    def to_wire_dict(self) -> dict[str, str | float]:
        return {
            "state": self.state.value,
            "confidence": self.confidence,
            "reasonCode": self.reason_code.value,
        }

    @classmethod
    def from_wire_dict(cls, value: object) -> "SemanticAssessment":
        if type(value) is not dict or set(value) != {
            "state",
            "confidence",
            "reasonCode",
        }:
            raise SemanticProviderResponseError(
                "Semantic provider response does not match the schema."
            )
        try:
            state = SemanticState(value["state"])
            reason_code = SemanticReasonCode(value["reasonCode"])
        except (TypeError, ValueError):
            raise SemanticProviderResponseError(
                "Semantic provider response contains an unknown enum."
            ) from None
        return cls(
            state=state,
            confidence=value["confidence"],
            reason_code=reason_code,
        )


def semantic_assessment_json_schema() -> dict[str, object]:
    """Return the exact strict schema sent to structured-output providers."""

    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "state": {
                "type": "string",
                "enum": [state.value for state in SemanticState],
            },
            "confidence": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1.0,
            },
            "reasonCode": {
                "type": "string",
                "enum": [reason.value for reason in SemanticReasonCode],
            },
        },
        "required": ["state", "confidence", "reasonCode"],
    }


def semantic_turns(turns: Sequence[SemanticTurn]) -> tuple[SemanticTurn, ...]:
    """Convenience helper for a boundary that already has a safe sequence."""

    return tuple(turns)
