# Creating PDFs (LLM → PDF)

Pick the engine by the source material, then **always render-and-verify** (last
section). Do not declare a generated PDF done from source alone.

| Source / need | Engine |
|---|---|
| Data, reports, letters, invoices, most documents | **Typst** (primary) |
| A Markdown file | `pandoc … --pdf-engine=typst` |
| Content is naturally HTML/CSS, or brand CSS is provided | **WeasyPrint** |
| Pixel-exact overlays, labels, stamps, watermarks | reportlab |

## Typst (primary)

```bash
command -v typst || brew install typst
```
Why Typst: one small binary, compiles in milliseconds, and its compiler errors
point at the exact line — so the loop is tight: **write `doc.typ` →
`typst compile doc.typ out.pdf` → read stderr → fix → retry.** Iterate until it
compiles clean, then render-verify.

**Name the output file explicitly** (`typst compile doc.typ out.pdf`). A bare
`typst compile doc.typ` writes the PDF next to the source as `<stem>.pdf` (e.g.
`doc.pdf`) — *not* `doc.typ.pdf` — so guessing the path for `view` wastes a
round trip.

#### Custom graphics: prefer flow layout over absolute `place()`

When you hand-draw charts, rings, diagrams, or badges, **lay them out with flow
constructs (`grid`, `stack`, concentric `circle`s) rather than absolute
`place()` + hand-tuned `dx/dy` offsets.** `place` positions an element's
*bounding box* relative to an anchor, and these bite every time — they compile
clean and only show up in the render:

- **A shape whose bbox isn't symmetric drifts off-center.** A partial-arc
  `curve()` (e.g. a 64% progress ring) has a lopsided bbox, so
  `place(center, arc)` centers the *box*, not the visual arc — it slides off any
  concentric background. Draw rings as concentric circles with a
  `gradient.conic` (colored to `pct*100%`, grey after) and a white inner disc
  punched out; concentric circles share an exact center and can't misalign.
- **Eyeballed `dx` offsets between items aren't evenly spaced.** For a
  `A → B → C` flow, put the nodes and arrow glyphs in a
  `grid(columns: (w, 1fr, w, 1fr, w))`; the `1fr` gaps self-distribute and each
  cell self-centers. No manual offsets to drift.
- **`layout(size => …)` reports the OUTER page region, not the enclosing
  box/block** — deriving heights from `size.height` balloons them near
  full-page. Use `layout` only for width; drive heights off a fixed param.
- **A block that contains only `place()`d items has ZERO height,** so
  `place(bottom, …)` anchors to its top edge and content grows *upward* into
  whatever is above it. Wrap placements in `block(height: H)` with an explicit
  height so `bottom` has a real floor.

Rule of thumb: if geometry is *relational* (centered-on, evenly-spaced,
stacked), express it with layout; reserve `place` for true one-off overlays
(watermarks, page-corner stamps) and even then verify the render.

#### API traps that cost a compile cycle (get these right the first time)

These are the calls a fresh agent reaches for when hand-drawing graphics and
gets subtly wrong — each compiles-fails with a terse error, so pre-empt them.
The error string is quoted so you can match it if you hit it anyway.

- **Conic/linear gradient stops** — *"first stop must have an offset of 0"* /
  *"either all stops must have an offset or none of them can."* Each stop is a
  `(color, offset%)` pair, the **first offset must be `0%`**, and **hard-edged
  segments (pie/donut, progress ring) need the color duplicated at both
  boundaries** — a color doesn't "span up to" its offset, it *is* that color
  *at* that offset. A 60/25/15 donut and a 72% ring:
  ```typ
  gradient.conic((green,0%),(green,60%),(blue,60%),(blue,85%),(pink,85%),(pink,100%))
  gradient.conic((blue,0%),(blue,72%),(luma(220),72%),(luma(220),100%), angle: -90deg)
  ```
- **Hollow / outline text** — *"expected color, gradient, or tiling, found
  none."* `text(fill: none)` is rejected (unlike shapes, where `stroke: none`
  works). For a hollow watermark use a transparent fill plus a visible stroke:
  `text(fill: rgb(0,0,0,0), stroke: 2pt + col)[PROOF]` (or
  `col.transparentize(92%)` for a faint ghost interior).
- **`curve()` coordinates are lengths, not numbers** — *"expected relative
  length, found integer."* `curve.move((0, h))` fails; write `curve.move((0pt,
  h))`. When mapping data, multiply a ratio by a length: `(v/vmax) * w`, never a
  bare float.
- **`grid`/`stack` spacing** — *"expected …, found dictionary."* `grid` takes
  `column-gutter:` and `row-gutter:` (or a single `gutter:` for both), **not**
  `gutter: (x:.., y:..)`. `table` is the same. `stack` uses `spacing:`.
- **`align` values don't multiply** — `(center+horizon)*5` errors. Repeat the
  value or build the array explicitly.

### Template (a): report

```typst
#set page(
  paper: "a4",
  margin: (x: 2.5cm, y: 2cm),
  header: [#set text(9pt, gray); Q3 Financial Report #h(1fr) Acme Inc.],
  numbering: "1 / 1",
)
#set text(font: "Helvetica", size: 11pt)
#set heading(numbering: "1.1")
#set par(justify: true)

