"""Claude API helpers for the agent layer.

Mirrors the pattern used in `processors/analyzer.py`: a thin wrapper that
calls `messages.create()`, parses JSON, and tracks tokens. Kept in its own
module so the agent doesn't import the analyzer (and so this module stays
focused on agent-flavored prompts).

Why we don't use `messages.parse()` with Pydantic schemas here: the existing
project pins `anthropic>=0.18.0`, which predates structured-output support.
Forcing an SDK upgrade would touch the rest of the codebase. JSON-mode +
manual validation is fine for the small number of prompts the agent uses.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Optional

try:
    import anthropic
    ANTHROPIC_AVAILABLE = True
except ImportError:
    ANTHROPIC_AVAILABLE = False

from src.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)


class AgentLLMError(RuntimeError):
    """Raised when the agent's LLM helper fails (no API key, parse error)."""


class AgentLLM:
    """Holds an Anthropic client. Stateless across calls otherwise."""

    def __init__(self, api_key: Optional[str] = None, model: str = CLAUDE_MODEL):
        if not ANTHROPIC_AVAILABLE:
            raise AgentLLMError(
                "The `anthropic` package is required. "
                "Install with: pip install anthropic"
            )

        self.api_key = api_key or ANTHROPIC_API_KEY
        if not self.api_key:
            raise AgentLLMError(
                "ANTHROPIC_API_KEY not set. Add to .env or pass api_key."
            )

        self.client = anthropic.Anthropic(api_key=self.api_key)
        self.model = model
        self.tokens_used = 0

    def call_json(
        self,
        prompt: str,
        system: Optional[str] = None,
        max_tokens: int = 1500,
    ) -> Optional[dict[str, Any]]:
        """Call Claude expecting a JSON object back. Returns None on parse failure."""
        try:
            kwargs: dict[str, Any] = {
                "model": self.model,
                "max_tokens": max_tokens,
                "messages": [{"role": "user", "content": prompt}],
            }
            if system:
                kwargs["system"] = system
            response = self.client.messages.create(**kwargs)
        except Exception as e:
            logger.error(f"agent_llm_api_error: {e}")
            return None

        if hasattr(response, "usage"):
            self.tokens_used += (
                response.usage.input_tokens + response.usage.output_tokens
            )

        text = response.content[0].text
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> Optional[dict[str, Any]]:
        """Strip code fences if present, then json.loads."""
        if not text:
            return None
        text = text.strip()
        if text.startswith("```"):
            # Drop the opening fence and optional 'json' tag.
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            # If there's a closing fence at the end, drop it.
            if "```" in text:
                text = text.split("```", 1)[0]
            text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            logger.error(f"agent_llm_parse_error: {e}; first 300 chars: {text[:300]}")
            return None
