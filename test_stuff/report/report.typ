#let d = json("data.json")
#let m = d.meta

// ---- palette ----
#let pine   = rgb("#20342A")
#let pine2  = rgb("#2C4437")
#let paper  = rgb("#F2ECDF")
#let brass  = rgb("#C0862E")
#let clay   = rgb("#B0563A")
#let sage   = rgb("#8A9A86")
#let ink    = rgb("#20271F")
#let faint  = rgb("#5E6B5A")

#let display = "Bodoni 72"
#let sans    = "Avenir Next"
#let mono    = "PT Mono"

// ---- helpers ----
#let money(x) = {
  let n = calc.round(x)
  let s = str(n)
  let out = ()
  let c = 0
  for ch in s.clusters().rev() {
    if c > 0 and calc.rem(c,3) == 0 { out.push(",") }
    out.push(ch); c += 1
  }
  "$" + out.rev().join()
}
#let kfmt(x) = {
  if x >= 1000 { str(calc.round(x/1000, digits:1)) + "k" } else { str(calc.round(x)) }
}
#let eyebrow(t, col: brass) = text(font: sans, size: 8pt, weight: 600, tracking: 3pt, fill: col)[#upper(t)]
#let lerp(a, b, t) = a.mix((b, t*100%))

#set page(width: 210mm, height: 297mm, margin: 0pt, fill: paper)
#set text(font: sans, fill: ink)

// =========================================================
// PAGE 1 — COVER
// =========================================================
#page(fill: pine)[
  #set text(fill: paper)
  // faint drafting grid
  #place(top+left, dx:0pt, dy:0pt, box(width:210mm, height:297mm)[
    #for i in range(1, 21) { place(left, dx: i*10mm, line(start:(0pt,0pt), end:(0pt,297mm), stroke: rgb(255,255,255,8))) }
    #for j in range(1, 30) { place(top, dy: j*10mm, line(start:(0pt,0pt), end:(210mm,0pt), stroke: rgb(255,255,255,8))) }
  ])

  #place(top+left, dx:22mm, dy:22mm)[
    #eyebrow("Annual Sales Almanac", col: brass)
    #v(3pt)
    #text(font: mono, size: 9pt, fill: sage, tracking: 1pt)[FY2024 · 01 JAN — 31 DEC]
  ]
  #place(top+right, dx:-22mm, dy:22mm)[
    #align(right)[#text(font: mono, size: 9pt, fill: sage)[N° 2024 / EDITION I]]
  ]

  #place(left+top, dx:22mm, dy:78mm)[
    #text(font: display, size: 68pt, fill: paper, weight: 400)[Anatomy]
    #v(-30pt)
    #text(font: display, size: 68pt, style: "italic", fill: brass, weight: 400)[of a Year]
  ]

  #place(left+top, dx:23mm, dy:150mm)[
    #text(font: sans, size: 11pt, fill: sage, weight: 300)[A data portrait of twelve months of trade —]
    #v(-4pt)
    #text(font: sans, size: 11pt, fill: sage, weight: 300)[every order, day, and object accounted for.]
  ]

  // the headline figure
  #place(left+bottom, dx:22mm, dy:-58mm)[
    #eyebrow("Net revenue recognised", col: sage)
    #v(2pt)
    #text(font: display, size: 84pt, fill: paper, weight: 400)[#money(m.total_net)]
  ]

  // bottom stat rail
  #place(left+bottom, dx:22mm, dy:-24mm, box(width: 166mm)[
    #line(start:(0pt,0pt), end:(166mm,0pt), stroke: 0.6pt + rgb(255,255,255,40))
    #v(7pt)
    #grid(columns: (1fr,1fr,1fr,1fr), 
      ..(("Orders", str(m.orders)),
         ("Units shipped", str(m.units)),
         ("Customers", str(m.customers)),
         ("Avg. order", money(m.aov))
      ).map(p => [
        #text(font: mono, size: 15pt, fill: brass)[#p.at(1)] \
        #text(font: sans, size: 7.5pt, tracking: 2pt, fill: sage)[#upper(p.at(0))]
      ])
    )
  ])
]

