"""
model_client.py
────────────────
The Bedrock-ready abstracted model client.

This is the architectural centerpiece of TradeDesk's cloud-agnostic design.
Every single agent in the system calls THIS class, never the Anthropic SDK
or boto3 directly. The result: swapping from Anthropic API to AWS Bedrock
requires changing exactly ONE line in config.py (MODEL_PROVIDER).

Why does this matter?
  In production AI infrastructure, you rarely want to be locked into a
  single provider's SDK. API pricing changes, rate limits differ by region,
  enterprise clients may require data to stay within AWS/GCP boundaries.
  An abstracted client costs ~100 lines of code to build and saves a
  potential week of refactoring if you ever need to migrate.

  This is the pattern AWS calls "provider-agnostic AI integration" and
  it's specifically covered in the Claude with Amazon Bedrock course.

Usage:
  from src.client.model_client import ModelClient
  client = ModelClient()
  response = client.create_message(
      model="fast",           # "fast" or "reasoning"
      messages=[...],
      system="...",
      tools=[...],
      use_thinking=False,
  )
  # response.content, response.stop_reason — same interface regardless of provider
"""

import anthropic
from config import (
    MODEL_PROVIDER, ANTHROPIC_API_KEY,
    MODEL_FAST, MODEL_REASONING,
    BEDROCK_MODEL_FAST, BEDROCK_MODEL_REASONING, BEDROCK_REGION,
    MAX_TOKENS_SUBAGENT, MAX_TOKENS_SYNTHESIS, THINKING_BUDGET,
)


class ModelClient:
    """
    Provider-agnostic Claude client.

    Wraps either the Anthropic SDK or AWS Bedrock depending on config,
    presenting a unified interface to all agents.
    """

    def __init__(self):
        self.provider = MODEL_PROVIDER
        if self.provider == "anthropic":
            self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        elif self.provider == "bedrock":
            self._client = self._init_bedrock()
        else:
            raise ValueError(f"Unknown MODEL_PROVIDER: {self.provider}")

    def _init_bedrock(self):
        """Initialize AWS Bedrock client via boto3."""
        try:
            import boto3
            return boto3.client(
                service_name="bedrock-runtime",
                region_name=BEDROCK_REGION,
            )
        except ImportError:
            raise ImportError("boto3 is required for Bedrock. Run: pip install boto3")

    def create_message(self, model: str, messages: list, system: str = "",
                       tools: list = None, use_thinking: bool = False,
                       max_tokens: int = None) -> object:
        """
        Create a message using the configured provider.

        Args:
            model:        "fast" (Haiku) or "reasoning" (Sonnet)
            messages:     List of message dicts
            system:       System prompt string
            tools:        List of tool schemas (optional)
            use_thinking: Enable extended thinking (Sonnet only)
            max_tokens:   Override default token limit

        Returns:
            Response object with .content and .stop_reason attributes
        """
        model_id = self._resolve_model(model)
        tokens = max_tokens or (MAX_TOKENS_SYNTHESIS if use_thinking else MAX_TOKENS_SUBAGENT)

        if self.provider == "anthropic":
            return self._anthropic_message(
                model_id, messages, system, tools, use_thinking, tokens
            )
        elif self.provider == "bedrock":
            return self._bedrock_message(
                model_id, messages, system, tools, use_thinking, tokens
            )

    def _resolve_model(self, model: str) -> str:
        """Map "fast"/"reasoning" to the correct model ID for the active provider."""
        if self.provider == "anthropic":
            return MODEL_FAST if model == "fast" else MODEL_REASONING
        else:
            return BEDROCK_MODEL_FAST if model == "fast" else BEDROCK_MODEL_REASONING

    def _anthropic_message(self, model_id, messages, system, tools,
                           use_thinking, max_tokens):
        """Call the Anthropic Messages API directly."""
        params = dict(
            model=model_id,
            max_tokens=max_tokens,
            messages=messages,
        )
        if system:
            params["system"] = system
        if tools:
            params["tools"] = tools
        if use_thinking:
            params["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}

        return self._client.messages.create(**params)

    def _bedrock_message(self, model_id, messages, system, tools,
                         use_thinking, max_tokens):
        """
        Call Claude via AWS Bedrock using the converse API.

        The Bedrock converse API uses a slightly different format than
        the Anthropic SDK — this method handles the translation so all
        agents remain unaware of the difference.
        """
        import json

        body = {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": max_tokens,
            "messages": messages,
        }
        if system:
            body["system"] = system
        if tools:
            body["tools"] = tools
        if use_thinking:
            body["thinking"] = {"type": "enabled", "budget_tokens": THINKING_BUDGET}

        response = self._client.invoke_model(
            modelId=model_id,
            body=json.dumps(body),
            contentType="application/json",
            accept="application/json",
        )

        # Parse and wrap Bedrock response to match Anthropic SDK shape
        result = json.loads(response["body"].read())
        return BedrockResponseWrapper(result)


class BedrockResponseWrapper:
    """
    Wraps a raw Bedrock response dict to expose the same interface
    as an Anthropic SDK response object (.content, .stop_reason).

    This is what makes the provider swap truly transparent to agents —
    they call response.content and response.stop_reason regardless of
    which provider produced the response.
    """

    def __init__(self, raw: dict):
        self._raw = raw
        self.stop_reason = raw.get("stop_reason", "end_turn")
        self.content = self._parse_content(raw.get("content", []))

    def _parse_content(self, raw_content: list) -> list:
        """Convert Bedrock content blocks to Anthropic SDK-compatible objects."""
        blocks = []
        for block in raw_content:
            if block.get("type") == "text":
                blocks.append(TextBlock(block["text"]))
            elif block.get("type") == "tool_use":
                blocks.append(ToolUseBlock(
                    id=block["id"],
                    name=block["name"],
                    input=block["input"],
                ))
            elif block.get("type") == "thinking":
                blocks.append(ThinkingBlock(block.get("thinking", "")))
        return blocks


class TextBlock:
    def __init__(self, text): self.type = "text"; self.text = text

class ToolUseBlock:
    def __init__(self, id, name, input):
        self.type = "tool_use"; self.id = id; self.name = name; self.input = input

class ThinkingBlock:
    def __init__(self, thinking): self.type = "thinking"; self.thinking = thinking
