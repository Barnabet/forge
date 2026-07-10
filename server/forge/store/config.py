from __future__ import annotations

import json
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel


class Policy(BaseModel):
    tool: str
    pattern: str


class ModelConfig(BaseModel):
    id: str
    display_name: str
    context_window: int = 200_000


DEFAULT_MODELS = [
    ModelConfig(id="claude-sonnet-4-5", display_name="sonnet-4.5"),
    ModelConfig(id="gpt-5.2", display_name="gpt-5.2", context_window=272_000),
]


class ForgeConfig(BaseModel):
    base_url: str = "http://127.0.0.1:8317/v1"
    api_key: str = "sk-forge"
    models: list[ModelConfig] = DEFAULT_MODELS
    default_model: str = ""
    default_autonomy: str = "yolo"
    max_concurrent: int = 3
    serper_api_key: str = ""     # enables web_search when set
    firecrawl_api_key: str = ""  # enables fetch_page when set
    max_subagents: int = 4
    subagent_max_turns: int = 12
    policies: list[Policy] = []

    def context_window(self, model_id: str) -> int:
        for m in self.models:
            if m.id == model_id:
                return m.context_window
        return 200_000


def load_config(home: Path) -> ForgeConfig:
    path = home / "config.toml"
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    cfg = ForgeConfig.model_validate(data)
    if not cfg.default_model:
        cfg.default_model = cfg.models[0].id
    return cfg


def save_global_policy(home: Path, policy: Policy) -> None:
    """Append a policy as TOML; crude but config.toml stays human-owned."""
    home.mkdir(parents=True, exist_ok=True)
    if policy in load_config(home).policies:
        return  # already persisted; don't duplicate
    path = home / "config.toml"
    # json.dumps yields a valid TOML basic string (same escaping rules), so
    # patterns containing quotes/backslashes round-trip instead of corrupting.
    block = (f"\n[[policies]]\ntool = {json.dumps(policy.tool)}\n"
             f"pattern = {json.dumps(policy.pattern)}\n")
    path.write_text((path.read_text() if path.exists() else "") + block)


def policy_matches(policies: list[Policy], tool: str, display: str) -> bool:
    return any(p.tool == tool and fnmatch(display, p.pattern) for p in policies)