// =========================================================
// PAGE 2 — OVERVIEW: KPIs + monthly revenue arc
// =========================================================
#page[
  #place(top+left, dx:22mm, dy:20mm)[
    #grid(columns:(auto,1fr), column-gutter: 8pt, align:horizon,
      text(font: mono, size:9pt, fill: brass)[01],
      line(length: 150mm, stroke: 0.5pt+sage))
    #v(6pt)
    #text(font: display, size: 30pt, weight:400)[The Year at a Glance]
    #v(-2pt)
    #text(font: sans, size: 9.5pt, fill: faint, weight: 300)[Headline performance across #m.orders orders, closed between January and December 2024.]
  ]

  // KPI cards
  #let kpi(label, val, sub) = box(width:100%, inset:(y:11pt, x:12pt), stroke:(left: 2pt+brass), fill: white.transparentize(8%))[
    #text(font:sans, size:7pt, tracking:2pt, fill:faint)[#upper(label)] \
    #v(3pt)
    #text(font: display, size: 25pt, fill: ink)[#val] \
    #text(font: mono, size: 8pt, fill: clay)[#sub]
  ]
  #place(top+left, dx:22mm, dy:60mm, box(width:166mm)[
    #grid(columns:(1fr,1fr,1fr), column-gutter: 7pt, row-gutter: 7pt,
      kpi("Net revenue", money(m.total_net), "after " + money(m.discount_total) + " discounts"),
      kpi("Gross revenue", money(m.total_gross), str(m.discount_share) + "% of orders discounted"),
      kpi("Avg. order value", money(m.aov), str(m.units) + " units total"),
      kpi("Peak month", "October", money(m.best_month.value) + " · " + str(m.best_month.orders) + " orders"),
      kpi("Active customers", str(m.customers), "across four regions"),
      kpi("Catalogue", str(m.products_count) + " SKUs", "five product families"),
    )
  ])

  // Monthly revenue — area chart with baseline
  #let mo = d.months
  #let mx = calc.max(..mo.map(x=>x.value))
  #let names = ("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec")
  #place(top+left, dx:22mm, dy:132mm, box(width:166mm)[
    #eyebrow("Monthly net revenue", col: clay)
    #v(10pt)
    #box(width:166mm, height:100mm)[
      #let W = 166mm
      #let H = 78mm
      #let plotH = 90mm
      #let n = mo.len()
      #let xstep = W/(n)
      #let xc(i) = xstep*(i+0.5)
      #let yc(v) = plotH*(1 - v/mx)
      // gridlines
      #for g in (0.25,0.5,0.75,1.0) {
        place(top+left, dy: plotH*(1-g), line(start:(0pt,0pt), end:(W,0pt), stroke: 0.4pt+sage.transparentize(45%)))
        place(top+left, dx:-1mm, dy: plotH*(1-g) - 4pt, align(right, box(width:0pt)[]))
      }
      // area fill
      #place(top+left, curve(
        curve.move((xc(0), yc(mo.at(0).value))),
        ..mo.enumerate().slice(1).map(((i,p)) => curve.line((xc(i), yc(p.value)))),
        curve.line((xc(n - 1) , plotH)),
        curve.line((xc(0), plotH)),
        curve.close(),
        fill: brass.transparentize(78%), stroke: none))
      // line
      #place(top+left, curve(
        curve.move((xc(0), yc(mo.at(0).value))),
        ..mo.enumerate().slice(1).map(((i,p)) => curve.line((xc(i), yc(p.value)))),
        stroke: 2pt+brass))
      // points + peak marker
      #for (i,p) in mo.enumerate() {
        let peak = p.value == mx
        place(top+left, dx: xc(i)-2pt, dy: yc(p.value)-2pt, circle(radius: if peak {3.5pt} else {2pt}, fill: if peak {clay} else {pine}, stroke: 1pt+paper))
        if peak {
          place(top+left, dx: xc(i)-14mm, dy: yc(p.value)-11mm, box(width:28mm)[#align(center, text(font:mono, size:8pt, fill:clay)[#money(p.value)])])
        }
      }
      // month labels
      #for (i,nm) in names.enumerate() {
        place(top+left, dx: xc(i)-8mm, dy: plotH+3pt, box(width:16mm)[#align(center, text(font:mono, size:7.5pt, fill: if mo.at(i).value==mx {clay} else {faint})[#nm])])
      }
    ]
  ])

  #place(bottom+left, dx:22mm, dy:-12mm, text(font:mono, size:7pt, fill:faint)[ANATOMY OF A YEAR — FY2024])
  #place(bottom+right, dx:-22mm, dy:-12mm, text(font:mono, size:7pt, fill:faint)[02])
]

