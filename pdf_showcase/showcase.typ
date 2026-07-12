// ============================================================
// IMPRESSION — A Specimen of Everything This Press Can Print
// A risograph spot-color specimen manual, printed entirely in
// vector by Typst. Self-referential: "the press" = the engine.
// ============================================================

// ---- Ink tokens (spot colors) ----
#let paper  = rgb("#E9E4D6")   // recycled stock
#let ink    = rgb("#1A1712")   // key / black
#let pink   = rgb("#FF3D8B")   // fluoro pink
#let blue   = rgb("#0F6FC4")   // riso blue (federal)
#let yellow = rgb("#FFD400")   // riso yellow
#let green  = rgb("#00A76A")   // riso green
#let paper2 = rgb("#DFD8C6")   // slightly darker stock for panels

#let mono = "DejaVu Sans Mono"
#let disp = "Helvetica Neue"
#let body = "Helvetica"

// ---- Page setup ----
#set page(
  width: 210mm, height: 297mm,
  margin: (x: 20mm, y: 18mm),
  fill: paper,
)
#set text(font: body, size: 10pt, fill: ink)
#set par(leading: 0.62em)

// ---- Reusable atoms ------------------------------------------------

// registration target (the classic crosshair-in-circle)
#let regmark(size: 9mm, col: ink) = {
  box(width: size, height: size)[
    #place(center + horizon, circle(radius: size/2, stroke: 0.6pt + col))
    #place(center + horizon, circle(radius: size/6, stroke: 0.6pt + col))
    #place(center + horizon, line(start: (0%, 50%), end: (100%, 50%), stroke: 0.6pt + col))
    #place(center + horizon, line(start: (50%, 0%), end: (50%, 100%), stroke: 0.6pt + col))
  ]
}

// mono label / eyebrow
#let eyebrow(s, col: ink) = text(font: mono, size: 7.5pt, fill: col, tracking: 2pt)[#upper(s)]

// a "plate number" tab
#let plate(n, name, col) = {
  block(width: 100%)[
    #box(fill: col, inset: (x: 6pt, y: 3pt))[#text(font: mono, size: 8pt, fill: paper, weight: "bold")[PLATE #n]]
    #h(6pt)
    #text(font: mono, size: 8pt, fill: ink, tracking: 1.5pt)[#upper(name)]
  ]
}

// overprinting ink disc — multiply blend so inks mix on the stock
#let inkdisc(d, col, x, y) = place(top + left, dx: x, dy: y,
  circle(radius: d/2, fill: col))

// ============================================================
// PAGE 1 — COVER
// ============================================================
#{
  // corner registration marks
  place(top + left, dx: -8mm, dy: -6mm, regmark())
  place(top + right, dx: 8mm, dy: -6mm, regmark())
  place(bottom + left, dx: -8mm, dy: 6mm, regmark())
  place(bottom + right, dx: 8mm, dy: 6mm, regmark())

  // overprinting ink field — three fluoro discs mixing via multiply
  place(top + left, dx: 0mm, dy: 6mm, block(
    width: 170mm, height: 96mm, clip: true,
    {
      set block(spacing: 0pt)
      place(top+left, dx: 8mm,  dy: 4mm,  circle(radius: 34mm, fill: yellow.transparentize(15%)))
      place(top+left, dx: 62mm, dy: 8mm,  circle(radius: 34mm, fill: pink.transparentize(18%)))
      place(top+left, dx: 34mm, dy: 30mm, circle(radius: 34mm, fill: blue.transparentize(22%)))
      place(top+left, dx: 108mm,dy: 26mm, circle(radius: 26mm, fill: green.transparentize(20%)))
    }
  ))
}

#v(104mm)
#eyebrow("Specimen No. 01 — for the discerning operator", col: ink)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(4mm)

#text(font: disp, size: 74pt, weight: "black", tracking: -3pt, fill: ink)[
  IMPRESSION
]
#v(-3mm)
#text(font: disp, size: 20pt, weight: "medium", tracking: -0.5pt)[
  A specimen of everything this press can print.
]
#v(6mm)

