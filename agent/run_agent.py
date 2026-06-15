"""
run_agent.py — CLI demo for the Mission Copilot agent.

    set -a; . ./.env; set +a            # (optional) export env; config.py also auto-loads .env
    ./.venv/bin/python -m agent.run_agent "6 surgeons, 7 days, maternal health, from Patna"

Prints the visible tool calls (the agentic trace) and the agent's final recommendation.
"""

import sys

from .orchestrator import run
from .llm_client import describe


def main() -> None:
    query = " ".join(sys.argv[1:]).strip() or \
        "I have 6 volunteers for 7 days for maternal health. Where should we go?"
    info = describe()
    print(f"[model: {info['model']} via {info['mode']}]\n")
    print(f"USER: {query}\n" + "=" * 84)

    result = run(query)

    for step in result.tool_trace:
        args = ", ".join(f"{k}={v}" for k, v in step["args"].items())
        print(f"  → tool: {step['tool']}({args})")
    if result.tool_trace:
        print("=" * 84)
    print(result.final_text)
    print("=" * 84 + f"\n[{result.iterations} iteration(s), {len(result.tool_trace)} tool call(s)]")


if __name__ == "__main__":
    main()
