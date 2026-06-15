"""
orchestrator.py — the agent loop. The agent ORCHESTRATES tools; the tools COMPUTE.

A manual tool-calling loop (not a framework) so it's transparent and portable into the
Databricks App later: send messages + tool schemas -> if the model returns tool_calls, execute
them via tools.dispatch and feed results back -> repeat until the model answers with text.

The model asking a clarifying question is just a text turn with no tool calls — so the loop
naturally supports the "ask one question when a constraint is missing" behavior.

`llm` is injected (defaults to the real client) so tests can pass a scripted mock.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from . import llm_client, tools as tools_mod
from .prompts import SYSTEM_PROMPT


@dataclass
class AgentResult:
    final_text: str
    tool_trace: list[dict] = field(default_factory=list)   # visible tool calls (the agentic story)
    iterations: int = 0
    transcript: list[dict] = field(default_factory=list)


def _msg_to_dict(message) -> dict:
    """Normalize an OpenAI/SDK assistant message into a plain dict to append to `messages`."""
    if hasattr(message, "model_dump"):
        return message.model_dump(exclude_none=True)
    return dict(message)


def run(user_message: str, max_iters: int = 6, llm=llm_client.chat,
        system_prompt: str = SYSTEM_PROMPT) -> AgentResult:
    messages = [{"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}]
    trace: list[dict] = []

    for i in range(1, max_iters + 1):
        resp = llm(messages, tools=tools_mod.TOOLS, max_tokens=4096)
        msg = resp.choices[0].message
        tool_calls = getattr(msg, "tool_calls", None)

        if not tool_calls:
            # Final answer (or a clarifying question) — text turn, no tools.
            return AgentResult(final_text=msg.content or "", tool_trace=trace,
                               iterations=i, transcript=messages)

        messages.append(_msg_to_dict(msg))
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            result = tools_mod.dispatch(name, args)          # tools compute; never raises
            trace.append({"tool": name, "args": args, "result": result})
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, default=str)})

    # Hit the iteration cap — ask the model for a final answer with tools off.
    messages.append({"role": "user",
                     "content": "Summarize your recommendation now using the tool results above."})
    resp = llm(messages, tools=None, max_tokens=4096)
    return AgentResult(final_text=resp.choices[0].message.content or "",
                       tool_trace=trace, iterations=max_iters, transcript=messages)