// =========================================================
// PAGE 3 — SIGNATURE: 366-day calendar pulse
// =========================================================
#page(fill: pine)[
  #set text(fill: paper)
  #place(top+left, dx:22mm, dy:20mm)[
    #grid(columns:(auto,1fr), column-gutter: 8pt, align:horizon,
      text(font: mono, size:9pt, fill: brass)[02],
      line(length: 150mm, stroke: 0.5pt+sage.transparentize(30%)))
    #v(6pt)
    #text(font: display, size: 30pt, weight:400, fill: paper)[The Pulse of the Year]
    #v(-2pt)
    #text(font: sans, size: 9.5pt, fill: sage, weight: 300)[Each cell is a single day of 2024; its warmth tracks net revenue booked that day.]
  ]

  // heatmap: 53 weeks x 7 days
  #let days = d.days
  #let mx = m.maxday
  #let cell = 2.7mm
  #let gap = 0.7mm
  #let pitch = cell + gap
  #let ox = 26mm
  #let oy = 68mm
  #let dowlab = ("M","T","W","T","F","S","S")
  #place(top+left, box[
    // day-of-week labels
    #for (i,l) in dowlab.enumerate() {
      place(top+left, dx: ox - 5mm, dy: oy + i*pitch - 0.5mm, text(font:mono, size:6pt, fill: sage)[#l])
    }
    // cells
    #for day in days {
      let x = ox + day.week*pitch
      let y = oy + day.dow*pitch
      let t = if mx > 0 { calc.min(day.v/ (mx*0.72), 1.0) } else { 0 }
      let col = if day.v == 0 { pine2 } else { lerp(brass.darken(30%), clay, t) }
      // brighten high days toward light brass
      let col = if t > 0.6 { lerp(brass, rgb("#E8C87A"), (t - 0.6)/0.4) } else { col }
      place(top+left, dx:x, dy:y, rect(width:cell, height:cell, radius:0.4mm, fill: col, stroke: none))
    }
    // month labels along top
    #let monthcols = (0,)
    #for mi in range(1,13) {
      // first week index where day.m == mi
      let fd = days.filter(dd => dd.m == mi).first()
      let wk = fd.week
      place(top+left, dx: ox + wk*pitch, dy: oy - 6mm, text(font:mono, size:7pt, fill: sage)[#("Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec").at(mi - 1)])
    }
  ])

  // legend
  #place(top+left, dx:26mm, dy:100mm, box[
    #text(font:sans, size:7pt, tracking:2pt, fill:sage)[QUIET]
    #h(4pt)
    #box(baseline:2pt)[#for i in range(0,7){ let t=i/6; let c= if t>0.6 {lerp(brass, rgb("#E8C87A"), (t - 0.6)/0.4)} else {lerp(brass.darken(30%), clay, t)}; box(width:5mm, height:2.7mm, radius:0.4mm, fill: if i==0 {pine2} else {c}); h(0.7mm)}]
    #h(4pt)
    #text(font:sans, size:7pt, tracking:2pt, fill:sage)[BUSY]
  ])

  // narrative stats on lower half
  #place(left+top, dx:22mm, dy:126mm, box(width:166mm)[
    #line(start:(0pt,0pt), end:(166mm,0pt), stroke: 0.5pt+sage.transparentize(40%))
  ])

  #let feat(k, val, sub) = box(width:100%)[
    #text(font:sans, size:7pt, tracking:2pt, fill:sage)[#upper(k)] \
    #v(4pt)
    #text(font: display, size: 30pt, fill: paper)[#val] \
    #text(font: mono, size:8pt, fill: brass)[#sub]
  ]
  #place(left+top, dx:22mm, dy:138mm, box(width:166mm)[
    #grid(columns:(1fr,1fr,1fr), column-gutter: 10pt,
      feat("Busiest single day", money(m.maxday), "peak of 366 days"),
      feat("Peak trading month", "October", money(m.best_month.value)),
      feat("Daily average", money(m.total_net/366), "steady cadence"),
    )
  ])

  // a short essay column
  #place(left+top, dx:22mm, dy:196mm, box(width:150mm)[
    #eyebrow("Reading the grid", col: brass)
    #v(6pt)
    #text(font: sans, size: 11pt, fill: sage, weight: 300)[Revenue arrives in bursts rather than a smooth tide. Warm columns cluster around large-ticket furniture orders — a single standing-desk or ergonomic-chair sale can outweigh a week of accessories. The darkest cells mark quiet days with no orders booked, a reminder that even a strong year breathes in and out.]
  ])

  #place(bottom+right, dx:-22mm, dy:-12mm, text(font:mono, size:7pt, fill:sage)[03])
]

