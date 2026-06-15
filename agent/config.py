"""
config.py — the ONLY provider knob for the agent.

Provider-agnostic via the OpenAI-compatible chat-completions interface. Swap provider by
editing three env vars in .env (LLM_BASE_URL / LLM_MODEL / LLM_API_KEY) — no code change:

  Databricks-served Claude (default): LLM_BASE_URL=.../serving-endpoints,
      LLM_MODEL=databricks-claude-opus-4-8, no LLM_API_KEY (auth from the Databricks CLI profile)
  OpenAI:    LLM_BASE_URL=https://api.openai.com/v1,    LLM_MODEL=gpt-...,         LLM_API_KEY=sk-...
  Anthropic: LLM_BASE_URL=https://api.anthropic.com/v1, LLM_MODEL=claude-opus-4-8, LLM_API_KEY=sk-ant-...
"""

import os
import shutil
from pathlib import Path


def _ensure_databricks_cli_on_path() -> None:
    """The Databricks SDK's OAuth (databricks-cli auth) shells out to the `databricks` binary.
    It's installed at ~/bin (not on the default PATH), so make it discoverable here."""
    if shutil.which("databricks"):
        return
    candidate = Path.home() / "bin"
    if (candidate / "databricks").exists():
        os.environ["PATH"] = f"{candidate}{os.pathsep}{os.environ.get('PATH', '')}"


def _load_dotenv() -> None:
    """Minimal .env loader (no dependency). Does not override already-exported env vars."""
    p = Path(__file__).resolve().parent.parent / ".env"
    if not p.exists():
        return
    for line in p.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()
_ensure_databricks_cli_on_path()

BASE_URL = os.environ.get("LLM_BASE_URL", "").rstrip("/")
MODEL = os.environ.get("LLM_MODEL", "databricks-claude-opus-4-8")
API_KEY = os.environ.get("LLM_API_KEY") or None
DATABRICKS_PROFILE = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")

# Databricks mode = a serving-endpoints base URL with no explicit key (auth via CLI profile).
IS_DATABRICKS = "serving-endpoints" in BASE_URL and not API_KEY