#grid(columns: (1fr, 1fr, 1fr), gutter: 6mm,
  [#eyebrow("Engine", col: pink) \ #text(font: mono, size: 9pt)[Typst 0.15 — vector]],
  [#eyebrow("Inks", col: blue) \ #text(font: mono, size: 9pt)[5 spot + key]],
  [#eyebrow("Finish", col: green) \ #text(font: mono, size: 9pt)[pypdf post-press]],
)
#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt)[THE FORGE PRESS · SET IN HELVETICA NEUE & DEJAVU MONO],
  text(font: mono, size: 8pt)[210 × 297 mm · A4],
)
#pagebreak()

// ============================================================
// PAGE 2 — CONTENTS / RUN SHEET
// ============================================================
#eyebrow("Run sheet", col: pink)
#h(1fr)
#eyebrow("Impression No. 01", col: ink)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 34pt, weight: "black", tracking: -1.5pt)[Contents]
#v(4mm)

#let toc-row(n, title, desc, col) = grid(
  columns: (16mm, 1fr, 44mm),
  align: (left + horizon, left + horizon, right + horizon),
  inset: (y: 7pt),
  text(font: mono, size: 15pt, weight: "bold", fill: col)[#n],
  [#text(font: disp, size: 13pt, weight: "bold")[#title] \ #text(size: 8.5pt, fill: ink.transparentize(20%))[#desc]],
  box(width: 100%, height: 8mm)[
    #place(right + horizon, circle(radius: 4mm, fill: col))
  ],
)

#toc-row("03", "The Ink Library", "Spot-color swatches, coverage, and mixing table.", pink)
#line(length: 100%, stroke: 0.4pt + ink.transparentize(60%))
#toc-row("04", "Halftones & Gradients", "Vector dot grids and linear / radial / conic fills.", blue)
#line(length: 100%, stroke: 0.4pt + ink.transparentize(60%))
#toc-row("05", "Data, Plotted", "Bar, line, and donut — drawn as pure vector.", green)
#line(length: 100%, stroke: 0.4pt + ink.transparentize(60%))
#toc-row("06", "The Press, Diagrammed", "Ink path from source to sheet, as a flow.", yellow)
#line(length: 100%, stroke: 0.4pt + ink.transparentize(60%))
#toc-row("07", "Registration & Overprint", "Trapping, misregistration, and knock-out.", pink)
#line(length: 100%, stroke: 0.4pt + ink.transparentize(60%))
#toc-row("08", "Quality Control", "Sign-off form and colophon.", blue)

#v(1fr)

// ink coverage meter — a mini stacked bar demonstrating fills
#eyebrow("Estimated ink coverage, this impression", col: ink)
#v(2mm)
#let cov = ((pink, 34%, "Pink"), (blue, 28%, "Blue"), (yellow, 16%, "Yellow"), (green, 14%, "Green"), (ink, 8%, "Key"))
#block(width: 100%, height: 11mm, radius: 1mm, clip: true, {
  set align(left)
  stack(dir: ltr, ..cov.map(c => box(width: c.at(1), height: 11mm, fill: c.at(0))))
})
#v(1.5mm)
#grid(columns: (1fr,)*5, ..cov.map(c => [
  #box(width: 6pt, height: 6pt, fill: c.at(0), baseline: 0pt) #text(font: mono, size: 7.5pt)[#c.at(2) #c.at(1)]
]))
#pagebreak()

// ============================================================
// PAGE 3 — THE INK LIBRARY
// ============================================================
#plate("03", "The Ink Library", pink)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Five inks, and the black.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  Each ink is a solid spot color laid straight onto the stock. The house
  standard is six drums; every color on later plates is one of these, screened
  or overprinted. Swatches below carry their build value and paper reserve.
]
#v(5mm)

// swatch cards
#let swatch(name, col, hexs, note) = box(width: 100%)[
  #block(width: 100%, height: 30mm, fill: col, radius: 1.5mm)[
    #place(bottom + right, dx: -3mm, dy: -2.5mm, text(font: mono, size: 7pt, fill: paper.transparentize(15%))[#note])
  ]
  #v(1.5mm)
  #text(font: disp, size: 11pt, weight: "bold")[#name] \
  #text(font: mono, size: 8pt, fill: ink.transparentize(25%))[#hexs]
]