// =========================================================
// PAGE 4 — COMPOSITION
// =========================================================
#page[
  #place(top+left, dx:22mm, dy:20mm)[
    #grid(columns:(auto,1fr), column-gutter: 8pt, align:horizon,
      text(font: mono, size:9pt, fill: brass)[03],
      line(length: 150mm, stroke: 0.5pt+sage))
    #v(6pt)
    #text(font: display, size: 30pt, weight:400)[What Sold, and to Whom]
    #v(-2pt)
    #text(font: sans, size: 9.5pt, fill: faint, weight: 300)[Revenue decomposed by product family, buyer segment, channel, and region.]
  ]

  // Category donut
  #let cat = d.category
  #let ctot = m.total_net
  #let palette = (pine, brass, clay, sage, rgb("#8C6B3F"))
  #place(top+left, dx:22mm, dy:52mm, box(width:78mm)[
    #eyebrow("By product family", col: clay)
    #v(6pt)
    #box(width:52mm, height:52mm)[
      #let stops = ()
      #let acc = 0.0
      #for (i,c) in cat.enumerate() {
        let frac = c.value/ctot
        stops.push((palette.at(calc.rem(i,palette.len())), acc*100%))
        acc = acc + frac
        let last = if i == cat.len() - 1 { 100% } else { acc*100% }
        stops.push((palette.at(calc.rem(i,palette.len())), last))
      }
      #place(center+horizon, circle(radius: 26mm, fill: gradient.conic(..stops, angle: -90deg)))
      #place(center+horizon, circle(radius: 15.5mm, fill: paper))
      #place(center+horizon, box(width:30mm)[#align(center)[
        #text(font: display, size: 17pt, fill: ink)[#kfmt(cat.first().value)] \
        #v(-3pt)
        #text(font:mono, size:6.5pt, fill:faint)[TOP: ELECTRONICS]
      ]])
    ]
    #v(8pt)
    #for (i,c) in cat.enumerate() [
      #grid(columns:(auto, 1fr, auto), column-gutter:6pt, align:horizon,
        box(width:8pt, height:8pt, radius:1pt, fill: palette.at(calc.rem(i,palette.len()))),
        text(font:sans, size:8.5pt, fill:ink)[#c.name],
        text(font:mono, size:8.5pt, fill:faint)[#money(c.value) · #str(calc.round(c.value/ctot*100))%]
      )
      #v(2.5pt)
    ]
  ])

  // Segment + Channel bars on the right
  #let hbar(items, accent) = {
    let mx = calc.max(..items.map(x=>x.value))
    for it in items [
      #grid(columns:(30mm, 1fr), column-gutter:6pt, align:horizon,
        text(font:sans, size:8.5pt, fill:ink)[#it.name],
        box(width:100%)[
          #box(width: (it.value/mx*100)*1%, height:12pt, radius:1pt, fill: accent)
          #h(3pt)
          #box(baseline:2.5pt, text(font:mono, size:7.5pt, fill:faint)[#money(it.value)])
        ]
      )
      #v(5pt)
    ]
  }
  #place(top+left, dx:112mm, dy:52mm, box(width:76mm)[
    #eyebrow("By buyer segment", col: clay)
    #v(7pt)
    #hbar(d.segment, brass)
    #v(6pt)
    #eyebrow("By sales channel", col: clay)
    #v(7pt)
    #hbar(d.channel, pine)
  ])

  // Region — full width bottom, as a ranked row of tiles
  #place(bottom+left, dx:22mm, dy:-24mm, box(width:166mm)[
    #eyebrow("By region", col: clay)
    #v(7pt)
    #let rg = d.region
    #let rmx = calc.max(..rg.map(x=>x.value))
    #grid(columns:(1fr,1fr,1fr,1fr,1fr), column-gutter:7pt,
      ..rg.map(r => box(width:100%, inset:(y:10pt,x:10pt), fill: lerp(paper, brass, r.value/rmx*0.55).darken(2%), stroke:(bottom: 2pt + clay))[
        #text(font:sans, size:7pt, tracking:1.5pt, fill:pine)[#upper(r.name)] \
        #v(3pt)
        #text(font: display, size: 18pt, fill:ink)[#kfmt(r.value)] \
        #text(font:mono, size:7pt, fill:faint)[#str(calc.round(r.value/m.total_net*100))% of net]
      ])
    )
  ])

  #place(bottom+left, dx:22mm, dy:-12mm, text(font:mono, size:7pt, fill:faint)[ANATOMY OF A YEAR — FY2024])
  #place(bottom+right, dx:-22mm, dy:-12mm, text(font:mono, size:7pt, fill:faint)[04])
]

