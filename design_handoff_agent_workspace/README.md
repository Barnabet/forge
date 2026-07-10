# Handoff: Agent Workspace — "Stream" (option 2a)

## Overview
Main workspace screen for **Forge** (working name), a local, super-powerful agent app — a web SPA in the spirit of Claude Code / Codex desktop clients. It manages coding, general file handling, browser/computer-use automation, parallel agent sessions, and a background task queue. The chosen direction is **2a "Stream"**: chat-first, with the agent's work rendered as inline tool cards in the conversation, a slide-in detail drawer for diffs/files, and approval gates inline in the stream.

## About the Design Files
The files in this bundle are **design references created in HTML** — prototypes showing intended look and behavior, **not production code to copy directly**. The task is to **recreate this design in the target codebase's existing environment** (React, Vue, Svelte, etc.) using its established patterns and libraries. If no frontend environment exists yet, choose the most appropriate SPA framework (React + Vite is a safe default) and implement the design there.

The design file is `Agent Workspace Options.dc.html` (open it in a browser with `support.js` alongside). It contains multiple exploration rounds on a pan/zoom canvas; **implement only the card labeled `2a`** (badge "2a", screen label "2a Stream refined"). Earlier options (1a, 1b, 1c, 2b) are rejected explorations kept for context.

## Fidelity
**High-fidelity.** Colors, typography, spacing, radii, and shadows are final. Recreate pixel-perfectly with the codebase's component patterns. All measurements below are CSS px at a 1360×830 reference viewport; the real app is fluid (chat column flexes, drawer is fixed-width).

## Screens / Views

### Workspace (single screen, three regions)

**Frame**: full-viewport column; background `#0a0a0c`; text `#ececef`; font `Geist`, monospace `Geist Mono` (Google Fonts); `-webkit-font-smoothing: antialiased`.

#### 1. Top bar (h 52, full width)
- Border-bottom `1px solid rgba(255,255,255,.06)`; background `linear-gradient(180deg, #0e0e11, #0b0b0d)`; padding 0 18px; flex row, gap 16.
- **Brand**: 20×20 logo tile, radius 6, `linear-gradient(135deg, ACCENT, color-mix(in oklab, ACCENT 60%, #0a0a0c))`, glow `0 0 12px color-mix(in oklab, ACCENT 30%, transparent)`; product name 13.5px / 600 / letter-spacing −.01em.
- **Session tabs**: segmented control — container `rgba(255,255,255,.04)`, radius 9, padding 3. Active tab: bg `#1a1a20`, border `1px solid rgba(255,255,255,.08)`, radius 7, 12px/500, shadow `0 1px 2px rgba(0,0,0,.4)`, 6px status dot in ACCENT with `0 0 6px ACCENT` glow. Inactive tabs: text `#9d9da8`, dots `#e0b34b` (working/queued) or `#3d3d47` (idle). Trailing `+` button `#62626d`.
- **Right**: queue pill (`2 queued`, 11.5px, border `rgba(255,255,255,.07)`, radius 999, amber 5px dot) and working directory in Geist Mono 11.5px `#62626d`.

#### 2. Chat stream (flexible center)
- Subtle top light: `radial-gradient(1200px 500px at 50% -200px, rgba(255,255,255,.025), transparent)`.
- Content column max-width 700, centered, 24px side padding, 22px vertical gap between turns.
- **User message**: right-aligned bubble, max-width 500; bg `#1b1b21`; border `1px solid rgba(255,255,255,.06)`; padding 11px 16px; radius `16 16 6 16`; 13.5px/1.6 `#e3e3e8`; shadow `0 2px 8px rgba(0,0,0,.3)`.
- **Agent prose**: plain text, no bubble; 13.5px/1.65 `#b9b9c2`; 13px gap to its tool cards.
- **Tool card** (terminal runs, file edits, any tool call): bg `#0e0e11`; border `1px solid rgba(255,255,255,.07)`; radius 12; shadow `inset 0 1px 0 rgba(255,255,255,.04), 0 2px 10px rgba(0,0,0,.25)`.
  - Header row: padding 9px 13px, Geist Mono 11px `#9d9da8`; leading 18×18 icon tile (radius 5, bg `color-mix(in oklab, ACCENT 13%, transparent)`, glyph in ACCENT: `▸` running/ran, `✓` done); command/filename in `#e3e3e8`; diff stats `+n` `#6fd598` / `−n` `#ee8484`; right-aligned duration/meta `#4c4c56`; header divider `rgba(255,255,255,.05)` when a body follows.
  - Body (terminal output / file list): padding 10px 15px, Geist Mono 11.5px/1.75 `#8f8f9a`; truncation lines `#4c4c56`.
