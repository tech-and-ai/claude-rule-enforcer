"""
CRE Adapters — translate between AI coding tools and the CRE engine.

Each adapter handles:
  - Input parsing: tool-specific JSON → CRE normalized format
  - Output formatting: CRE decision → tool-specific response JSON
  - Exit codes: tool-specific exit code conventions
"""

from .claude_code import ClaudeCodeAdapter
from .generic import GenericAdapter
from .delegate import DelegateAdapter

# Backwards compatibility
AmpAdapter = DelegateAdapter

ADAPTERS = {
    "claude-code": ClaudeCodeAdapter,
    "generic": GenericAdapter,
    "delegate": DelegateAdapter,
    "amp": DelegateAdapter,  # backwards compatible alias
}


def detect_adapter(raw_input):
    """Auto-detect which adapter to use based on input format."""
    import os
    if os.environ.get("AGENT_TOOL_NAME"):
        return DelegateAdapter()
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