#grid(columns: (1fr,)*5, gutter: 4mm,
  swatch("Fluoro Pink", pink, "FF3D8B", "0.98 op"),
  swatch("Federal Blue", blue, "0F6FC4", "0.94 op"),
  swatch("Sun Yellow", yellow, "FFD400", "1.00 op"),
  swatch("Field Green", green, "00A76A", "0.96 op"),
  swatch("Key Black", ink, "1A1712", "1.00 op"),
)
#v(7mm)

// coverage rings (concentric-circle conic technique — always centered)
#let ring(pct, col, label) = box(width: 30mm)[
  #box(width: 26mm, height: 26mm)[
    #let rest = ink.transparentize(88%)
    #place(center+horizon, circle(radius: 13mm, fill: gradient.conic(
      (col, 0%), (col, pct), (rest, pct), (rest, 100%), angle: -90deg)))
    #place(center+horizon, circle(radius: 8.5mm, fill: paper))
    #place(center+horizon, text(font: mono, size: 11pt, weight: "bold", fill: ink)[#{calc.round(pct/1%)}%])
  ]
  #v(1mm)
  #align(center, text(font: mono, size: 7.5pt)[#label])
]

#grid(columns: (auto, 1fr), gutter: 8mm, align: horizon,
  grid(columns: 3, gutter: 3mm,
    ring(72%, pink, "PINK LOAD"),
    ring(58%, blue, "BLUE LOAD"),
    ring(40%, green, "GREEN LOAD"),
  ),
  [
    #eyebrow("Overprint mixing — two inks, one pass", col: ink)
    #v(2mm)
    #table(
      columns: (1fr, 1fr, 1.4fr),
      stroke: none,
      inset: 6pt,
      fill: (_, r) => if r == 0 { ink } else if calc.odd(r) { paper2 } else { none },
      table.header(
        text(fill: paper, font: mono, size: 8pt)[BASE],
        text(fill: paper, font: mono, size: 8pt)[SCREEN],
        text(fill: paper, font: mono, size: 8pt)[RESULT],
      ),
      [Yellow], [Pink], box(width: 100%, height: 6mm, fill: yellow, {place(left+horizon, box(width:70%, height:6mm, fill: pink.transparentize(30%)))}),
      [Blue], [Pink], box(width: 100%, height: 6mm, fill: blue, {place(left+horizon, box(width:70%, height:6mm, fill: pink.transparentize(30%)))}),
      [Yellow], [Blue], box(width: 100%, height: 6mm, fill: yellow, {place(left+horizon, box(width:70%, height:6mm, fill: blue.transparentize(30%)))}),
      [Green], [Pink], box(width: 100%, height: 6mm, fill: green, {place(left+horizon, box(width:70%, height:6mm, fill: pink.transparentize(30%)))}),
    )
  ],
)

#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt, fill: ink.transparentize(20%))[SPOT INKS ARE SOLID FILLS · OVERPRINTS USE MULTIPLY TRANSPARENCY],
  text(font: mono, size: 8pt)[03],
)
#pagebreak()

// ============================================================
// PAGE 4 — HALFTONES & GRADIENTS
// ============================================================
#plate("04", "Halftones & Gradients", blue)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Dots do the shading.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  There is no grey ink. A tone is a field of dots whose radius rises with
  demand — computed and drawn as vector, one circle at a time. Gradients are
  the smooth cousin: the same math, resolved continuously.
]
#v(5mm)

// --- vector halftone gradient: dot radius grows left->right ---
#eyebrow("Halftone ramp — 24 × 8 cells, radius by column", col: ink)
#v(2mm)
#let cols = 24
#let rows = 8
#block(width: 100%, height: 40mm, fill: paper2, radius: 1.5mm, inset: 4mm)[
  #let cw = 100% / cols
  #grid(columns: (1fr,)*cols, rows: (1fr,)*rows,
    ..range(rows*cols).map(i => {
      let c = calc.rem(i, cols)
      let t = c / (cols - 1)
      let r = 0.6mm + t * 2.0mm
      box(width: 100%, height: 100%)[#place(center+horizon, circle(radius: r, fill: pink))]
    })
  )
]
#v(6mm)

