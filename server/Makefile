# server/Makefile
.PHONY: dev test lint export-protocol

dev:
	uv run python -m forge.api.app

test:
	uv run pytest -q

lint:
	uv run ruff check .

export-protocol:
	uv run python -m forge.protocol_export > ../web/src/protocol/schema.json
