# Creating spreadsheets (data → xlsx)

Pick the engine by what you need, write the workbook, then **recalculate and
verify** (see SKILL.md). A workbook with formulas is not done until its cached
values exist and `recalc.py` reports zero errors.

| Need | Engine |
|---|---|
| Dump a DataFrame / simple table, no styling | **pandas** `to_excel` |
| Styles, formulas, charts, edit an existing file | **openpyxl** (primary) |
| Write-only, richest charts / conditional formats, huge streamed output | **xlsxwriter** |

`openpyxl`, `pandas`, and `xlsxwriter` are all preinstalled — no `pip install`.

## openpyxl (primary)

An `.xlsx` is a workbook of sheets; cells are 1-based (`ws.cell(row=1,
column=1)` is `A1`). Build with `Workbook`, or edit with `load_workbook` — the
styling API is identical either way.

```python
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter

wb = Workbook()
ws = wb.active
ws.title = "Q3"

# Header row + data
ws.append(["Region", "Jul", "Aug", "Sep", "Total"])
rows = [("EMEA", 120, 135, 150), ("APAC", 90, 95, 110), ("AMER", 210, 205, 230)]
for name, *months in rows:
    ws.append([name, *months])

# Formulas — write the STRING; recalc.py fills the value later.
for r in range(2, ws.max_row + 1):
    ws[f"E{r}"] = f"=SUM(B{r}:D{r})"
ws["A5"] = "Total"
for col in "BCDE":
    ws[f"{col}5"] = f"=SUM({col}2:{col}4)"

# Styling: build whole style objects and assign (they're immutable).
hdr = Font(bold=True, color="FFFFFF")
fill = PatternFill("solid", fgColor="305496")
for cell in ws[1]:
    cell.font = hdr
    cell.fill = fill
    cell.alignment = Alignment(horizontal="center")

# Number format, widths, freeze panes
for r in range(2, ws.max_row + 1):
    for c in "BCDE":
        ws[f"{c}{r}"].number_format = "#,##0"
ws.column_dimensions["A"].width = 14
ws.freeze_panes = "A2"          # freeze the header row

wb.save("out.xlsx")
```

Common number formats: `"#,##0"`, `"#,##0.00"`, `'"$"#,##0.00'`, `"0.0%"`,
`"yyyy-mm-dd"`, `"0.0x"` (multiples), `"$#,##0;($#,##0);-"` (negatives in
parens, zero as dash).

### Merged cells & borders

```python
ws.merge_cells("A1:E1")         # value goes in A1; the rest become read-only
ws["A1"] = "Q3 Regional Sales"
thin = Side(style="thin", color="CCCCCC")
ws["A1"].border = Border(bottom=thin, top=thin, left=thin, right=thin)
```

### Conditional formatting

```python
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule
ws.conditional_formatting.add(
    "E2:E4",
    CellIsRule(operator="lessThan", formula=["300"],
               fill=PatternFill("solid", fgColor="FFC7CE")))
ws.conditional_formatting.add(
    "B2:D4",
    ColorScaleRule(start_type="min", start_color="F8696B",
                   end_type="max", end_color="63BE7B"))
```

### Charts

`Reference` picks the data range; include the header row and pass
`titles_from_data=True` so the series is named. Categories are the x-axis labels.

```python
from openpyxl.chart import BarChart, Reference
chart = BarChart()
chart.title = "Monthly sales by region"
data = Reference(ws, min_col=2, max_col=4, min_row=1, max_row=4)  # B1:D4 w/ header
cats = Reference(ws, min_col=1, min_row=2, max_row=4)             # A2:A4
chart.add_data(data, titles_from_data=True)
chart.set_categories(cats)
ws.add_chart(chart, "G2")
wb.save("out.xlsx")
```

## pandas (plain data export)

```python
import pandas as pd
df.to_excel("out.xlsx", index=False, sheet_name="Data")

# Multiple sheets in one file:
with pd.ExcelWriter("out.xlsx", engine="openpyxl") as xw:
    for name, frame in {"Sales": s, "Costs": c}.items():
        frame.to_excel(xw, sheet_name=name, index=False)
```

pandas won't add formulas or styling itself. When you need both bulk data *and*
formatting, write the DataFrame first, then reopen with openpyxl to style — or
use the xlsxwriter ExcelWriter (below).

## xlsxwriter (write-only, rich formatting)

Cannot read or edit existing files — use only for fresh output. Best charts,
data validation, sparklines, in-cell images, and `{"constant_memory": True}` for
streaming millions of rows top-to-bottom.

```python
import xlsxwriter
wb = xlsxwriter.Workbook("out.xlsx")
ws = wb.add_worksheet("Q3")
money = wb.add_format({"num_format": "#,##0", "bold": True})
ws.write_row(0, 0, ["Region", "Jul", "Aug", "Sep"])
ws.write_formula(1, 4, "=SUM(B2:D2)")   # optionally: value=405 to pre-cache
ws.set_column("A:A", 14)
ws.freeze_panes(1, 0)
wb.close()
```

xlsxwriter also doesn't compute formulas; either pass a pre-computed `value=` to
`write_formula`, or run `recalc.py` afterward.

## Convert

**xlsx → csv** (one file per sheet; recalc first so formula cells aren't blank):

```python
import csv
from openpyxl import load_workbook
wb = load_workbook("in.xlsx", read_only=True, data_only=True)
for ws in wb.worksheets:
    with open(f"{ws.title}.csv", "w", newline="") as f:
        w = csv.writer(f)
        for row in ws.iter_rows(values_only=True):
            w.writerow(row)
wb.close()
```

**xlsx → pdf** — LibreOffice gives the best fidelity. Set page layout first so
wide sheets don't clip:

```python
from openpyxl import load_workbook
wb = load_workbook("in.xlsx")
for ws in wb.worksheets:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
wb.save("in.xlsx")
```
```bash
soffice --headless --convert-to pdf --outdir /tmp in.xlsx
```
Then view the result with the `view` tool. Run only one `soffice` at a time
(or give each a private profile via `-env:UserInstallation=file:///tmp/lo_$$`).

## Recalculate & verify (required last step)

After writing any formula, the cached values don't exist yet. Run
`python3 <skill>/scripts/recalc.py out.xlsx` and confirm `total_errors: 0`, then
`python3 <skill>/scripts/xlsx_inspect.py out.xlsx` to eyeball the data and
computed values. For visual output, export to PDF and use `view`. Details in
SKILL.md.
