# Forge server

Event-sourced agent engine. Requires [CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)
running locally (default `http://127.0.0.1:8317/v1`).

## Setup

    uv sync
    mkdir -p ~/.forge

`~/.forge/config.toml` (created with defaults on first boot; the api_key MUST be
set to your real CLIProxyAPI key — the default is a placeholder):

    base_url = "http://127.0.0.1:8317/v1"
    api_key = "sk-..."

Optional web tools (each tool only appears when its key is set):

    serper_api_key = "..."     # web_search via serper.dev
    firecrawl_api_key = "..."  # fetch_page via firecrawl.dev

Optional semantic memory retrieval (embeds memory regions, powers the
`remember` tool and per-message recall; off when unset):

    openrouter_api_key = "..."
    embedding_model = "google/gemini-embedding-001"  # default

Setting `openrouter_api_key` also enables `search_files`, a project-wide
semantic search over file contents (source, docs, PDFs) for sessions that
belong to a project. Indexing limits:

    file_search_max_file_bytes = 262144  # skip files larger than this (default)
    file_search_max_files = 5000         # cap on files indexed per project (default)

## Run

    make dev          # uvicorn on 127.0.0.1:8700 (serves web/dist if built)
    make test         # pytest
    make lint         # ruff
    make export-protocol   # JSON-Schema bundle for web codegen

Env: `FORGE_HOME` overrides `~/.forge`. Contents: `config.toml` (settings),
`projects.json` (project list + per-project default model/autonomy/effort).

Skills resolve from three places, later wins on name collision:
`forge/skills/` (stock, shipped with the app) → `~/.forge/skills/` (user) →
`<cwd>/.forge/skills/` (project).

## Shared workspace

Multiple sessions on the same resolved cwd edit one live working tree, serialized
by a single in-process mutation lock, with durable per-workspace activity/tree
tracking that drives stale-write, revert, and rewind guards. See
[../docs/shared-workspace.md](../docs/shared-workspace.md).
