---
name: image-generation
description: Use whenever a task involves generating or creating an image, logo, icon, illustration, app icon, favicon, banner, avatar, mockup, or any raster artwork from a text description — including "make a logo", "generate an icon for X", "create an image of…", "design a mark/emblem", "I need artwork/graphics". Activates the create_image tool (OpenRouter image models). Load this skill first to master the prompt before generating.
activates_tools: [create_image]
---

# Image Generation

Loading this skill activates the **`create_image`** tool. It generates an image
from a prompt via OpenRouter and saves it to a file. **Prompt quality is
everything** — a vague prompt yields a generic result. Master the prompt below
before you call the tool.

## Workflow

1. Clarify the intent: what is the image *for* (logo, icon, illustration,
   banner)? Where will it be used and at what size?
2. Compose a structured prompt (see below). State the intended use explicitly.
3. Call `create_image(prompt, path, …)`. Save to a sensible path (e.g.
   `assets/forge-logo.png`). The result is shown to you inline.
4. **Verify**: look at the returned image. Does it read at the target size? Is
   the style/color right? Is there unwanted text or clutter?
5. Refine with a single-change follow-up prompt, restating the invariants (see
   *Iterating*). Generate a few variations and keep the strongest.

## Prompt structure

Cover these slots, in roughly this order. Use short labeled lines for complex
prompts (`Subject:`, `Style:`, `Color:`, `Constraints:`) rather than one long
run-on sentence — modern models follow labeled, explicit instructions well.

- **Subject** — the one concrete thing depicted.
- **Style / medium** — the strongest lever: `flat vector`, `3D render`, `line
  art`, `watercolor`, `photographic`. Pick ONE visual language.
- **Composition / framing** — `centered`, `square 1:1`, `generous padding`,
  `top-down`, `clean negative space`.
- **Color** — 2–3 named colors, hex codes for consistency (`charcoal #1E1E24
  and ember orange #FF7A18`).
- **Background** — `plain white background`, `transparent RGBA PNG`, or a solid
  color. Use plain white while evaluating; transparent for a cut-out asset.
- **Constraints / exclusions** — `no text`, `no watermark`, `no gradients`,
  `no shadows`, `must read clearly at small sizes`.

Always state the **intended use** ("app icon", "logo", "banner") — it sets the
level of polish and framing.

## Logos & icons

Logos are the most common request and the easiest to get wrong. Rules:

- **One symbol concept only.** Every extra symbol roughly halves the odds of a
  usable mark. An anvil *or* a flame, not both fighting for space.
- **≤ 3 colors**, `no gradients` by default (matters for print, favicons, and
  dark mode). Add a gradient only deliberately.
- **Pick one style**: minimalist, geometric, monoline, emblem/badge,
  lettermark/monogram, or abstract mark. Don't mix ("minimalist retro 3D").
- **Icon only, no text.** Models render text poorly. Say `icon only` / `no
  text`. If you *must* have lettering (a monogram), put the literal text in
  quotes/ALL CAPS and keep it to 1–2 letters.
- **Square, centered, padded** for app icons/marks (`square 1:1`, `centered
  with generous padding`) so the mark isn't cropped.
- **Strong silhouette.** Judge a candidate by reducing it to grayscale at ~32px
  — if the shape doesn't read small, color won't save it.

Most reliable combo for a clean mark:
`flat vector logo + minimalist + centered on plain white background + no
gradients + no text`.

## Tool parameters

`create_image(prompt, path, size?, quality?, background?, n?)`:

- `path` — output file (the extension is set from the returned format).
- `size` — `1024x1024` (square, default-ish), `1536x1024` (landscape),
  `1024x1536` (portrait), or `auto`. Use square for icons/logos.
- `quality` — `low` | `medium` | `high` | `auto`.
- `background` — `opaque` or `transparent` (transparent → cut-out RGBA PNG for
  logos/icons). Support varies by model; if `transparent` isn't honored, ask
  for a "plain white background" and treat white as the key.
- `n` — 1–10; generate 3–4 variations, then pick the best. Multiple images are
  saved as `name-1.png`, `name-2.png`, …

## Iterating

To refine, restate the invariants every time so the style doesn't drift:

> Keep the same mark, style, colors, and composition. Change ONLY the flame —
> make it larger and rounder. Everything else stays identical.

## Failure modes → fixes

| Symptom | Fix |
|---|---|
| Generic/average mark | add industry + style + symbol + color + constraints |
| Busy / cluttered | one symbol concept only |
| Looks like an illustration, not a logo | add `flat vector`, `minimalist`; drop "realistic/detailed/3D" |
| Rendered on a scene/gradient/texture | `plain white background`, `centered`, `isolated` |
| Garbled text | `no text` / `icon only`; or quote + spell out letters |
| Style drift across edits | restate the preserve-list every turn |

## Example prompts (product: "Forge", a local AI coding agent)

**Anvil + spark, minimal geometric (good default):**
```
Create an original, non-infringing app icon for "Forge", a local AI coding agent.
Subject: a single stylized anvil as simple geometric shapes, one small spark above it.
Style: flat vector logo, minimalist, strong bold silhouette, clean negative space.
Color: deep charcoal (#1E1E24) and warm ember orange (#FF7A18), no gradients.
Composition: single mark centered in a square frame with generous padding.
Background: plain white.
Constraints: icon only, no text, no watermark, no shadows; must read clearly at 32px.
```

**Flame-in-negative-space "F" monogram:**
```
Flat vector logo mark for "Forge". A geometric letter "F" whose interior negative
space forms a rising flame. Minimalist, single symbol, bold silhouette, clean
negative space. Two colors only: charcoal and ember orange, no gradients. Centered
on a plain white background, square composition, icon only, no extra text.
```

**Hammer + code bracket fusion (tech signal):**
```
Minimalist flat vector logo for "Forge", a local AI coding agent. One symbol only:
a blacksmith hammer whose handle is an angular code bracket "< >". Simple geometric
shapes, strong silhouette. Slate gray and electric orange, flat design, no gradients,
no shadows. Centered icon on white background, square 1:1, icon only, no text.
```

## Reusable template

```
Create an original, non-infringing [logo / app icon / illustration] for [BRAND],
a [what it is].
Subject: [ONE concrete subject/symbol].
Style: [flat vector | 3D | line art | photographic], [minimalist | geometric | …],
       strong silhouette, clean negative space.
Color: [2–3 named colors + hex], no gradients.
Composition: single subject centered in a square (1:1) frame with generous padding.
Background: [plain white | transparent RGBA PNG | solid <color>].
Constraints: [icon only, no text, no watermark, no shadows]; must read at small sizes.
```

Note: AI output is a strong *concept starter*. For a production logo, rebuild the
winning silhouette as clean vector art in a design tool rather than shipping the
raster directly.