- **Approval gate (guarded mode)**: bg `linear-gradient(180deg, #16130a, #111008)`; border `1px solid rgba(229,184,75,.25)`; radius 12; padding 14px 16px; outer glow `0 0 24px rgba(229,184,75,.06)`. Leading 30×30 `⚠` tile (radius 8, bg `rgba(229,184,75,.12)`, glyph `#e5b84b`). Title 12.5px/600 `#eac26a`; command Geist Mono 11.5px `#b9b9c2`. Buttons (right): **Allow** — bg ACCENT, text INK, 12px/600, radius 8, shadow `0 4px 14px color-mix(in oklab, ACCENT 30%, transparent), inset 0 1px 0 rgba(255,255,255,.25)`; **Deny** and **Always ⌄** — ghost, border `rgba(255,255,255,.09)`, text `#9d9da8`.
- **Auto-approved line (autopilot mode)**: replaces the gate; styled like a tool-card header only (radius 12, `✓` tile, "auto-approved" + command, right meta `policy: full autonomy`).
- **Status line**: 12px `#8f8f9a` with 7px ACCENT dot glowing `0 0 8px ACCENT`; e.g. "Waiting on approval · step 6 of ~12" (guarded) / "Running tests · step 6 of ~12" (autopilot).
- **Composer** (bottom, 16/24/22 padding): card max-width 700; bg `#131317`; border `1px solid rgba(255,255,255,.09)`; radius 14; padding 13px 15px; shadow `0 12px 32px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.05)`. Placeholder 13.5px `#55555f` "Reply, steer, or queue another task…". Footer row: `@ files` and `/ commands` chips (Geist Mono 10.5px, border `rgba(255,255,255,.08)`, radius 7); right: model pill `opus-5 · guarded` (radius 999, bg `rgba(255,255,255,.04)`, Geist Mono 10.5px) and 28×28 send button (radius 8, bg ACCENT, `↑` in INK, shadow `0 4px 12px color-mix(in oklab, ACCENT 35%, transparent), inset 0 1px 0 rgba(255,255,255,.3)`).

#### 3. Detail drawer (w 480, right, dismissible)
- Border-left `rgba(255,255,255,.06)`; bg `#0c0c0f`. Opens when a tool card's "open panel" is clicked; hosts diff / file / blame views (extends to doc previews, images, browser sessions).
- **Header** (padding 11px 15px, hairline bottom): breadcrumb path — dir in Geist Mono 11px `#62626d`, filename 12px `#ececef`; stat chips `+41` (bg `rgba(111,213,152,.1)`, text `#6fd598`) and `−38` (bg `rgba(238,132,132,.1)`, text `#ee8484`), Geist Mono 10.5px, radius 5; right: Diff/File/Blame segmented control (same pattern as session tabs, 10.5px).
- **Diff body**: Geist Mono 11.5px/1.8. Gutter column w 44, right-aligned, padding-right 12: line numbers `#3f3f49`, `−` `#6e4040`, `+` `#3e6e50`. Hunk headers `#4c4c56`. Context text `#8f8f9a`. Deletions: row bg `rgba(238,132,132,.07)`, text `#e89b9b`. Additions: row bg `rgba(111,213,152,.07)`, text `#86d9a8`. 8px gap between hunks.
- **Footer** (hairline top, padding 11px 15px): "2 of 14 files changed" 11.5px `#8f8f9a`; right: **Revert** ghost button; **Keep all** — bg `#22222a`, border `rgba(255,255,255,.1)`, inset highlight, 12px/500.

