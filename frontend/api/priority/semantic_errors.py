"""Safe, content-free failures for semantic conversation analysis."""

from __future__ import annotations


class SemanticCoreError(Exception):
    """Base error exposed to the future route boundary.

    Messages must remain operational and must never include email text, model
    output, prompts, credentials, or provider tokens.
    """

    code = "semantic_analysis_failed"
    retryable = False

    def __init__(self, message: str = "Semantic analysis failed.") -> None:
        super().__init__(message)


class SemanticInputError(SemanticCoreError):
    code = "input_invalid"
    retryable = False


class SemanticConfigurationError(SemanticCoreError):
    code = "configuration_invalid"
    retryable = False


class SemanticProviderUnavailableError(SemanticCoreError):
    code = "provider_unavailable"
    retryable = True


class SemanticProviderTimeoutError(SemanticCoreError):
    code = "provider_timeout"
    retryable = True


class SemanticProviderRateLimitError(SemanticCoreError):
    code = "provider_rate_limited"
    retryable = True


class SemanticProviderResponseError(SemanticCoreError):
    code = "provider_response_invalid"
    retryable = False
