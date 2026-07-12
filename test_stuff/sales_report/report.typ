#let data = json("data.json")
#let ink = rgb("#172420")
#let forest = rgb("#174C3C")
#let moss = rgb("#6E8B68")
#let coral = rgb("#E56B52")
#let saffron = rgb("#E5A93D")
#let sky = rgb("#76A9B7")
#let paper = rgb("#F4F0E7")
#let cream = rgb("#FBF8F1")
#let mist = rgb("#DCE4DC")
#let muted = rgb("#65716B")
#let hair = rgb("#C9D0C8")

#set page(
  paper: "a4",
  margin: (x: 16mm, top: 15mm, bottom: 14mm),
  fill: paper,
  footer: context [
    #set text(font: "Avenir Next", size: 7pt, fill: muted)
    #line(length: 100%, stroke: .4pt + hair)
    #v(3pt)
    #grid(columns: (1fr, auto), [REVENUE CURRENTS · 2024 SALES], [#counter(page).display("01")])
  ],
)
#set text(font: "Avenir Next", size: 9pt, fill: ink)
#set par(leading: 0.62em)

#let money(n) = {
  let s = str(calc.round(n))
  let out = ""
  for (i, ch) in s.clusters().rev().enumerate() {
    if i > 0 and calc.rem(i, 3) == 0 { out = "," + out }
    out = ch + out
  }
  "$" + out
}
#let compact(n) = if n >= 1000 { "$" + str(calc.round(n / 100) / 10) + "k" } else { money(n) }
#let pct(n) = str(calc.round(n * 1000) / 10) + "%"
#let eyebrow(body) = text(font: "Avenir Next", size: 7pt, weight: 700, tracking: 1.3pt, fill: coral, body)
#let title(body) = text(font: "Iowan Old Style", size: 27pt, weight: 500, fill: ink, body)
#let section-head(no, name, deck) = [
  #grid(columns: (13mm, 1fr), column-gutter: 4mm,
    [#text(font: "Iowan Old Style", size: 29pt, fill: coral)[#no]],
    [#eyebrow([ANNUAL SALES REPORT]) #v(2pt) #text(font: "Iowan Old Style", size: 23pt, weight: 500)[#name] #v(2pt) #text(size: 8pt, fill: muted)[#deck]]
  )
  #v(7mm)
]
#let rule-label(label, value) = [
  #grid(columns: (1fr, auto), [#text(size: 7.5pt, fill: muted)[#label]], [#text(size: 8pt, weight: 700)[#value]])
  #v(2pt)#line(length: 100%, stroke: .45pt + hair)
]
#let metric(label, value, note) = block(fill: cream, inset: 5mm, radius: 1.5mm, width: 100%)[
  #eyebrow(label)
  #v(3pt)
  #text(font: "Iowan Old Style", size: 22pt, weight: 500)[#value]
  #v(2pt)
  #text(size: 7pt, fill: muted)[#note]
]
#let bar-row(name, val, maxv, color: forest, suffix: none) = [
  #grid(columns: (38mm, 1fr, 18mm), column-gutter: 3mm,
    [#text(size: 7.4pt)[#name]],
    [#box(width: 100%, height: 3.2mm, fill: mist, radius: 2mm)[#box(width: val / maxv * 100%, height: 100%, fill: color, radius: 2mm)]],
    [#align(right)[#text(size: 7.2pt, weight: 700)[#if suffix == none { compact(val) } else { suffix }]]]
  )
  #v(3.2mm)
]

// COVER
#set page(margin: (x: 17mm, top: 17mm, bottom: 14mm), fill: ink)
#set text(fill: paper)
#eyebrow([A YEAR IN MOTION])
#v(9mm)
#text(font: "Iowan Old Style", size: 44pt, weight: 500, fill: paper)[Revenue\\Currents]
#v(4mm)
#text(size: 10pt, fill: sky)[A visual anatomy of 1,000 sales across 2024]
#v(12mm)

// Monthly sales translated into a data-driven field of light.
#let max-month = calc.max(..data.months.map(m => m.net))
#block(height: 82mm, width: 100%)[
  #align(bottom)[
    #grid(columns: ((1fr,) * 12), column-gutter: 2.3mm,
      ..data.months.map(m => {
        let count = int(calc.round(m.net / max-month * 20))
        align(bottom, stack(dir: btt, spacing: 1.7mm,
          ..range(count).map(i => {
            let fade = 82% - i * 2.4%
            circle(radius: 1.25mm, fill: if m.month == "Oct" { coral.transparentize(fade) } else { sky.transparentize(fade) })
          })
        ))
      })
    )
  ]
]
#v(4mm)
#grid(columns: ((1fr,) * 12), column-gutter: 2.3mm,
  ..data.months.map(m => align(center)[#text(size: 6pt, fill: paper.transparentize(35%))[#m.month]])
)
#v(13mm)
#line(length: 100%, stroke: .5pt + paper.transparentize(72%))
#v(6mm)
#grid(columns: (1.25fr, 1fr, 1fr), column-gutter: 12mm,
  [#text(font: "Iowan Old Style", size: 21pt, fill: paper)[#money(data.summary.net)] #v(2pt) #text(size: 6.5pt, tracking: .7pt, fill: paper.transparentize(35%))[NET SALES]],
  [#text(font: "Iowan Old Style", size: 21pt, fill: paper)[#data.summary.orders] #v(2pt) #text(size: 6.5pt, tracking: .7pt, fill: paper.transparentize(35%))[ORDERS]],
  [#text(font: "Iowan Old Style", size: 21pt, fill: paper)[#pct(data.summary.realization)] #v(2pt) #text(size: 6.5pt, tracking: .7pt, fill: paper.transparentize(35%))[REVENUE KEPT]],
)
#v(11mm)
#grid(columns: (1fr, auto),
  [#text(size: 7pt, fill: paper.transparentize(38%))[SOURCE · SAMPLE SALES DATA]],
  [#text(size: 7pt, fill: paper.transparentize(38%))[#data.summary.period]],
)

// PAGE 2
#pagebreak()
#set page(margin: (x: 16mm, top: 15mm, bottom: 14mm), fill: paper)
#set text(fill: ink)
#section-head("01", [The pulse], [A year of uneven momentum, with October setting the high-water mark.])
#grid(columns: (1fr, 1fr, 1fr), column-gutter: 4mm,
  metric([NET SALES], money(data.summary.net), [after #money(data.summary.discount_value) in discounts]),
  metric([AVERAGE ORDER], money(data.summary.avg_order), [#data.summary.avg_units units per order]),
  metric([CUSTOMER BASE], str(data.summary.customers), [unique customer names in the file]),
)
#v(8mm)
#eyebrow([MONTHLY NET SALES])
#v(4mm)
#let chart-max = calc.max(..data.months.map(m => m.net))
#block(height: 64mm, width: 100%)[
  #align(bottom)[
    #grid(columns: ((1fr,) * 12), column-gutter: 2.2mm,
      ..data.months.map(m => align(bottom)[
        #stack(dir: btt, spacing: 2mm,
          [#box(width: 100%, height: m.net / chart-max * 48mm, fill: if m.month == "Oct" { coral } else { forest }, radius: (top-left: 1.5mm, top-right: 1.5mm))],
          [#align(center)[#text(size: 6pt, weight: if m.month == "Oct" {700} else {400})[#m.month]]]
        )
      ])
    )
  ]
]
#v(4mm)
#grid(columns: (1.15fr, 1fr), column-gutter: 10mm,
  [
    #eyebrow([READING THE RHYTHM])
    #v(3mm)
    #text(font: "Iowan Old Style", size: 15pt)[October reached #money(data.summary.best_month.net), #pct(data.summary.best_month.net / data.summary.net) of the year in a single month.]
    #v(3mm)
    #text(size: 8pt, fill: muted)[The low point came in June at #money(data.summary.low_month.net). Sales then rebounded #pct((data.months.at(6).net / data.summary.low_month.net) - 1) in July, showing that the mid-year trough was sharp rather than persistent.]
  ],
  [
    #eyebrow([QUARTERLY FLOW])
    #v(3mm)
    #let qs = range(4).map(q => data.months.slice(q*3, q*3+3).fold(0, (acc, m) => acc + m.net))
    #for (i, q) in qs.enumerate() [#bar-row("Q" + str(i+1), q, calc.max(..qs), color: (forest, moss, saffron, coral).at(i))]
  ]
)

// PAGE 3
#pagebreak()
#section-head("02", [The portfolio], [A concentrated engine: premium workspace products carry the commercial story.])
#grid(columns: (1.02fr, .98fr), column-gutter: 10mm,
  [
    #eyebrow([CATEGORY COMPOSITION])
    #v(5mm)
    #let catcols = (forest, coral, saffron, sky, moss)
    #for (i, c) in data.categories.enumerate() [
      #grid(columns: (1fr, auto),
        [#text(size: 8pt, weight: 600)[#c.name]],
        [#text(size: 8pt, weight: 700)[#pct(c.share)]]
      )
      #v(1.5mm)
      #box(width: 100%, height: 5mm, fill: mist, radius: 3mm)[#box(width: c.share * 100%, height: 100%, fill: catcols.at(i), radius: 3mm)]
      #v(4mm)
    ]
  ],
  [
    #block(fill: ink, inset: 7mm, radius: 2mm)[
      #text(font: "Iowan Old Style", size: 19pt, fill: paper)[Two categories create nearly the whole horizon.]
      #v(5mm)
      #text(font: "Iowan Old Style", size: 36pt, fill: coral)[#pct(data.categories.at(0).share + data.categories.at(1).share)]
      #v(2mm)
      #text(size: 7.5pt, fill: paper.transparentize(28%))[of net sales came from Electronics and Furniture, despite representing only #pct((data.categories.at(0).orders + data.categories.at(1).orders) / data.summary.orders) of orders.]
      #v(7mm)
      #line(length: 100%, stroke: .5pt + paper.transparentize(70%))
      #v(5mm)
      #text(size: 7.5pt, fill: paper.transparentize(28%))[The portfolio is value-led rather than volume-led: Stationery produced #data.categories.at(4).units units but only #pct(data.categories.at(4).share) of revenue.]
    ]
  ]
)
#v(10mm)
#eyebrow([PRODUCT CONSTELLATION · TOP EIGHT])
#v(4mm)
#let pmax = data.products.at(0).net
#grid(columns: (1fr, 1fr), column-gutter: 10mm,
  [#for p in data.products.slice(0,4) [#bar-row(p.name, p.net, pmax, color: forest)]],
  [#for p in data.products.slice(4,8) [#bar-row(p.name, p.net, pmax, color: coral)]],
)
#v(4mm)
#block(fill: cream, inset: 5mm, radius: 1.5mm)[
  #grid(columns: (auto, 1fr), column-gutter: 5mm,
    [#text(font: "Iowan Old Style", size: 26pt, fill: coral)[#pct(data.summary.top3_product_share)]],
    [#text(size: 8pt, fill: muted)[The top three products—Standing Desk, Ergonomic Chair and Monitor 27in—generated more than half of all net sales. This creates focus, but also concentration risk.]]
  )
]

// PAGE 4
#pagebreak()
#section-head("03", [The market], [Balanced routes to market; less balanced geography.])
#grid(columns: (1.08fr, .92fr), column-gutter: 11mm,
  [
    #eyebrow([REGIONAL TIDE])
    #v(5mm)
    #let rmax = data.regions.at(0).net
    #for (i, r) in data.regions.enumerate() [
      #bar-row(r.name, r.net, rmax, color: (coral, forest, moss, sky, saffron).at(i), suffix: pct(r.share))
    ]
    #v(5mm)
    #text(font: "Iowan Old Style", size: 15pt)[South leads East by #money(data.regions.at(0).net - data.regions.at(4).net).]
    #v(2mm)
    #text(size: 7.7pt, fill: muted)[Yet order counts are broadly similar. The gap is primarily ticket size: South averaged #money(data.regions.at(0).net / data.regions.at(0).orders) per order versus #money(data.regions.at(4).net / data.regions.at(4).orders) in East.]
  ],
  [
    #eyebrow([ROUTES TO CUSTOMER])
    #v(5mm)
    #for (i, c) in data.channels.enumerate() [
      #block(fill: if i == 0 {ink} else {cream}, inset: 5mm, radius: 1.5mm)[
        #grid(columns: (1fr, auto),
          [#text(size: 8pt, weight: 700, fill: if i == 0 {paper} else {ink})[#c.name]],
          [#text(font: "Iowan Old Style", size: 17pt, fill: if i == 0 {coral} else {forest})[#pct(c.share)]]
        )
        #text(size: 6.8pt, fill: if i == 0 {paper.transparentize(35%)} else {muted})[#c.orders orders · #money(c.net)]
      ]
      #v(3mm)
    ]
  ]
)
#v(10mm)
#eyebrow([SEGMENT EQUILIBRIUM])
#v(4mm)
#grid(columns: ((1fr,) * 3), column-gutter: 4mm,
  ..data.segments.map((s) => block(fill: cream, inset: 5mm, radius: 1.5mm)[
    #text(font: "Iowan Old Style", size: 19pt)[#s.name]
    #v(3mm)
    #text(font: "Iowan Old Style", size: 25pt, fill: forest)[#pct(s.share)]
    #v(2mm)
    #text(size: 7pt, fill: muted)[#s.orders orders · #money(s.net)]
  ])
)
#v(8mm)
#block(stroke: .6pt + hair, inset: 5mm, radius: 1.5mm)[
  #grid(columns: (auto, 1fr), column-gutter: 6mm,
    [#text(font: "Iowan Old Style", size: 25pt, fill: coral)[01]],
    [#text(size: 8pt)[The most useful expansion question is not “which channel?”—channel shares differ by only four points. It is “how do we raise East’s order value without disturbing the healthy channel mix?”]]
  )
]

