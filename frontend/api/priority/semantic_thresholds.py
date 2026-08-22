"""Conservative advisory confidence thresholds.

Threshold checks are intentionally separate from deterministic Priority
authority.  Passing a threshold never mutates, removes, or ranks Priority.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .semantic_types import SemanticAssessment, SemanticState


SEMANTIC_CONFIDENCE_THRESHOLDS: Mapping[SemanticState, float] = MappingProxyType(
    {
        # False-positive completion is the highest-risk semantic outcome.
        SemanticState.RESOLVED: 0.97,
        # FYI alone must not silently close an existing deterministic open loop.
        SemanticState.INFORMATIONAL: 0.93,
        SemanticState.WAITING_ON_OTHER: 0.82,
        SemanticState.NEEDS_USER_ACTION: 0.80,
        # Uncertain is always a conservative no-op for deterministic authority.
        SemanticState.UNCERTAIN: 0.0,
    }
)

# Reserved for a later, separately reviewed activation slice.  This threshold
# is intentionally not consulted by any current Priority effect.
NEW_INBOUND_NEEDS_USER_ACTION_PROMOTION_THRESHOLD = 0.90


@dataclass(frozen=True, slots=True)
class SemanticConfidencePolicyResult:
    state: SemanticState
    effective_state: SemanticState
    confidence: float
    threshold: float
    meets_threshold: bool

    @property
    def is_shadow_only(self) -> bool:
        return True


def confidence_threshold_for(state: SemanticState) -> float:
    return SEMANTIC_CONFIDENCE_THRESHOLDS[state]


def evaluate_semantic_confidence(
    assessment: SemanticAssessment,
) -> SemanticConfidencePolicyResult:
    threshold = confidence_threshold_for(assessment.state)
    meets_threshold = assessment.confidence >= threshold
    return SemanticConfidencePolicyResult(
        state=assessment.state,
        effective_state=(
            assessment.state if meets_threshold else SemanticState.UNCERTAIN
        ),
        confidence=assessment.confidence,
        threshold=threshold,
        meets_threshold=meets_threshold,
    )


def meets_future_new_inbound_promotion_threshold(
    assessment: SemanticAssessment,
) -> bool:
    """Return future eligibility without granting any product authority."""

    return (
        assessment.state is SemanticState.NEEDS_USER_ACTION
        and assessment.confidence
        >= NEW_INBOUND_NEEDS_USER_ACTION_PROMOTION_THRESHOLD
    )


def confidence_bucket(confidence: float) -> str:
    """Return a bounded content-free value suitable for operational logs."""

    if confidence >= 0.90:
        return "very_high"
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.50:
        return "medium"
    return "low"
