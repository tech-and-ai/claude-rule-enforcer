"""
CRE Adapters — translate between AI coding tools and the CRE engine.

Each adapter handles:
  - Input parsing: tool-specific JSON → CRE normalized format
  - Output formatting: CRE decision → tool-specific response JSON
  - Exit codes: tool-specific exit code conventions
"""

from .claude_code import ClaudeCodeAdapter
from .generic import GenericAdapter

ADAPTERS = {
    "claude-code": ClaudeCodeAdapter,
    "generic": GenericAdapter,
}


def detect_adapter(raw_input):
    """Auto-detect which adapter to use based on input format."""
    if "tool_name" in raw_input and "tool_input" in raw_input:
        return ClaudeCodeAdapter()
    return GenericAdapter()


def get_adapter(name=None, raw_input=None):
    """Get adapter by name, or auto-detect from input."""
    if name and name in ADAPTERS:
        return ADAPTERS[name]()
    if raw_input:
        return detect_adapter(raw_input)
    return GenericAdapter()
