---
name: xlsx
description: Use whenever a task involves a spreadsheet file (.xlsx, .xlsm, .xls, .csv, .tsv) as the primary input or output — reading or extracting data/tables, cleaning messy tabular data, adding or computing columns, writing formulas, styling/number-formatting, conditional formatting, merged cells, freeze panes, charts, pivot-style summaries, multi-sheet workbooks, building a model or report, or converting between tabular formats (xlsx↔csv, xlsx→pdf). Fires on phrases like "the xlsx in my downloads", "add a total column", "why are my formula cells blank", "make a spreadsheet", "clean up this CSV", "recalculate the formulas", "convert this sheet to PDF", "read the second tab". Do NOT fire when the deliverable is a Word/PDF document, a chart image, or a standalone script that merely happens to touch tabular data.
---

# XLSX / Spreadsheet Processing

## Start here: pick the branch

| Goal | Go to |
|---|---|
| Read / extract / analyze data from a spreadsheet | **READ** below |
| Create a new workbook from data (report, model, export) | `creating.md` |
| Edit an existing workbook (add columns, tweak cells, restyle) | **EDIT** below |
| A workbook you wrote shows blank/`None` for formula cells | **RECALCULATE** below |
| Convert xlsx → csv / pdf | `creating.md` (Convert section) |
| Confirm any workbook you produced is correct | **Verify** below |

`openpyxl`, `pandas`, `xlsxwriter`, `python-calamine`, and `xlrd` are
**preinstalled** — import and use them directly, no `pip install` needed.
`soffice` (LibreOffice, for recalculation and PDF export) is a separate binary —
`command -v soffice || brew install --cask libreoffice`.

**The one fact that trips everyone:** openpyxl, pandas, and xlsxwriter all write
formula *strings* but **never compute them**. A cell holding `=SUM(A1:A9)` has no
value until Excel or LibreOffice recalculates. See **RECALCULATE**.

## READ

Two libraries, chosen by what you need:

- **pandas** — bulk tabular data, quick analysis, whole sheets as DataFrames.
- **openpyxl** — cell-level access, styles, formulas-as-text, merged cells.

```python
import pandas as pd
df = pd.read_excel("in.xlsx")                       # first sheet
sheets = pd.read_excel("in.xlsx", sheet_name=None)  # {name: df} for every tab
df.head(); df.info(); df.describe()                 # eyeball shape, dtypes, stats
```

For a fast, low-memory read of a large file, use the Rust-based calamine engine
(`python-calamine`, preinstalled): `pd.read_excel("big.xlsx", engine="calamine")`.

Reading with openpyxl — two mutually exclusive views of the same file:

```python
from openpyxl import load_workbook
wb_f = load_workbook("in.xlsx")                 # formula TEXT: '=A1*2'
wb_v = load_workbook("in.xlsx", data_only=True) # last CACHED value: 84 (or None)
ws = wb_f["Sheet1"]                              # or wb_f.active
for row in ws.iter_rows(values_only=True):      # tuples of values
    ...
```

**`data_only=True` returns the value Excel cached the last time it saved** — so
formula cells read `None` if the file was written by a library and never
recalculated (see **RECALCULATE**). You cannot get formula text and cached value
from one `load_workbook`; open twice.

Large files: `load_workbook(path, read_only=True, data_only=True)` streams and
stays lean — but you **must** `wb.close()` afterward (it holds the file open).

Legacy `.xls` (old binary): `pd.read_excel("old.xls", engine="xlrd")`. Note
**xlrd 2.0 reads `.xls` only, not `.xlsx`** — a very common breakage; use
openpyxl/calamine for `.xlsx`.

## EDIT

`load_workbook` → mutate → `save` **preserves** cell data, formulas, styles,
number formats, merged cells, column widths, and most conditional formatting.
It does **not** reliably round-trip Excel-authored **charts, pivot tables,
images, or slicers** — if the file has those and they must survive untouched,
edit a copy and spot-check the render, or drop to the raw-XML escape hatch.