// PAGE 5
#pagebreak()
#section-head("04", [The craft], [Performance lives in the space between reach, pricing discipline and human execution.])
#grid(columns: (1fr, 1fr), column-gutter: 11mm,
  [
    #eyebrow([SALES TEAM])
    #v(5mm)
    #let repmax = data.reps.at(0).net
    #for (i, r) in data.reps.enumerate() [
      #bar-row(r.name, r.net, repmax, color: if i == 0 {coral} else {forest})
    ]
    #v(3mm)
    #text(size: 7.5pt, fill: muted)[F. Oyelaran led with #money(data.reps.at(0).net), #pct(data.reps.at(0).share) of annual net sales. The remaining five representatives sit within a comparatively narrow #money(data.reps.at(1).net - data.reps.at(5).net) band.]
  ],
  [
    #eyebrow([THE DISCOUNT SLOPE])
    #v(5mm)
    #let dmax = calc.max(..data.discount_bands.map(d => d.avg_order))
    #for (i, d) in data.discount_bands.enumerate() [
      #bar-row(d.name, d.avg_order, dmax, color: (forest, moss, saffron, coral, rgb("#A9473D")).at(i), suffix: money(d.avg_order))
    ]
    #v(3mm)
    #text(size: 7.5pt, fill: muted)[Orders discounted 16–20% averaged #money(data.discount_bands.at(4).avg_order), #pct(1 - data.discount_bands.at(4).avg_order / data.discount_bands.at(0).avg_order) below undiscounted orders. This is descriptive, not causal—but it argues for testing discount thresholds carefully.]
  ]
)
#v(9mm)
#eyebrow([THREE MOVES FOR THE NEXT CYCLE])
#v(4mm)
#grid(columns: ((1fr,) * 3), column-gutter: 4mm,
  [#block(fill: ink, inset: 5mm, radius: 1.5mm)[#text(font: "Iowan Old Style", size: 21pt, fill: coral)[01] #v(3mm) #text(size: 8pt, weight: 700, fill: paper)[Protect the engine] #v(2mm) #text(size: 7pt, fill: paper.transparentize(32%))[Monitor availability, pricing and attach-rate around the three products that create #pct(data.summary.top3_product_share) of revenue.]]],
  [#block(fill: cream, inset: 5mm, radius: 1.5mm)[#text(font: "Iowan Old Style", size: 21pt, fill: coral)[02] #v(3mm) #text(size: 8pt, weight: 700)[Lift the East] #v(2mm) #text(size: 7pt, fill: muted)[Use South’s basket mix as a benchmark and test bundles that raise East’s order value rather than simply chasing more orders.]]],
  [#block(fill: cream, inset: 5mm, radius: 1.5mm)[#text(font: "Iowan Old Style", size: 21pt, fill: coral)[03] #v(3mm) #text(size: 8pt, weight: 700)[Learn from October] #v(2mm) #text(size: 7pt, fill: muted)[Decompose the peak month by product, region and rep; turn the repeatable ingredients into a focused autumn playbook.]]],
)
#v(9mm)
#grid(columns: (1fr, .85fr), column-gutter: 12mm,
  [
    #eyebrow([A SINGLE ORDER, IN RELIEF])
    #v(3mm)
    #text(font: "Iowan Old Style", size: 17pt)[The year’s largest order was #money(data.summary.largest_order.net): #data.summary.largest_order.product for #data.summary.largest_order.customer in #data.summary.largest_order.region on #data.summary.largest_order.date.]
  ],
  [
    #eyebrow([METHOD])
    #v(3mm)
    #text(size: 6.8pt, fill: muted)[Computed directly from sample_sales_data.csv. Net sales are summed from net_amount; discounts equal gross_amount minus net_amount. Customer count uses distinct customer strings. Percentages may not sum to 100.0% because of rounding. This report describes revenue, not profit or causality.]
  ]
)