## Interactions & Behavior
- **Session tabs** switch the active agent session; each keeps its own stream, drawer state, and queue entry. `+` starts a new session.
- **Tool cards** stream in as the agent works: header appears immediately (`▸` running), body streams, glyph flips to `✓` on completion. Diff-edit cards link to the drawer ("open panel →").
- **Approval gate** blocks the run (status line shows waiting). Allow → executes, gate collapses into a completed tool card. Deny → agent asks for direction. "Always ⌄" → dropdown scoping a standing policy (e.g. always allow pytest). In **autopilot** the gate is replaced by the auto-approved line and nothing blocks.
- **Drawer**: slides in ~240ms ease-out; chat column reflows. Revert / Keep all act on the current changeset; file pager steps through changed files.
- **Composer**: `@` opens fuzzy file picker, `/` opens command palette; Enter sends, mid-run messages steer the agent.
- **Hover states** (not drawn; follow this system): buttons/tabs brighten border to `rgba(255,255,255,.14)` and bg by ~4% white; accent buttons lift shadow slightly; tool-card headers show pointer + reveal right-side actions.
- **Loading**: glowing accent dot on the status line; running tool card shows `▸` in accent.

## State Management
- `sessions[]` — id, name, status (running / attention / queued / idle), cwd, branch, stream, drawerState.
- `activeSessionId`.
- `stream[]` per session — items: userMessage | agentProse | toolCall {kind, command, status, output, diffStats} | approvalRequest {command, riskKind, status}.
- `autonomy` — 'guarded' | 'autopilot' (drives approval gates, status copy, model pill label).
- `drawer` — open flag, file, view (diff/file/blame), changesetIndex.
- `queue[]` — pending/scheduled tasks (top-bar pill count).
- Data: WebSocket (or similar) stream of agent events appending to `stream[]`; approval responses posted back on the same channel.

## Design Tokens
- **ACCENT** = `#35e0c2` (teal, default). **INK** (text on accent) = `color-mix(in oklab, ACCENT 20%, #050505)`. Alternate palettes shipped in the prototype's tweaks: amber `#f0a832`, violet `#8b7cf6`, coral `#ef7259`. Accent tints are always derived via `color-mix`, never hardcoded: 13% tile bg, 30–35% shadow glow, 60% logo gradient end.
- **Backgrounds**: app `#0a0a0c`; bar gradient `#0e0e11→#0b0b0d`; card `#0e0e11`; drawer `#0c0c0f`; composer `#131317`; raised `#1a1a20`/`#1b1b21`/`#22222a`.
- **Text**: primary `#ececef`, body `#b9b9c2`, secondary `#9d9da8`, muted `#8f8f9a`, faint `#62626d`/`#55555f`, ghost `#4c4c56`/`#3f3f49`.
- **Semantic**: success/add `#6fd598` (dim `#86d9a8`, bg `rgba(111,213,152,.07–.1)`); danger/del `#ee8484` (dim `#e89b9b`, bg `rgba(238,132,132,.07–.1)`); warning `#e5b84b`/`#e0b34b` (title tint `#eac26a`).
- **Hairlines**: `rgba(255,255,255,.05 / .06 / .07 / .08 / .09)` by elevation.
- **Radii**: 5 (icon tiles), 7–8 (buttons, chips), 9 (segmented containers), 12 (cards), 14 (composer), 999 (pills).
- **Shadows**: inset top highlight `inset 0 1px 0 rgba(255,255,255,.04–.05)`; card `0 2px 10px rgba(0,0,0,.25)`; composer `0 12px 32px rgba(0,0,0,.45)`; accent glows via color-mix (see above).
- **Type scale**: 13.5 body/composer · 12.5 approval title · 12 buttons/tabs · 11.5 mono body/meta · 11 card headers · 10.5 chips/pills · headings letter-spacing −.01em. Weights 400/500/600/700.
- **Spacing**: 22 between turns · 13 prose→cards · card padding 9–14 / 13–16 · bar/drawer padding 15–18.

## Assets
- **Fonts**: Geist + Geist Mono (Google Fonts). No other assets — the logo is a CSS gradient tile (placeholder; replace with the real mark), icons are text glyphs (`▸ ✓ ⚠ ↑ ⌄`) — swap for the codebase's icon set (e.g. Lucide: play, check, alert-triangle, arrow-up, chevron-down).

## Files
- `Agent Workspace Options.dc.html` — the design canvas; implement card **2a** ("2a Stream refined"). 1a/1b/1c/2b are rejected explorations. Tweakable props on the file (palette / autonomy / productName) demonstrate the theming + autonomy behavior described above.
- `support.js` — runtime needed to open the HTML file locally; not part of the design.
