"""Fail-closed runtime configuration for shadow semantic analysis."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .semantic_errors import SemanticConfigurationError
from .semantic_types import SEMANTIC_SCHEMA_VERSION


SEMANTIC_MODE_ENV = "PRIORITY_SEMANTIC_MODE"
SEMANTIC_MODEL_ENV = "PRIORITY_SEMANTIC_MODEL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
SEMANTIC_PROVIDER_DEADLINE_SECONDS = 8.0

_MODEL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)


class SemanticMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"


@dataclass(frozen=True, slots=True)
class SemanticRuntimeConfig:
    mode: SemanticMode
    model: str | None
    deadline_seconds: float = SEMANTIC_PROVIDER_DEADLINE_SECONDS
    schema_version: str = SEMANTIC_SCHEMA_VERSION

    @property
    def enabled(self) -> bool:
        return self.mode is SemanticMode.SHADOW

    @property
    def can_mutate_priority(self) -> bool:
        # Slice 1 is observation-only by construction.
        return False


def _read_env(environ: Mapping[str, str], key: str) -> str:
    value = environ.get(key, "")
    if type(value) is not str:
        raise SemanticConfigurationError("Semantic environment value is invalid.")
    return value.strip()


def load_semantic_runtime_config(
    environ: Mapping[str, str] | None = None,
) -> SemanticRuntimeConfig:
    source = os.environ if environ is None else environ
    raw_mode = _read_env(source, SEMANTIC_MODE_ENV).lower()
    # Unknown values fail closed.  They must never accidentally enable calls.
    mode = SemanticMode.SHADOW if raw_mode == SemanticMode.SHADOW.value else SemanticMode.OFF

    model = _read_env(source, SEMANTIC_MODEL_ENV) or None
    if mode is SemanticMode.SHADOW and model is None:
        raise SemanticConfigurationError(
            "Shadow semantic mode requires an explicit model."
        )
    if model is not None and _MODEL_NAME_PATTERN.fullmatch(model) is None:
        if mode is SemanticMode.SHADOW:
            raise SemanticConfigurationError("Semantic model name is invalid.")
        model = None

    return SemanticRuntimeConfig(mode=mode, model=model)


def read_openai_api_key(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    api_key = _read_env(source, OPENAI_API_KEY_ENV)
    if not api_key:
        raise SemanticConfigurationError("OpenAI API key is unavailable.")
    return api_key
