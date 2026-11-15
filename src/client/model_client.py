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

Bedrock uses the Converse API, not raw InvokeModel:
  Bedrock's raw InvokeModel operation (passing Anthropic's native JSON
  body straight through) works for plain text and extended thinking, but
  tool use through that path was unreliable in testing (ValidationException
  on every tool-bearing request, regardless of inference profile). The
  Converse API is AWS's purpose-built, actively-maintained interface for
  tool calling across every Bedrock model, so this client translates to
  and from that format instead. Agents remain completely unaware of this —
  they still just call create_message() and get back the same
  .content / .stop_reason shape either way.

Known limitation — native web search:
  Anthropic's server-executed "web_search" tool (used directly against the
  Anthropic API) has no equivalent in Bedrock's Converse API — it's a
  Claude-API-hosted capability, not something Bedrock proxies. When running
  on Bedrock, any tool of that type is silently dropped before the request
  is sent, so agents that rely purely on web search (News, and Macro's
  search calls) will answer from the model's training knowledge instead of
  live results. This is a platform gap, not a bug — a real fix would mean
  wiring up an external search API (e.g. Tavily, Brave) as a Bedrock tool.

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

import re

from config import (
    MODEL_PROVIDER, ANTHROPIC_API_KEY,
    MODEL_FAST, MODEL_REASONING,
    BEDROCK_MODEL_FAST, BEDROCK_MODEL_REASONING, BEDROCK_MODEL_REGION,
    BEDROCK_API_KEY_SET, IAM_KEYS_SET,
    MAX_TOKENS_SUBAGENT, MAX_TOKENS_SYNTHESIS, THINKING_BUDGET,
    SYNTHESIS_EFFORT,
)


def _is_legacy_model(model_id: str) -> bool:
    """
    True for the Claude 3.x / 4.x generation, False for 5.x and later.

    The two generations take different thinking settings:
      4.x  → thinking is off unless asked for, and is asked for with a
             fixed token budget ({"type": "enabled", "budget_tokens": N}).
      5.x  → thinking is ON by default, a fixed budget is rejected with a
             400 error, and depth is controlled by an "effort" level.
    """
    match = re.search(r"claude-(?:[a-z]+-)?(\d+)", model_id)
    return bool(match) and int(match.group(1)) < 5


def thinking_fields(model_id: str, use_thinking: bool) -> dict:
    """
    The provider-neutral thinking settings for one request. The same dict
    is valid for the Anthropic API (as top-level params) and for Bedrock
    Converse (inside additionalModelRequestFields).
    """
    if _is_legacy_model(model_id):
        if use_thinking:
            return {"thinking": {"type": "enabled", "budget_tokens": THINKING_BUDGET}}
        return {}

    if use_thinking:
        # "summarized" asks for readable reasoning text back; the 5.x
        # default ("omitted") returns an empty thinking block.
        return {
            "thinking": {"type": "adaptive", "display": "summarized"},
            "output_config": {"effort": SYNTHESIS_EFFORT},
        }

    # Thinking is on by default on 5.x, so the research agents must turn
    # it off explicitly. Two reasons: it would eat into their small
    # max_tokens, and thinking blocks would have to be replayed unchanged
    # on every turn of the tool loop (this client drops them).
    if "sonnet-5-5" in model_id:
        # Sonnet 5.5 rejects "disabled"; this is its lowest setting.
        return {"thinking": {"type": "between_tools"},
                "output_config": {"effort": "medium"}}
    return {"thinking": {"type": "disabled"}}


