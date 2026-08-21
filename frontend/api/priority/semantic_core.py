"""Provider-neutral orchestration for one shadow semantic assessment."""

from __future__ import annotations

from .semantic_adapter import SemanticModelAdapter
from .semantic_errors import SemanticProviderResponseError
from .semantic_text import build_semantic_text_window
from .semantic_types import SemanticAssessment, SemanticAssessmentRequest


def assess_semantic_conversation(
    request: SemanticAssessmentRequest,
    *,
    adapter: SemanticModelAdapter,
) -> SemanticAssessment:
    """Normalize and classify a bounded conversation window.

    The return value is evidence only.  This function has no route, persistence,
    provider-identity, or Priority mutation authority.
    """

    window = build_semantic_text_window(request.turns)
    assessment = adapter.assess(window)
    if not isinstance(assessment, SemanticAssessment):
        raise SemanticProviderResponseError(
            "Semantic adapter returned an invalid result."
        )
    return assessment
