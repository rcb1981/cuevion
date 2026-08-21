"""Provider-neutral semantic conversation-state analysis.

This package is intentionally advisory.  It never mutates deterministic
Priority state, mailbox state, or provider data.
"""

from .semantic_core import assess_semantic_conversation
from .semantic_types import (
    SemanticAssessment,
    SemanticAssessmentRequest,
    SemanticReasonCode,
    SemanticState,
    SemanticTurn,
    SpeakerRole,
    TurnDirection,
)

__all__ = [
    "SemanticAssessment",
    "SemanticAssessmentRequest",
    "SemanticReasonCode",
    "SemanticState",
    "SemanticTurn",
    "SpeakerRole",
    "TurnDirection",
    "assess_semantic_conversation",
]
