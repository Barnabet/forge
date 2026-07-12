---
name: pdf
description: Use whenever a task involves a PDF file (.pdf) — reading or extracting text/tables, merging/combining, splitting, extracting or rotating pages, watermarking or stamping, reading or setting metadata, extracting embedded images, encrypting/decrypting/password-protecting, OCR on scanned PDFs, generating a PDF from data/Markdown/HTML (report, letter, invoice, label), or filling out a PDF form. Fires on phrases like "merge these PDFs", "this scanned PDF", "fill out this form", "make a PDF invoice", "extract the tables", "why are my form fields blank".
---

# PDF Processing

## Start here: pick the branch

| Goal | Go to |
|---|---|
| Read text / tables from a PDF | **READ** below |
| PDF is scanned (no selectable text) | **SCANNED** below |
| Create a new PDF from data / Markdown / HTML | `creating.md` |
| Fill a PDF form | `forms.md` |
| Merge, split, rotate, watermark, metadata, images, encrypt | **EDIT** below |
| Verify any PDF you produced looks right | **Render & verify** below |

Python has `pypdf`, `pdfplumber`, `pypdfium2`, `reportlab`, `markdown`, and
`Pillow` preinstalled. External binaries (`typst`, `ocrmypdf`, `pandoc`,
`weasyprint`, `qpdf`, `docling`) are **not** — install on demand with
`command -v X || brew install X`.

**Never import PyMuPDF/`fitz` (AGPL) or `marker` (GPL).** Use the libs above.

## READ

Use the first-class **`read_pdf` tool** — don't shell out for plain reading:
- `read_pdf(path)` → text per page, and it flags scanned/no-text-layer PDFs.
- `mode=layout` preserves columns and table alignment — use it for tables,
  multi-column pages, or anything where reading order matters.
- `pages="1-5,8"` to scope large documents.

If `read_pdf` flags no text layer → **SCANNED**.

If layout mode still mangles a complex page (nested tables, forms as tables),
fall back to **docling** (MIT license, heavy ~1GB — install only when needed):
`uv tool install docling` (or `pip install docling`), then `docling in.pdf`
emits clean Markdown. Reserve it for pages the tool genuinely can't handle.

## SCANNED

`read_pdf` says there's no text layer → add one with OCR, then read normally:
```bash
command -v ocrmypdf || brew install ocrmypdf
ocrmypdf --skip-text -l eng in.pdf out.pdf   # --skip-text keeps existing text pages
```
Then `read_pdf(path="out.pdf")`. Use `-l eng+fra` etc. for other/multiple
languages.

## EDIT (pypdf recipes)

**Page indexes are 0-based in pypdf** (page 1 = `reader.pages[0]`), but
1-based in the `read_pdf` tool and in `forms.md`/`pdf_to_images.py`. Convert.

Merge/append:
```python
from pypdf import PdfWriter
w = PdfWriter()
for f in ["a.pdf", "b.pdf"]:
    w.append(f)            # append preserves outlines/links; add_page loses them
w.write("merged.pdf")
```

Split / extract pages:
```python
from pypdf import PdfReader, PdfWriter
r = PdfReader("in.pdf")
w = PdfWriter()
for i in [0, 2, 4]:        # 0-based; pages 1,3,5
    w.add_page(r.pages[i])
w.write("subset.pdf")
```

Rotate:
```python
r = PdfReader("in.pdf"); w = PdfWriter()
for p in r.pages:
    p.rotate(90)           # clockwise; multiple of 90
    w.add_page(p)
w.write("rotated.pdf")
```

Read metadata + page count:
```python
r = PdfReader("in.pdf")
print(len(r.pages), r.metadata.title, r.metadata.author)
```

Set metadata:
```python
r = PdfReader("in.pdf"); w = PdfWriter(); w.append(r)
w.add_metadata({"/Title": "Q3 Report", "/Author": "Finance"})
w.write("out.pdf")
```

Watermark / stamp (overlay one page onto every page). Make the overlay PDF with
Typst (see `creating.md`) — e.g. rotated grey "DRAFT" text on a transparent page:
```python
stamp = PdfReader("stamp.pdf").pages[0]
r = PdfReader("in.pdf"); w = PdfWriter()
for p in r.pages:
    p.merge_page(stamp)    # stamp on top; use merge_page(stamp, over=False) for background
    w.add_page(p)
w.write("stamped.pdf")
```

Extract embedded images:
```python
r = PdfReader("in.pdf")
for pi, page in enumerate(r.pages):
    for img in page.images:
        open(f"p{pi+1}_{img.name}", "wb").write(img.data)
```

Encrypt / decrypt:
```python
# encrypt
r = PdfReader("in.pdf"); w = PdfWriter(); w.append(r)
w.encrypt("userpw", "ownerpw", algorithm="AES-256")
w.write("locked.pdf")
# decrypt
r = PdfReader("locked.pdf"); r.decrypt("userpw")
w = PdfWriter(); w.append(r); w.write("open.pdf")
```

For very large files or a corrupt/damaged PDF, use **qpdf** instead —
it streams and repairs:
```bash
command -v qpdf || brew install qpdf
qpdf --empty --pages a.pdf b.pdf -- merged.pdf      # merge
qpdf in.pdf --pages . 1-5 -- subset.pdf             # extract
qpdf --decrypt --password=PW in.pdf out.pdf         # remove password
qpdf --replace-input --qdf --object-streams=disable broken.pdf   # repair
```

## Render & verify (acceptance test)

Any PDF you generate or edit must pass a render-and-verify loop before you call
it done — text extraction alone won't catch overlap, clipping, or bad layout:

1. Call the **`view` tool** (`view(path, pages="1-3", dpi=120)`) — it renders
   the pages and returns them **as images you actually see**. Up to 8 pages per
   call; use `pages` to scope longer documents. (`view` also opens image files
   like PNG/JPG directly.)
2. **Look at the rendered pages** and check the layout with your own eyes.
3. Fix the source, regenerate, repeat until correct.

Scriptable fallback (e.g. batch export, or outside a session):
`python3 <skill>/scripts/pdf_to_images.py out.pdf /tmp/pdfcheck` writes PNGs to
disk — then `view` any of them directly.

## Scripts

- `scripts/pdf_to_images.py <in.pdf> <outdir> [--dpi 200] [--pages 1-5,8]` — render to PNG (pypdfium2).
- `scripts/detect_text_layer.py <in.pdf>` — prints per-page char counts + `TEXT_LAYER: yes|no|partial`. Use if `read_pdf` isn't handy or to confirm before OCR.
- `scripts/extract_form_fields.py` / `scripts/fill_form_fields.py` — see `forms.md`.

## Gotchas

- **Filled form values render blank** unless `NeedAppearances` is set. `fill_form_fields.py` handles it (`set_need_appearances_writer(True)`); details in `forms.md`.
- **PyMuPDF/`fitz` is AGPL** and `marker` is GPL — do not import either in code you write here.
- **0-based vs 1-based**: pypdf pages are 0-based; `read_pdf`, `pdf_to_images.py`, and form schemas use 1-based page numbers.
