"""
base_agent.py
──────────────
Shared agentic loop logic inherited by all 5 TradeDesk subagents.

Why a base class?
  All 5 subagents follow the same fundamental loop:
    1. Receive a task
    2. Call tools to gather evidence
    3. Return a structured JSON finding
  The only differences are: which tools they have access to, what
  system prompt they use, and what JSON schema they return.

  Putting the loop in a base class means each subagent is ~80 lines
  of focused domain logic rather than 200 lines of repeated boilerplate.
  This is the same separation of concerns pattern used in the
  Introduction to Subagents course.

Context isolation:
  Each subagent instantiates its own ModelClient — meaning each one
  gets a completely fresh API call with no shared message history.
  This is what makes them true subagents rather than a single agent
  with multiple roles: their contexts are isolated by construction,
  not by convention.
"""

import json
from src.client.model_client import ModelClient
from src.mcp_server.market_tools import MarketToolExecutor


class BaseAgent:
    """
    Base class for all TradeDesk subagents.

    Subclasses must implement:
      - SYSTEM_PROMPT: str
      - TOOLS: list of tool schemas this agent has access to
      - agent_name: str (for logging)
      - run(ticker, **kwargs) -> dict
    """

    SYSTEM_PROMPT = ""
    TOOLS = []
    agent_name = "BaseAgent"

    def __init__(self, executor: MarketToolExecutor):
        self.client = ModelClient()
        self.executor = executor

    def _run_loop(self, user_message: str, use_web_search: bool = False,
                  verbose: bool = True) -> str:
        """
        Core agentic tool-use loop.

        Runs until Claude stops requesting tools (stop_reason != "tool_use")
        or until a safety limit of 8 turns is reached.

        Args:
            user_message:    The task description sent to this subagent
            use_web_search:  Include the native web_search tool
            verbose:         Print tool calls to console

        Returns:
            Claude's final text response (the subagent's structured finding)
        """
        from src.mcp_server.market_tools import WEB_SEARCH_TOOL

        tools = list(self.TOOLS)
        if use_web_search:
            tools.append(WEB_SEARCH_TOOL)

        messages = [{"role": "user", "content": user_message}]
        max_turns = 8

        for turn in range(max_turns):
            response = self.client.create_message(
                model="fast",
                messages=messages,
                system=self.SYSTEM_PROMPT,
                tools=tools if tools else None,
            )

            # ── Tool use requested ────────────────────────────────
            if response.stop_reason == "tool_use":
                tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
                messages.append({"role": "assistant", "content": response.content})

                tool_results = []
                for block in tool_use_blocks:
                    if verbose:
                        print(f"    [{self.agent_name}] → {block.name}"
                              f"({json.dumps(block.input)[:60]}...)")

                    # web_search is handled natively by the API — its result
                    # comes back automatically in the next response, so we
                    # don't need to execute it ourselves
                    if block.name == "web_search":
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": "Web search executed by API.",
                        })
                    else:
                        result_json = self.executor.execute(block.name, block.input)
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": result_json,
                        })

                messages.append({"role": "user", "content": tool_results})

            # ── Final answer ──────────────────────────────────────
            else:
                return "".join(
                    block.text for block in response.content
                    if hasattr(block, "text")
                )

        return '{"error": "Agent exceeded maximum turns without producing a final answer"}'

    def _parse_json(self, raw: str) -> dict:
        """
        Safely parse JSON from agent output.
        Strips markdown code fences if present.
        """
        text = raw.strip()
        # Strip ```json ... ``` or ``` ... ``` fences
        if "```" in text:
            import re
            # Extract content between first ``` and last ```
            match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
            if match:
                text = match.group(1)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"raw_output": raw, "parse_error": True}