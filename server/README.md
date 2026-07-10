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

## Run

    make dev          # uvicorn on 127.0.0.1:8700 (serves web/dist if built)
    make test         # pytest
    make lint         # ruff
    make export-protocol   # JSON-Schema bundle for web codegen

Env: `FORGE_HOME` overrides `~/.forge`.