// --- three gradient chips ---
#eyebrow("Continuous fills", col: ink)
#v(2mm)
#let gchip(title, grad) = box(width: 100%)[
  #block(width: 100%, height: 42mm, radius: 1.5mm, fill: grad)
  #v(1.5mm)
  #text(font: mono, size: 8pt)[#title]
]
#grid(columns: (1fr, 1fr, 1fr), gutter: 4mm,
  gchip("linear · blue→pink, 45°", gradient.linear(blue, pink, angle: 45deg)),
  gchip("radial · yellow core→green", gradient.radial(yellow, green, radius: 70%)),
  gchip("conic · full ink wheel", gradient.conic(pink, blue, green, yellow, pink)),
)
#v(6mm)

// --- duotone-style bars: quantized steps ---
#eyebrow("Screened steps — 10% increments", col: ink)
#v(2mm)
#grid(columns: (1fr,)*10, gutter: 1.5mm,
  ..range(10).map(i => box(width: 100%, height: 12mm, radius: 0.8mm, fill: blue.transparentize(90% - i*9%)))
)
#v(1.5mm)
#grid(columns: (1fr,)*10, gutter: 1.5mm,
  ..range(10).map(i => align(center, text(font: mono, size: 7pt)[#{(i+1)*10}]))
)

#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt, fill: ink.transparentize(20%))[EVERY DOT AND RAMP IS RESOLUTION-INDEPENDENT VECTOR],
  text(font: mono, size: 8pt)[04],
)
#pagebreak()

// ============================================================
// PAGE 5 — DATA, PLOTTED (pure-vector charts)
// ============================================================
#plate("05", "Data, Plotted", green)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Numbers, given ink.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  No plotting library ran here. Bars are boxes, the line is a #raw("curve()"),
  the donut is concentric arcs — all laid out in flow, all sized from the data
  below. Impressions pulled, by month.
]
#v(6mm)

// --- BAR CHART ---
#let months = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug")
#let vals = (42, 58, 51, 73, 66, 88, 79, 95)
#let vmax = 100
#eyebrow("Impressions pulled — thousands / month", col: green)
#v(2mm)
#block(width: 100%, height: 46mm)[
  #grid(columns: (1fr,)*8, rows: (1fr, auto), column-gutter: 4mm,
    ..vals.map(v => box(width: 100%, height: 100%)[
      #place(bottom+center, box(width: 100%, height: (v/vmax)*100%, fill: green, radius: (top: 1mm)))
      #place(bottom+center, dy: -((v/vmax)*40mm)-3mm, text(font: mono, size: 8pt, weight: "bold")[#v])
    ]),
    ..months.map(m => align(center, text(font: mono, size: 8pt, fill: ink.transparentize(20%))[#m])),
  )
]
#v(8mm)

#grid(columns: (1.55fr, 1fr), gutter: 10mm, align: top,
  // --- LINE CHART ---
  [
    #eyebrow("Registration accuracy — microns off", col: blue)
    #v(2mm)
    #let ldata = (18, 12, 15, 8, 10, 5, 6, 3)
    #let lmax = 20
    #block(width: 100%, height: 46mm, fill: paper2, radius: 1.5mm, inset: 4mm)[
      #layout(sz => {
        let w = sz.width
        let h = sz.height
        let n = ldata.len()
        let pts = ldata.enumerate().map(((i, v)) => (
          (i/(n - 1)) * w,
          (1 - v/lmax) * h,
        ))
        // gridlines
        for g in (0, 0.5, 1) {
          place(top+left, dy: g*h, line(length: w, stroke: 0.4pt + ink.transparentize(70%)))
        }
        // area under curve
        place(top+left, curve(
          curve.move((0pt, h)),
          ..pts.map(p => curve.line(p)),
          curve.line((w, h)),
          curve.close(),
          fill: blue.transparentize(78%),
          stroke: none,
        ))
        // the line
        place(top+left, curve(
          curve.move(pts.first()),
          ..pts.slice(1).map(p => curve.line(p)),
          stroke: 1.6pt + blue,
        ))
        // markers
        for p in pts { place(top+left, dx: p.at(0)-1.4mm, dy: p.at(1)-1.4mm, circle(radius: 1.4mm, fill: paper, stroke: 1.2pt + blue)) }
      })
    ]
    #v(1mm)
    #text(font: mono, size: 7.5pt, fill: ink.transparentize(25%))[Lower is tighter. Target ≤ 5 µm by run's end — met.]
  ],
  // --- DONUT ---
  [
    #eyebrow("Sheets by grade", col: pink)
    #v(2mm)
    #let seg = ((62%, green, "A"), (24%, blue, "B"), (14%, pink, "C"))
    #box(width: 100%, height: 46mm)[
      #let start = 0%
      #place(center+horizon, circle(radius: 21mm, fill: gradient.conic(
        (green, 0%), (green, 62%),
        (blue, 62%), (blue, 86%),
        (pink, 86%), (pink, 100%),
        angle: -90deg)))
      #place(center+horizon, circle(radius: 13mm, fill: paper))
      #place(center+horizon, box[#align(center)[#text(font: disp, size: 15pt, weight: "black")[8,940] \ #text(font: mono, size: 7pt)[SHEETS]]])
    ]
    #v(1mm)
    #grid(columns: (auto, auto), column-gutter: 4mm, row-gutter: 1.5mm,
      ..seg.map(s => (
        box[#box(width:7pt,height:7pt,fill:s.at(1),baseline:0pt) #text(font: mono, size: 8pt)[Grade #s.at(2)]],
        text(font: mono, size: 8pt, weight: "bold")[#s.at(0)],
      )).flatten()
    )
  ],
)

