"""Provider-neutral adapter boundary for semantic classification."""

from __future__ import annotations

from typing import Protocol

from .semantic_text import SemanticTextWindow
from .semantic_types import SemanticAssessment


class SemanticModelAdapter(Protocol):
    provider: str
    model: str

    def assess(self, window: SemanticTextWindow) -> SemanticAssessment:
        """Classify meaning only; adapters have no action or tool authority."""

        ...
