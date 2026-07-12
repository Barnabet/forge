// Transparent proof stamp — rotated fluoro outline, corner tag.
#set page(width: 210mm, height: 297mm, margin: 0pt, fill: none)
#place(center + horizon,
  rotate(-28deg,
    text(font: "Helvetica Neue", size: 108pt, weight: "black",
         tracking: 4pt, stroke: 2pt + rgb("#FF3D8B").transparentize(35%),
         fill: rgb("#FF3D8B").transparentize(92%))[PROOF]
  )
)
#place(top + right, dx: -12mm, dy: 14mm,
  box(stroke: 1pt + rgb("#FF3D8B"), inset: (x: 6pt, y: 3pt),
    text(font: "DejaVu Sans Mono", size: 8pt, fill: rgb("#FF3D8B"))[NOT FOR DISTRIBUTION])
)