#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt, fill: ink.transparentize(20%))[BARS · CURVE · CONCENTRIC ARCS — NO CHART LIBRARY, DATA-DRIVEN],
  text(font: mono, size: 8pt)[05],
)
#pagebreak()

// ============================================================
// PAGE 6 — THE PRESS, DIAGRAMMED (flow layout)
// ============================================================
#plate("06", "The Press, Diagrammed", yellow)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Source to sheet.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  How a page becomes an impression. Nodes and connectors are laid out on a grid
  with #raw("1fr") gutters — no eyeballed offsets, so the arrows always meet
  their boxes.
]
#v(7mm)

// --- flow nodes ---
#let node(n, title, sub, col) = {
  let fg = if col == yellow { ink } else { paper }
  box(width: 100%, height: 26mm, radius: 2mm, fill: col, inset: 4mm)[
    #text(font: mono, size: 7pt, fill: fg.transparentize(15%))[STAGE #n]
    #v(1fr)
    #text(font: disp, size: 12pt, weight: "bold", fill: fg)[#title] \
    #text(font: mono, size: 7.5pt, fill: fg.transparentize(20%))[#sub]
  ]
}
#let arrow = align(center + horizon, text(size: 20pt, fill: ink)[→])

#grid(columns: (1fr, 8mm, 1fr, 8mm, 1fr), align: horizon,
  node("01", "Markup", ".typ source", ink),
  arrow,
  node("02", "Compile", "layout engine", blue),
  arrow,
  node("03", "Rasterless", "vector PDF", green),
)
#v(6mm)
// down-connectors
#grid(columns: (1fr, 8mm, 1fr, 8mm, 1fr),
  align(center)[#text(size: 16pt, fill: ink)[↓]], [],
  align(center)[#text(size: 16pt, fill: ink)[↓]], [],
  align(center)[#text(size: 16pt, fill: ink)[↓]],
)
#v(6mm)
#grid(columns: (1fr, 8mm, 1fr, 8mm, 1fr), align: horizon,
  node("06", "Sheet", "the impression", pink),
  align(center + horizon, text(size: 20pt, fill: ink)[←]),
  node("05", "Post-press", "stamp · meta · marks", yellow),
  align(center + horizon, text(size: 20pt, fill: ink)[←]),
  node("04", "Proof", "render & verify", blue),
)
#v(9mm)

// --- drum schematic: the ink path ---
#eyebrow("Ink path — one drum, one pass", col: ink)
#v(3mm)
#block(width: 100%, height: 44mm, fill: paper2, radius: 2mm, inset: 5mm)[
  #layout(sz => {
    let w = sz.width
    let cy = 17mm
    // paper baseline
    place(left, dy: 34mm, line(length: w, stroke: (paint: ink, thickness: 1.4pt, dash: "densely-dotted")))
    place(right, dy: 30mm, text(font: mono, size: 7pt)[STOCK])
    // ink drum
    place(left, dx: 10mm, dy: cy - 12mm, circle(radius: 12mm, fill: pink, stroke: 1.2pt + ink))
    place(left, dx: 16.5mm, dy: cy - 5.5mm, text(font: mono, size: 7pt, fill: paper)[DRUM])
    // dot pattern falling from drum to sheet (the screen)
    for i in range(9) {
      let dx = 34mm + i*3.2mm
      let r = 0.5mm + calc.abs(4 - i)*0.18mm
      place(left, dx: dx, dy: cy + 2mm, circle(radius: r, fill: pink))
      place(left, dx: dx, dy: cy + 8mm, circle(radius: r*0.8, fill: pink.transparentize(20%)))
    }
    // labels + arrow to sheet
    place(left, dx: 30mm, dy: 4mm, text(font: mono, size: 7pt, fill: ink)[SCREEN → HALFTONE])
    // fixing / output roller
    place(left, dx: w - 24mm, dy: cy - 10mm, circle(radius: 10mm, fill: ink))
    place(left, dx: w - 21mm, dy: cy - 3mm, text(font: mono, size: 6.5pt, fill: paper)[FIX])
  })
]

