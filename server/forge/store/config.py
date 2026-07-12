from __future__ import annotations

import json
import os
import tomllib
from fnmatch import fnmatch
from pathlib import Path

from pydantic import BaseModel, Field


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
    max_resident_sessions: int = 24  # LRU cap on actors kept in memory
    serper_api_key: str = ""     # enables web_search when set
    firecrawl_api_key: str = ""  # enables fetch_page when set
    openrouter_api_key: str = ""  # enables embedding-based memory retrieval + create_image when set
    embedding_model: str = "google/gemini-embedding-001"
    image_model: str = "google/gemini-3.1-flash-lite-image"  # OpenRouter model for create_image
    memory_similarity_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    file_search_max_file_bytes: int = 262_144  # skip files larger than this when indexing
    file_search_max_files: int = 5_000  # cap on files indexed per project
    max_subagents: int = 4
    subagent_max_turns: int = 25
    subagent_model: str = ""  # empty = inherit the session's model
    memory_model: str = ""    # empty = inherit the session's model
    compaction_model: str = ""  # empty = inherit the session's model
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


def _toml_scalar(value: object) -> str:
    # bool must be checked before int (bool is a subclass of int)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    # json.dumps yields a valid TOML basic string (same escaping rules)
    return json.dumps(value)


def dump_config_toml(cfg: ForgeConfig) -> str:
    """Serialize a ForgeConfig to TOML that round-trips through load_config."""
    lines: list[str] = []
    for name in ForgeConfig.model_fields:
        if name in ("models", "policies"):
            continue
        lines.append(f"{name} = {_toml_scalar(getattr(cfg, name))}")
    for m in cfg.models:
        lines.append("")
        lines.append("[[models]]")
        lines.append(f"id = {json.dumps(m.id)}")
        lines.append(f"display_name = {json.dumps(m.display_name)}")
        lines.append(f"context_window = {m.context_window}")
    for p in cfg.policies:
        lines.append("")
        lines.append("[[policies]]")
        lines.append(f"tool = {json.dumps(p.tool)}")
        lines.append(f"pattern = {json.dumps(p.pattern)}")
    return "\n".join(lines) + "\n"


def save_config(home: Path, cfg: ForgeConfig) -> None:
    """Write home/config.toml atomically via a temp file + os.replace."""
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    tmp = path.with_suffix(".toml.tmp")
    tmp.write_text(dump_config_toml(cfg))
    os.replace(tmp, path)  # atomic on POSIX


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
