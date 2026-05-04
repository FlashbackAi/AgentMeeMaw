"""LLM adapter error types."""


class LLMError(Exception):
    """Base class for provider-agnostic LLM failures."""


class LLMTimeout(LLMError):
    """Raised when an LLM call exceeds its configured timeout."""


class LLMMalformedResponse(LLMError):
    """Raised when a provider response is missing the expected content."""