#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt, fill: ink.transparentize(20%))[NODES ON A GRID · CONNECTORS IN GUTTERS · NO ABSOLUTE OFFSETS],
  text(font: mono, size: 8pt)[06],
)
#pagebreak()

// ============================================================
// PAGE 7 — REGISTRATION & OVERPRINT
// ============================================================
#plate("07", "Registration & Overprint", pink)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Where inks meet.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  Two drums never land in exactly the same place. The craft is in the seam:
  overprint to hide it, trap to bridge it, knock out to keep colors pure. Three
  studies, each a live composite.
]
#v(7mm)

#let study(title, note, gfx) = box(width: 100%)[
  #block(width: 100%, height: 52mm, fill: paper2, radius: 2mm, clip: true)[#gfx]
  #v(2mm)
  #text(font: disp, size: 11pt, weight: "bold")[#title] \
  #text(font: mono, size: 7.5pt, fill: ink.transparentize(25%))[#note]
]

#grid(columns: (1fr, 1fr, 1fr), gutter: 5mm,
  // 1. clean overprint (in register)
  study("In register", "Overprint mixes to a third color.", {
    place(center+horizon, dx: -6mm, circle(radius: 15mm, fill: yellow.transparentize(5%)))
    place(center+horizon, dx: 6mm, circle(radius: 15mm, fill: blue.transparentize(20%)))
  }),
  // 2. misregistration (offset + paper gap)
  study("Misregister", "Drums off by 3 mm — a paper seam shows.", {
    place(center+horizon, dx: -8mm, dy: -1.5mm, circle(radius: 15mm, fill: pink.transparentize(5%)))
    place(center+horizon, dx: 5mm, dy: 2mm, circle(radius: 15mm, fill: blue.transparentize(15%)))
  }),
  // 3. knockout (hole punched so top ink stays pure)
  study("Knock-out", "Base punched away; top ink prints pure.", {
    place(center+horizon, circle(radius: 16mm, fill: green))
    place(center+horizon, circle(radius: 8mm, fill: paper2))
    place(center+horizon, circle(radius: 7mm, fill: pink))
  }),
)
#v(9mm)

// --- trapping detail strip ---
#eyebrow("Trap gauge — spread in points", col: ink)
#v(3mm)
#block(width: 100%, height: 30mm, fill: ink, radius: 2mm, inset: 5mm)[
  #grid(columns: (1fr,)*6, column-gutter: 4mm, align: horizon,
    ..(0, 0.25, 0.5, 1, 1.5, 2).map(t => box(width: 100%)[
      #box(width: 100%, height: 12mm)[
        #place(center+horizon, box(width: 100%, height: 12mm, fill: blue))
        #place(center+horizon, box(width: (100% - t*8%), height: 8mm, fill: pink))
      ]
      #v(1.5mm)
      #align(center, text(font: mono, size: 7.5pt, fill: paper)[#{t}pt])
    ])
  )
]
#v(3mm)
#text(font: mono, size: 7.5pt, fill: ink.transparentize(25%))[
  A trap overlaps the lighter ink into the darker by a hair, so a slight
  misregister still shows no paper. House default: 0.5 pt.
]

#v(1fr)
#line(length: 100%, stroke: 0.6pt + ink)
#v(2mm)
#grid(columns: (1fr, auto),
  text(font: mono, size: 8pt, fill: ink.transparentize(20%))[COMPOSITES BUILT LIVE FROM TRANSPARENT + KNOCK-OUT FILLS],
  text(font: mono, size: 8pt)[07],
)
#pagebreak()

