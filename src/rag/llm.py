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
from dataclasses import dataclass, field
from typing import Any, Callable, Generator, Optional

from src.config.settings import LLMConfig

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tool result type returned by complete_with_tools
# ---------------------------------------------------------------------------

@dataclass
class ToolUseResponse:
    """
    Result of a complete_with_tools() call.

    answer:         the LLM's final natural-language response
    tool_name:      which tool was called (None if LLM answered directly)
    tool_input:     the arguments the LLM passed to the tool
    tool_result:    the value your code returned to the LLM
    input_tokens:   total input tokens across all turns
    output_tokens:  total output tokens across all turns
    """
    answer: str
    tool_name: Optional[str] = None
    tool_input: Optional[dict] = None
    tool_result: Optional[Any] = None
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient:
    """
    Anthropic Claude client.

    Parameters
    ----------
    config:      LLM section of the YAML config
    system:      system prompt string (built externally in prompt.py)
    """

    # Models that deprecated the temperature parameter (adaptive thinking)
    _NO_TEMPERATURE_MODELS = ("claude-sonnet-5", "claude-opus-4-8", "claude-fable-5")

    def __init__(self, config: LLMConfig, system: str) -> None:
        self.config = config
        self.system = system
        self._client = None

    def _supports_temperature(self) -> bool:
        return not any(self.config.model.startswith(m) for m in self._NO_TEMPERATURE_MODELS)

    def _base_kwargs(self, messages: list[dict]) -> dict:
        """Build the common kwargs dict for all API calls."""
        kwargs: dict[str, Any] = dict(
            model=self.config.model,
            max_tokens=2048,
            system=self.system,
            messages=messages,
        )
        if self._supports_temperature():
            kwargs["temperature"] = self.config.temperature
        return kwargs

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

        response = self.client.messages.create(**self._base_kwargs(messages))

        text = "".join(
            block.text for block in response.content if hasattr(block, "text")
        )

        logger.debug(
            "LLM response: %d chars | input_tokens=%d output_tokens=%d",
            len(text), response.usage.input_tokens, response.usage.output_tokens,
        )
        return text

    # ------------------------------------------------------------------
    # Tool use — multi-turn cycle
    # ------------------------------------------------------------------

    def complete_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Any],
    ) -> ToolUseResponse:
        """
        Run a tool-use conversation loop:

          Turn 1: send messages + tool definitions → LLM either answers
                  directly (stop_reason="end_turn") or requests a tool call
                  (stop_reason="tool_use").
          Turn 2: execute the requested tool locally, send the result back
                  as a tool_result message → LLM produces its final answer.

        Parameters
        ----------
        messages:       conversation history (user turn already appended)
        tools:          Anthropic tool definitions (list of dicts with
                        name / description / input_schema)
        tool_executor:  callable(tool_name, tool_input) → any JSON-serialisable
                        value.  Called when the LLM requests a tool.

        Returns
        -------
        ToolUseResponse with the final answer and metadata about what was called.
        """
        import json

        total_input_tokens  = 0
        total_output_tokens = 0
        tool_name:   Optional[str]  = None
        tool_input:  Optional[dict] = None
        tool_result: Optional[Any]  = None

        # ── Turn 1 ───────────────────────────────────────────────────────
        kwargs = self._base_kwargs(messages)
        kwargs["tools"] = tools

        logger.debug("Tool-use turn 1: sending %d tool definition(s)", len(tools))
        response = self.client.messages.create(**kwargs)

        total_input_tokens  += response.usage.input_tokens
        total_output_tokens += response.usage.output_tokens

        # ── Did the LLM answer directly? ─────────────────────────────────
        if response.stop_reason != "tool_use":
            answer = "".join(
                b.text for b in response.content if hasattr(b, "text")
            )
            logger.info("LLM answered directly (no tool called).")
            return ToolUseResponse(
                answer=answer,
                input_tokens=total_input_tokens,
                output_tokens=total_output_tokens,
            )

        # ── LLM requested a tool ─────────────────────────────────────────
        tool_use_block = next(
            b for b in response.content if b.type == "tool_use"
        )
        tool_name  = tool_use_block.name
        tool_input = tool_use_block.input
        tool_id    = tool_use_block.id

        logger.info("LLM called tool: %s(%s)", tool_name, json.dumps(tool_input))

        # Execute locally
        tool_result = tool_executor(tool_name, tool_input)
        logger.debug("Tool result type: %s", type(tool_result).__name__)

        # ── Turn 2: send tool result back ────────────────────────────────
        # Append the assistant's tool_use turn and our tool_result
        followup_messages = messages + [
            {"role": "assistant", "content": response.content},
            {
                "role": "user",
                "content": [
                    {
                        "type":        "tool_result",
                        "tool_use_id": tool_id,
                        "content":     json.dumps(tool_result),
                    }
                ],
            },
        ]

        kwargs2 = self._base_kwargs(followup_messages)
        # Tools still available in case the LLM wants to chain calls,
        # though we don't loop further than one tool call for now.
        kwargs2["tools"] = tools

        logger.debug("Tool-use turn 2: sending tool result back to LLM")
        response2 = self.client.messages.create(**kwargs2)

        total_input_tokens  += response2.usage.input_tokens
        total_output_tokens += response2.usage.output_tokens

        answer = "".join(
            b.text for b in response2.content if hasattr(b, "text")
        )

        logger.info(
            "Tool-use complete | tool=%s | input_tokens=%d output_tokens=%d",
            tool_name, total_input_tokens, total_output_tokens,
        )

        return ToolUseResponse(
            answer=answer,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_result=tool_result,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
        )

    # ------------------------------------------------------------------
    # Streaming (ready for SSE endpoint when needed)
    # ------------------------------------------------------------------

    def stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield text deltas as they arrive from the API."""
        with self.client.messages.stream(**self._base_kwargs(messages)) as stream_ctx:
            for delta in stream_ctx.text_stream:
                yield delta