```python
from openpyxl import load_workbook
wb = load_workbook("in.xlsx")            # add keep_vba=True for .xlsm macros
ws = wb["Data"]

ws["E1"] = "Total"                       # header
for r in range(2, ws.max_row + 1):       # 1-based rows; row 2 = first data row
    ws[f"E{r}"] = f"=SUM(B{r}:D{r})"     # write the FORMULA, not a Python sum
ws.insert_rows(2); ws.delete_cols(3)     # structural edits
new = wb.create_sheet("Summary")
wb.save("out.xlsx")                       # keep .xlsm extension for macro files
```

Prefer emitting **formulas** over Python-computed constants so the sheet stays
live when a user changes an input. After writing any formula, go to
**RECALCULATE** so the cached values exist.

Styling, number formats, conditional formatting, merged cells, freeze panes, and
charts all live in `creating.md` — the API is identical whether you create or
edit.

### Raw-XML escape hatch

An `.xlsx` is a zip of XML parts. When openpyxl can't express something or would
drop an artifact you must preserve, `unzip in.xlsx -d parts/`, edit the XML
(`xl/worksheets/sheet1.xml`, `xl/workbook.xml`, …), then rezip **with the
mimetype/relationships intact**: `cd parts && zip -r -X ../out.xlsx .`. Reserve
this for cases openpyxl genuinely can't handle.

## RECALCULATE

You wrote formulas with a library; every formula cell's cached value is empty, so
`data_only=True` reads `None` and any downstream formula referencing it, or a
csv/pdf export, comes out blank. Fix it by making LibreOffice open, compute, and
resave the file:

```bash
command -v soffice || brew install --cask libreoffice
python3 <skill>/scripts/recalc.py out.xlsx
```

`recalc.py` drives headless LibreOffice to `calculateAll()` + save, then reloads
with openpyxl and scans every cell for Excel error strings, printing JSON:

```json
{"status": "success", "total_formulas": 42, "total_errors": 0, "error_summary": {}}
```

If `status` is `errors_found`, read `error_summary` (each `#REF!`, `#DIV/0!`,
`#VALUE!`, `#N/A`, `#NAME?`, `#NUM!`, `#NULL!` with up to 20 cell locations),
fix the formulas, and rerun. **Deliver workbooks with zero formula errors.**

Pure-Python alternatives when LibreOffice isn't available (`pip install
formulas` or `pycel`) evaluate a dependency graph but cover only a subset of
Excel functions — LibreOffice is the reliable path.

## Verify (acceptance test)

Before calling any workbook done:

1. **Recalculate** (above) and confirm `total_errors: 0`.
2. `python3 <skill>/scripts/xlsx_inspect.py out.xlsx` — prints each sheet's
   dimensions, headers, a few sample rows, and any remaining error cells, so you
   can confirm the data and computed values landed where intended.
3. For anything with visual layout (styling, charts, a report meant to print),
   render to PDF and look: `soffice --headless --convert-to pdf --outdir /tmp
   out.xlsx`, then use the `view` tool on the result.

## Scripts

- `scripts/recalc.py <file.xlsx> [timeout_s]` — recalc all formulas via headless
  LibreOffice, then scan for error cells; prints JSON. Run after writing formulas.
- `scripts/xlsx_inspect.py <file.xlsx>` — per-sheet dimensions, headers, sample
  rows, and error-cell report (uses cached values). No pandas needed.

## Gotchas

- **Formula cells read blank / `None`** — nothing computed them yet. Run
  `recalc.py`. This is the single most common surprise.
- **`data_only=True` then `save` destroys formulas** — loading data-only drops
  the formula text, so re-saving replaces every formula with its cached value.
  Never save a workbook you opened `data_only=True` if you want to keep formulas.
- **`xlrd` won't open `.xlsx`** (2.0+ is `.xls`-only) — use openpyxl/calamine.
- **Editing can drop Excel-authored charts/pivots/images/VBA** — round-trip a
  copy and verify; pass `keep_vba=True` for `.xlsm`.
- **`read_only=True` needs `wb.close()`**; `write_only=True` allows only
  `ws.append(row)`, no random-access cell writes.
- **Styles are whole immutable objects** — build a new `Font(...)`/`PatternFill`
  and assign it; you can't mutate `cell.font.bold = True` in place.
- **`MergedCell`s are read-only** except the top-left anchor; write the value
  there before (or without) merging.