// ============================================================
// PAGE 8 — QUALITY CONTROL & COLOPHON
// ============================================================
#plate("08", "Quality Control", blue)
#v(2mm)
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#text(font: disp, size: 30pt, weight: "black", tracking: -1.2pt)[Signed off the press.]
#v(1mm)
#text(size: 10pt, fill: ink.transparentize(15%))[
  Every impression carries a sign-off. The boxes below are drawn as an
  interactive proof — the same document can be printed as a form (see the
  press's forms module) or stamped and locked in post.
]
#v(6mm)

// --- checklist ---
#let check(done, label) = grid(columns: (7mm, 1fr), align: horizon, inset: (y: 4pt),
  box(width: 4.5mm, height: 4.5mm, radius: 0.8mm, stroke: 1pt + ink,
    fill: if done { green } else { none })[
    #if done { place(center+horizon, text(fill: paper, size: 8pt, weight: "bold")[✓]) }],
  text(size: 10pt)[#label],
)
#grid(columns: (1fr, 1fr), column-gutter: 10mm,
  [
    #eyebrow("Press check", col: green)
    #v(1mm)
    #check(true, "Registration within 5 µm")
    #check(true, "Solid density on target")
    #check(true, "No banding in gradients")
    #check(false, "Deckle edge trimmed")
  ],
  [
    #eyebrow("Bindery", col: blue)
    #v(1mm)
    #check(true, "Page sequence verified")
    #check(true, "Bookmarks embedded")
    #check(false, "Cover laminated")
    #check(false, "Shrink-wrapped")
  ],
)
#v(7mm)

// --- sign-off block ---
#block(width: 100%, fill: paper2, radius: 2mm, inset: 6mm)[
  #eyebrow("Sign-off", col: ink)
  #v(3mm)
  #grid(columns: (1fr, 1fr, 1fr), column-gutter: 8mm,
    ..(("Operator", "J. Rivera"), ("Date", "2026 · 07 · 12"), ("Run №", "0001 / 0500")).map(f => [
      #box(width: 100%, stroke: (bottom: 0.8pt + ink), inset: (bottom: 2mm))[#text(font: mono, size: 10pt)[#f.at(1)]]
      #v(1mm)
      #text(font: mono, size: 7.5pt, fill: ink.transparentize(25%))[#upper(f.at(0))]
    ])
  )
]
#v(9mm)

// --- colophon ---
#line(length: 100%, stroke: 1.2pt + ink)
#v(3mm)
#grid(columns: (1.4fr, 1fr), column-gutter: 10mm,
  [
    #text(font: disp, size: 16pt, weight: "black", tracking: -0.6pt)[Colophon]
    #v(2mm)
    #text(size: 9.5pt, fill: ink.transparentize(10%))[
      This specimen was set and composed entirely in *Typst 0.15*, which
      resolves every rule, dot, ramp, arc, and chart on these pages to pure
      vector — no image was placed. It was then run through *pypdf* in
      post-press to stamp a proof mark, write document metadata, and stitch the
      outline you can navigate at left. Rendered and verified page by page
      before release.
    ]
  ],
  [
    #eyebrow("Specification", col: pink)
    #v(2mm)
    #set text(font: mono, size: 8pt)
    #grid(columns: (auto, 1fr), row-gutter: 3pt, column-gutter: 4mm,
      text(fill: ink.transparentize(35%))[FORMAT], [A4 · 210×297],
      text(fill: ink.transparentize(35%))[DISPLAY], [Helvetica Neue],
      text(fill: ink.transparentize(35%))[DATA], [DejaVu Mono],
      text(fill: ink.transparentize(35%))[INKS], [6 spot],
      text(fill: ink.transparentize(35%))[ENGINE], [Typst → pypdf],
      text(fill: ink.transparentize(35%))[PLATES], [8],
    )
  ],
)
#v(1fr)
#align(center)[#regmark(size: 7mm, col: ink)]
#v(2mm)
#align(center, text(font: mono, size: 8pt, fill: ink.transparentize(20%))[— END OF SPECIMEN · THE FORGE PRESS —])