class ModelClient:
    """
    Provider-agnostic Claude client.

    Wraps either the Anthropic SDK or AWS Bedrock depending on config,
    presenting a unified interface to all agents.
    """

    def __init__(self):
        self.provider = MODEL_PROVIDER
        if self.provider == "anthropic":
            # Imported here, not at the top of the file, so a Bedrock-only
            # deployment never needs the anthropic package or its API key.
            import anthropic
            if not ANTHROPIC_API_KEY:
                raise RuntimeError(
                    'MODEL_PROVIDER is "anthropic" but ANTHROPIC_API_KEY is not set. '
                    'Set the key, or switch MODEL_PROVIDER to "bedrock" in config.py.'
                )
            self._client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        elif self.provider == "bedrock":
            self._client = self._init_bedrock()
        else:
            raise ValueError(f"Unknown MODEL_PROVIDER: {self.provider}")

    def _init_bedrock(self):
        """
        Initialize the AWS Bedrock client via boto3.

        boto3 reads the credentials from the environment by itself. If a
        Bedrock API key (AWS_BEARER_TOKEN_BEDROCK) is set it is used for
        Bedrock calls; otherwise the IAM keys are. An ANTHROPIC_API_KEY
        is never used on this path.
        """
        try:
            import boto3
        except ImportError:
            raise ImportError("boto3 is required for Bedrock. Run: pip install boto3")

        if not (BEDROCK_API_KEY_SET or IAM_KEYS_SET):
            # Not fatal: boto3 can also find credentials in ~/.aws or an
            # attached IAM role (e.g. on App Runner). Just make the most
            # common misconfiguration easy to spot in the logs.
            print("  [ModelClient] ⚠ Neither AWS_BEARER_TOKEN_BEDROCK nor "
                  "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY is set in the environment.")

        if BEDROCK_API_KEY_SET:
            major, minor = (int(x) for x in boto3.__version__.split(".")[:2])
            if (major, minor) < (1, 39):
                raise RuntimeError(
                    f"boto3 {boto3.__version__} is too old to use a Bedrock API key "
                    "(AWS_BEARER_TOKEN_BEDROCK). Run: pip install -U boto3"
                )

        from botocore.config import Config
        return boto3.client(
            service_name="bedrock-runtime",
            region_name=BEDROCK_MODEL_REGION,
            config=Config(
                connect_timeout=10,
                read_timeout=120,
                max_pool_connections=20,
                retries={"max_attempts": 4, "mode": "adaptive"},
            ),
        )

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
            use_thinking: Enable thinking (used by the synthesis agent)
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
        params.update(thinking_fields(model_id, use_thinking))

        return self._client.messages.create(**params)

    # ── Bedrock via the Converse API ────────────────────────────────────────

    def _bedrock_message(self, model_id, messages, system, tools,
                         use_thinking, max_tokens):
        """
        Call Claude via AWS Bedrock's Converse API.

        Converse has its own request/response shape, different from both
        the Anthropic SDK and raw Bedrock InvokeModel — this method
        translates in both directions so agents (built against the
        Anthropic-native message format) never need to know Bedrock is
        involved at all.
        """
        params = dict(
            modelId=model_id,
            messages=self._to_converse_messages(messages),
            inferenceConfig={"maxTokens": max_tokens},
        )
        if system:
            params["system"] = [{"text": system}]

        converse_tools = self._to_converse_tools(tools) if tools else []
        if converse_tools:
            params["toolConfig"] = {"tools": converse_tools}

        extra = thinking_fields(model_id, use_thinking)
        if extra:
            params["additionalModelRequestFields"] = extra

        response = self._client.converse(**params)
        return ConverseResponseWrapper(response)

    def _to_converse_messages(self, messages: list) -> list:
        """
        Translate Anthropic-native message history into Converse's format.

        Three shapes show up in practice, all produced by base_agent.py's
        tool-use loop:
          1. Initial user turn — content is a plain string.
          2. Assistant turn (after a tool_use response) — content is a list
             of our own TextBlock/ToolUseBlock/ThinkingBlock wrapper objects.
          3. User turn carrying tool results — content is a list of
             Anthropic-native {"type": "tool_result", ...} dicts.
        """
        converted = []
        for msg in messages:
            role = msg["role"]
            content = msg["content"]

            if isinstance(content, str):
                converted.append({"role": role, "content": [{"text": content}]})
                continue

            blocks = []
            for item in content:
                if isinstance(item, dict) and item.get("type") == "tool_result":
                    result_text = item.get("content", "")
                    if not isinstance(result_text, str):
                        result_text = str(result_text)
                    blocks.append({
                        "toolResult": {
                            "toolUseId": item["tool_use_id"],
                            "content": [{"text": result_text}],
                        }
                    })
                elif isinstance(item, TextBlock):
                    blocks.append({"text": item.text})
                elif isinstance(item, ToolUseBlock):
                    blocks.append({
                        "toolUse": {
                            "toolUseId": item.id,
                            "name": item.name,
                            "input": item.input,
                        }
                    })
                elif isinstance(item, ThinkingBlock):
                    # Reasoning content is echoed back only if the SDK
                    # requires it in history; safe to omit for our
                    # single-tool-loop use case (no multi-turn thinking).
                    continue
                elif isinstance(item, dict) and "text" in item:
                    blocks.append({"text": item["text"]})

            converted.append({"role": role, "content": blocks})

        return converted

    def _to_converse_tools(self, tools: list) -> list:
        """
        Translate Anthropic-native tool schemas into Converse's toolSpec
        format. Native server-executed tools (e.g. Anthropic's web_search,
        identified by a "type" field instead of an "input_schema") have no
        Converse equivalent and are dropped — see the module docstring.
        """
        converted = []
        for tool in tools:
            if "input_schema" not in tool:
                # e.g. WEB_SEARCH_TOOL — not representable via Converse, skip.
                continue
            converted.append({
                "toolSpec": {
                    "name": tool["name"],
                    "description": tool.get("description", ""),
                    "inputSchema": {"json": tool["input_schema"]},
                }
            })
        return converted


class ConverseResponseWrapper:
    """
    Wraps a Bedrock Converse API response to expose the same interface
    as an Anthropic SDK response object (.content, .stop_reason).
    """

    _STOP_REASON_MAP = {
        "tool_use": "tool_use",
        "end_turn": "end_turn",
        "max_tokens": "max_tokens",
        "stop_sequence": "stop_sequence",
        "content_filtered": "end_turn",
    }

    def __init__(self, raw: dict):
        self._raw = raw
        self.stop_reason = self._STOP_REASON_MAP.get(
            raw.get("stopReason", "end_turn"), "end_turn"
        )
        message = raw.get("output", {}).get("message", {})
        self.content = self._parse_content(message.get("content", []))

    def _parse_content(self, raw_content: list) -> list:
        """Convert Converse content blocks to Anthropic SDK-compatible objects."""
        blocks = []
        for block in raw_content:
            if "text" in block:
                blocks.append(TextBlock(block["text"]))
            elif "toolUse" in block:
                tu = block["toolUse"]
                blocks.append(ToolUseBlock(
                    id=tu["toolUseId"],
                    name=tu["name"],
                    input=tu.get("input", {}),
                ))
            elif "reasoningContent" in block:
                text = (
                    block["reasoningContent"]
                    .get("reasoningText", {})
                    .get("text", "")
                )
                blocks.append(ThinkingBlock(text))
        return blocks


class TextBlock:
    def __init__(self, text): self.type = "text"; self.text = text

class ToolUseBlock:
    def __init__(self, id, name, input):
        self.type = "tool_use"; self.id = id; self.name = name; self.input = input

class ThinkingBlock:
    def __init__(self, thinking): self.type = "thinking"; self.thinking = thinking
