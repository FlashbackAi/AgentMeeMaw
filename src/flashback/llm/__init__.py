"""Provider-agnostic LLM adapter surface."""

from flashback.llm.errors import LLMError, LLMMalformedResponse, LLMTimeout
from flashback.llm.interface import Provider, call_with_tool
from flashback.llm.tool_spec import ToolSpec

__all__ = [
    "LLMError",
    "LLMMalformedResponse",
    "LLMTimeout",
    "Provider",
    "ToolSpec",
    "call_with_tool",
]