// =========================================================
// PAGE 5 — RANKINGS + colophon
// =========================================================
#page[
  #place(top+left, dx:22mm, dy:20mm)[
    #grid(columns:(auto,1fr), column-gutter: 8pt, align:horizon,
      text(font: mono, size:9pt, fill: brass)[04],
      line(length: 150mm, stroke: 0.5pt+sage))
    #v(6pt)
    #text(font: display, size: 30pt, weight:400)[Leaders of the Year]
    #v(-2pt)
    #text(font: sans, size: 9.5pt, fill: faint, weight: 300)[The objects and the people that carried FY2024.]
  ]

  // Product ranking table
  #place(top+left, dx:22mm, dy:52mm, box(width:100mm)[
    #eyebrow("Top products by net revenue", col: clay)
    #v(6pt)
    #let ps = d.products.slice(0, 10)
    #let pmx = ps.first().value
    #for (i,p) in ps.enumerate() [
      #grid(columns:(6mm, 1fr, auto), column-gutter:5pt, align:horizon,
        text(font:mono, size:8pt, fill:brass)[#{if i + 1 < 10 { "0" } else { "" }}#{i + 1}],
        box(width:100%)[
          #text(font:sans, size:8.5pt, fill:ink)[#p.name]
          #v(2pt)
          #box(width: (p.value/pmx*100)*1%, height:4pt, radius:1pt, fill: lerp(brass, clay, i/10))
        ],
        box(width:20mm)[#align(right, text(font:mono, size:8pt, fill:faint)[#money(p.value)])]
      )
      #v(4pt)
    ]
  ])

  // Sales reps
  #place(top+left, dx:130mm, dy:52mm, box(width:58mm)[
    #eyebrow("Sales representatives", col: clay)
    #v(6pt)
    #let rs = d.reps
    #let rmx = rs.first().value
    #for (i,r) in rs.enumerate() [
      #text(font:sans, size:8.5pt, fill:ink)[#r.name]
      #v(1.5pt)
      #grid(columns:(1fr,auto), align:horizon,
        box(width: (r.value/rmx*100)*1%, height:10pt, radius:1pt, fill: if i==0 {brass} else {sage}),
        text(font:mono, size:7.5pt, fill:faint)[#h(4pt)#money(r.value)])
      #text(font:mono, size:6.5pt, fill:faint)[#r.orders orders]
      #v(6pt)
    ]
  ])

  // Colophon band at bottom (pine)
  #place(bottom+left, dx:0pt, dy:0pt, box(width:210mm, height:74mm, fill: pine)[
    #set text(fill: paper)
    #place(top+left, dx:22mm, dy:16mm)[
      #text(font: display, style:"italic", size: 24pt, fill: brass)[Anatomy of a Year]
      #v(2pt)
      #text(font: sans, size: 9pt, fill: sage, weight:300, )[This almanac was composed from #m.orders individual orders spanning \ 01 January to 31 December 2024 — #m.units units across #m.products_count products.]
    ]
    #place(top+right, dx:-22mm, dy:16mm, box(width:70mm)[
      #align(right)[
        #text(font:mono, size:7pt, tracking:1pt, fill:sage)[COLOPHON] \
        #v(3pt)
        #text(font:sans, size:8pt, fill:paper, weight:300)[Set in Bodoni 72, Avenir Next \ and PT Mono. Rendered as vector \ typography in Typst.] \
        #v(4pt)
        #text(font:mono, size:7pt, fill:brass)[NET #money(m.total_net) · FY2024]
      ]
    ])
    #place(bottom+left, dx:22mm, dy:-10mm, text(font:mono, size:7pt, fill:sage.transparentize(20%))[FY2024 ANNUAL SALES ALMANAC — 05 / 05])
  ])
]
