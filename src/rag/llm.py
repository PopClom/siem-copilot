"""
rag/llm.py
----------
Thin wrapper around the Anthropic Python SDK.

Exposes two modes:
  * complete()   → returns the full response as a string (for REST API)
  * stream()     → yields text deltas (for future SSE / WebSocket support)

The client is instantiated once and reused across requests.
"""

from __future__ import annotations

import logging
import os
from typing import Generator, Optional

from src.config.settings import LLMConfig

logger = logging.getLogger(__name__)


class LLMClient:
    """
    Anthropic Claude client.

    Parameters
    ----------
    config:      LLM section of the YAML config
    system:      system prompt string (built externally in prompt.py)
    """

    def __init__(self, config: LLMConfig, system: str) -> None:
        self.config = config
        self.system = system
        self._client = None

    @property
    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise ImportError(
                    "anthropic SDK not installed. Run: pip install anthropic"
                ) from exc

            api_key = self.config.api_key or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError(
                    "No Anthropic API key found. Set the ANTHROPIC_API_KEY env var "
                    "or provide api-key in the config YAML."
                )

            self._client = anthropic.Anthropic(api_key=api_key)
            logger.info("Anthropic client initialised (model: %s)", self.config.model)

        return self._client

    # ------------------------------------------------------------------
    # Non-streaming (used by the REST endpoint)
    # ------------------------------------------------------------------

    def complete(self, messages: list[dict]) -> str:
        """Send messages and return the full assistant response as a string."""
        logger.debug("Sending %d message(s) to %s", len(messages), self.config.model)

        response = self.client.messages.create(
            model=self.config.model,
            max_tokens=2048,
            temperature=self.config.temperature,
            system=self.system,
            messages=messages,
        )

        # Extract text from the first content block
        text = "".join(
            block.text
            for block in response.content
            if hasattr(block, "text")
        )

        logger.debug(
            "LLM response: %d chars | input_tokens=%d output_tokens=%d",
            len(text),
            response.usage.input_tokens,
            response.usage.output_tokens,
        )

        return text

    # ------------------------------------------------------------------
    # Streaming (ready for SSE endpoint when needed)
    # ------------------------------------------------------------------

    def stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield text deltas as they arrive from the API."""
        with self.client.messages.stream(
            model=self.config.model,
            max_tokens=2048,
            temperature=self.config.temperature,
            system=self.system,
            messages=messages,
        ) as stream_ctx:
            for delta in stream_ctx.text_stream:
                yield delta
