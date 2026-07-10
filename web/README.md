# Forge web

React + Vite SPA for the Forge engine. Design source of truth:
`../design_handoff_agent_workspace/` (card 2a "Stream").

    pnpm install
    pnpm dev            # Vite on :5173, proxies /api and /ws to :8700
    pnpm test           # vitest
    pnpm build          # emits dist/ (served by the engine at :8700)
    pnpm gen:protocol   # regenerate src/protocol/generated.ts from the engine
