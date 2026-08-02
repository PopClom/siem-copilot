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

        # ── LLM requested one or more tools ──────────────────────────────
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]

        # Track the first tool call for metadata (tool_name / tool_input)
        tool_name   = tool_use_blocks[0].name
        tool_input  = tool_use_blocks[0].input

        logger.info(
            "LLM called %d tool(s): %s",
            len(tool_use_blocks),
            ", ".join(f"{b.name}({json.dumps(b.input)[:60]})" for b in tool_use_blocks),
        )

        # Execute all tool calls and collect results
        tool_results: list[Any] = []
        tool_result_blocks: list[dict] = []
        for block in tool_use_blocks:
            result = tool_executor(block.name, block.input)
            tool_results.append(result)
            tool_result_blocks.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result),
            })
            logger.debug("Tool %s result type: %s", block.name, type(result).__name__)

        # Use the first result for metadata (consistent with single-tool behaviour)
        tool_result = tool_results[0] if tool_results else None

        # ── Turn 2: send all tool results back ───────────────────────────
        # The assistant content must include ALL blocks from response.content
        # (text + tool_use), and the user turn must have ONE tool_result per
        # tool_use block, in the same order.
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type":  "tool_use",
                    "id":    block.id,
                    "name":  block.name,
                    "input": block.input,
                })

        followup_messages = messages + [
            {"role": "assistant", "content": assistant_content},
            {"role": "user",      "content": tool_result_blocks},
        ]

        for i, msg in enumerate(followup_messages):
            content = msg["content"]
            if isinstance(content, list):
                types = [b.get("type", "?") if isinstance(b, dict) else b.type for b in content]
                logger.debug("followup_messages[%d] role=%s content_types=%s", i, msg["role"], types)
            else:
                logger.debug("followup_messages[%d] role=%s content=%r", i, msg["role"], str(content)[:80])

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
    # Streaming (SSE)
    # ------------------------------------------------------------------

    def stream(self, messages: list[dict]) -> Generator[str, None, None]:
        """Yield text deltas as they arrive from the API."""
        with self.client.messages.stream(**self._base_kwargs(messages)) as stream_ctx:
            for delta in stream_ctx.text_stream:
                yield delta

    def stream_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        tool_executor: Callable[[str, dict], Any],
    ) -> Generator[str, None, None]:
        """
        Tool use + streaming hybrid:
          Turn 1 — síncrono: LLM picks tool, tool is executed
          Turn 2 — streaming: LLM final answer is streamed token by token

        Yields str tokens. Caller wraps in SSE.
        Before yielding tokens, yields two special sentinel strings:
          "__TOOL__:<json>"   — emitted once when a tool is called
          "__DONE__:<json>"   — emitted at end with metadata
        """
        import json

        # ── Turn 1: síncrono ─────────────────────────────────────────────
        kwargs = self._base_kwargs(messages)
        kwargs["tools"] = tools

        response = self.client.messages.create(**kwargs)

        # ── No tool called — stream the direct answer ─────────────────────
        if response.stop_reason != "tool_use":
            # Re-stream from the already-completed response as character chunks
            text = "".join(b.text for b in response.content if hasattr(b, "text"))
            for char in text:
                yield char
            yield f"__DONE__:{json.dumps({'tool_used': None})}"
            return

        # ── Tool called — execute, then stream turn 2 ────────────────────
        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        tool_name = tool_use_blocks[0].name

        # Signal to the frontend which tool is running
        yield f"__TOOL__:{json.dumps({'tool': tool_name})}"

        tool_result_blocks: list[dict] = []
        combined_result: dict = {}
        for block in tool_use_blocks:
            result = tool_executor(block.name, block.input)
            if isinstance(result, dict) and not combined_result:
                combined_result = result
            tool_result_blocks.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     json.dumps(result),
            })

        # Build followup messages
        assistant_content = []
        for block in response.content:
            if block.type == "text":
                assistant_content.append({"type": "text", "text": block.text})
            elif block.type == "tool_use":
                assistant_content.append({
                    "type": "tool_use", "id": block.id,
                    "name": block.name, "input": block.input,
                })

        followup = messages + [
            {"role": "assistant", "content": assistant_content},
            {"role": "user",      "content": tool_result_blocks},
        ]

        # ── Turn 2: stream the final answer ──────────────────────────────
        kwargs2 = self._base_kwargs(followup)
        kwargs2["tools"] = tools

        with self.client.messages.stream(**kwargs2) as stream_ctx:
            for delta in stream_ctx.text_stream:
                yield delta

        yield f"__DONE__:{json.dumps({'tool_used': tool_name, 'sources': combined_result.get('sources', [])})}"
