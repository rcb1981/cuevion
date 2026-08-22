"""Fail-closed runtime configuration for Priority semantic analysis."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import Enum
from typing import Mapping

from .semantic_errors import SemanticConfigurationError
from .semantic_types import SEMANTIC_SCHEMA_VERSION


SEMANTIC_MODE_ENV = "PRIORITY_SEMANTIC_MODE"
SEMANTIC_NEW_INBOUND_MODE_ENV = "PRIORITY_SEMANTIC_NEW_INBOUND_MODE"
SEMANTIC_MODEL_ENV = "PRIORITY_SEMANTIC_MODEL"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
SEMANTIC_PROVIDER_DEADLINE_SECONDS = 8.0

_MODEL_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)


class SemanticMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"
    ACTIVE = "active"


class NewInboundSemanticMode(str, Enum):
    OFF = "off"
    SHADOW = "shadow"


@dataclass(frozen=True, slots=True)
class SemanticRuntimeConfig:
    mode: SemanticMode
    model: str | None
    deadline_seconds: float = SEMANTIC_PROVIDER_DEADLINE_SECONDS
    schema_version: str = SEMANTIC_SCHEMA_VERSION
    new_inbound_mode: NewInboundSemanticMode = NewInboundSemanticMode.OFF

    @property
    def enabled(self) -> bool:
        return self.mode in {SemanticMode.SHADOW, SemanticMode.ACTIVE}

    @property
    def can_mutate_priority(self) -> bool:
        return self.mode is SemanticMode.ACTIVE

    @property
    def new_inbound_enabled(self) -> bool:
        return self.new_inbound_mode is NewInboundSemanticMode.SHADOW

    @property
    def can_call_provider(self) -> bool:
        return self.enabled or self.new_inbound_enabled


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
    mode = {
        SemanticMode.SHADOW.value: SemanticMode.SHADOW,
        SemanticMode.ACTIVE.value: SemanticMode.ACTIVE,
    }.get(raw_mode, SemanticMode.OFF)
    raw_new_inbound_mode = _read_env(
        source,
        SEMANTIC_NEW_INBOUND_MODE_ENV,
    ).lower()
    # This capability is deliberately shadow-only.  Unknown values, including
    # "active", fail closed so a future promotion cannot be enabled by typo.
    new_inbound_mode = {
        NewInboundSemanticMode.SHADOW.value: NewInboundSemanticMode.SHADOW,
    }.get(raw_new_inbound_mode, NewInboundSemanticMode.OFF)

    model = _read_env(source, SEMANTIC_MODEL_ENV) or None
    any_capability_enabled = (
        mode in {SemanticMode.SHADOW, SemanticMode.ACTIVE}
        or new_inbound_mode is NewInboundSemanticMode.SHADOW
    )
    if any_capability_enabled and model is None:
        raise SemanticConfigurationError(
            "Enabled semantic mode requires an explicit model."
        )
    if model is not None and _MODEL_NAME_PATTERN.fullmatch(model) is None:
        if any_capability_enabled:
            raise SemanticConfigurationError("Semantic model name is invalid.")
        model = None

    return SemanticRuntimeConfig(
        mode=mode,
        model=model,
        new_inbound_mode=new_inbound_mode,
    )


def read_new_inbound_client_mode(
    environ: Mapping[str, str] | None = None,
) -> str:
    """Expose only the fail-closed provider-refresh capability flag."""

    try:
        config = load_semantic_runtime_config(environ)
    except SemanticConfigurationError:
        return NewInboundSemanticMode.OFF.value
    if config.new_inbound_enabled and config.model:
        return NewInboundSemanticMode.SHADOW.value
    return NewInboundSemanticMode.OFF.value


def read_openai_api_key(environ: Mapping[str, str] | None = None) -> str:
    source = os.environ if environ is None else environ
    api_key = _read_env(source, OPENAI_API_KEY_ENV)
    if not api_key:
        raise SemanticConfigurationError("OpenAI API key is unavailable.")
    return api_key
