# server/Makefile
.PHONY: dev dev-web test lint export-protocol

dev:
	uv run python -m forge.api.app

dev-web:
	cd ../web && pnpm dev

test:
	uv run pytest -q

lint:
	uv run ruff check .

export-protocol:
	uv run python -m forge.protocol_export > ../web/src/protocol/schema.json