#align(center)[#text(20pt, weight: "bold")[Q3 Financial Report]]
#v(1em)

= Summary
Revenue grew 12% quarter over quarter.

== Regional detail

#figure(
  table(
    columns: (auto, 1fr, auto),
    align: (left, left, right),
    table.header([*Region*], [*Lead*], [*Revenue*]),
    [EMEA], [Nadia], [\$1.2M],
    [APAC], [Wei],   [\$0.9M],
    [AMER], [Jordan],[\$2.1M],
  ),
  caption: [Revenue by region.],
)

// Embed an image: #figure(image("chart.png", width: 80%), caption: [...])
```

### Template (b): letter

```typst
#set page(paper: "us-letter", margin: 1in)
#set text(font: "Times New Roman", size: 12pt)

#align(right)[Acme Inc. \ 100 Main St \ Springfield]
#v(2em)
July 11, 2026
#v(1em)
Dear Ms. Rivera,
#v(1em)
Thank you for your inquiry. #lorem(40)
#v(2em)
Sincerely, \ \
Jordan Lee \ Accounts
```

### Template (c): invoice from JSON input

Pass data with `typst compile invoice.typ out.pdf --input data='{...json...}'`
(or `--input data=@data.json` on newer Typst). Read it via `sys.inputs`:

```typst
#let data = json(bytes(sys.inputs.data))

#set page(paper: "a4", margin: 2cm)
#set text(size: 11pt)

#text(18pt, weight: "bold")[Invoice #data.number]
#v(0.5em)
Bill to: #data.client \
Date: #data.date
#v(1em)

#table(
  columns: (1fr, auto, auto, auto),
  align: (left, right, right, right),
  table.header([*Item*], [*Qty*], [*Unit*], [*Total*]),
  ..data.items.map(it => (
    it.name, str(it.qty), [\$#it.unit],
    [\$#(it.qty * it.unit)],
  )).flatten(),
)
#v(0.5em)
#align(right)[*Total: \$#data.items.map(it => it.qty * it.unit).sum()*]
```

### Reproducible fonts

To render identically on any machine, bundle fonts and ignore system ones:
```bash
typst compile doc.typ --font-path ./fonts --ignore-system-fonts
```

### Markdown source → Typst

```bash
command -v pandoc || brew install pandoc
pandoc file.md -o file.pdf --pdf-engine=typst
```

## WeasyPrint (HTML/CSS fallback)

Use when the content is already HTML/CSS or the user supplies brand CSS.
```bash
command -v weasyprint || brew install weasyprint
```

```html
<!doctype html><html><head><meta charset="utf-8"><style>
  @page {
    size: A4;
    margin: 2cm;
    @top-right { content: "Acme Inc. — Confidential"; font-size: 9px; color: #666; }
    @bottom-center { content: "Page " counter(page) " of " counter(pages); font-size: 9px; }
  }
  body { font: 11px/1.5 "Helvetica", sans-serif; }
  h1 { font-size: 20px; }
  table { width: 100%; border-collapse: collapse; break-inside: avoid; }
  th, td { border: 1px solid #ccc; padding: 6px; text-align: left; }
  thead { background: #f0f0f0; }
</style></head><body>
  <h1>Q3 Report</h1>
  <table><thead><tr><th>Region</th><th>Revenue</th></tr></thead>
  <tbody><tr><td>EMEA</td><td>$1.2M</td></tr></tbody></table>
</body></html>
```
```bash
weasyprint in.html out.pdf
```
**WeasyPrint silently ignores CSS it doesn't support** (much of flexbox/grid,
some paged-media features) — never trust it without a render-verify. Avoid it
for 100+ page documents; it gets slow.

## reportlab (only for pixel-exact placement)

Reach for reportlab **only** when you need exact coordinates — overlays,
mailing labels, watermark/stamp pages. For prose or tables, Typst is better.
Note: reportlab built-in fonts lack Unicode sub/superscript glyphs (they render
as black boxes) — use `<sub>`/`<super>` in a `Paragraph` instead.

## Render & verify (required last step)

Call the **`view` tool** — it renders the pages and returns them as images
you can actually see:

```
view(path="out.pdf", pages="1-3", dpi=120)
```
Look at the rendered pages, confirm the layout, and fix-and-regenerate until
right. (Scriptable fallback: `python3 <skill>/scripts/pdf_to_images.py out.pdf
/tmp/pdfcheck` writes PNGs to disk — then `view` any of them directly.)
