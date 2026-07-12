# Filling PDF Forms

Do these in order. Do not skip to writing code.

## Step 0 — detect fields

```bash
python3 <skill>/scripts/extract_form_fields.py in.pdf
```
Prints a JSON schema to stdout:
```json
{"fields": [
  {"name": "last_name", "page": 1, "type": "text",
   "rect": [92, 700, 260, 716], "value": null},
  {"name": "over_18", "page": 1, "type": "checkbox",
   "rect": [285, 197, 292, 205], "value": null,
   "states": {"checked_value": "/Yes", "unchecked_value": "/Off"}},
  {"name": "plan", "page": 2, "type": "radio",
   "rect": [...], "options": ["/Basic", "/Pro"]},
  {"name": "country", "page": 1, "type": "choice",
   "options": ["USA", "Canada", "Mexico"]}
]}
```
- Empty `{"fields": []}` + a stderr note → the form is **flat**. Go to
  **Flat forms** below.
- Otherwise → **Fillable forms**.

Page numbers here are **1-based**. `rect` is `[left, bottom, right, top]` in
PDF points (origin bottom-left).

## Fillable forms

1. Read the schema. For each field note its `type` and, for checkboxes, the
   exact `checked_value` — it is a **trap**: it may be `/Yes`, `/On`, `/1`, or a
   field-specific string. Never guess; use what the schema reports. For
   radio/choice, values must come from `options`.

   If it's unclear what a field is *for*, render the page and look:
   `python3 <skill>/scripts/pdf_to_images.py in.pdf /tmp/form` then read the PNG.

2. Write a flat `values.json` mapping name → value:
   ```json
   {
     "last_name": "Simpson",
     "over_18": true,
     "plan": "/Pro",
     "country": "Canada"
   }
   ```
   Checkboxes accept `true`/`false` (mapped to the schema's checked/unchecked
   values) or the exact state string. Radio/choice must be one of `options`.

3. Fill — the script validates **every** name and value against the schema and
   exits 1 with per-field errors *before* writing anything:
   ```bash
   python3 <skill>/scripts/fill_form_fields.py in.pdf values.json out.pdf
   ```
   Under the hood: pypdf `update_page_form_field_values(page, {...},
   auto_regenerate=False)` per page, then `writer.set_need_appearances_writer(True)`.

4. **Verify**: render and read the PNGs.
   ```bash
   python3 <skill>/scripts/pdf_to_images.py out.pdf /tmp/pdfcheck
   ```

### Why filled values sometimes look blank

pypdf writes the value but not always its visual appearance stream. Viewers that
don't regenerate appearances then show an empty field. `NeedAppearances=true`
(set by `fill_form_fields.py`) tells the viewer to render values itself. This is
the canonical fix — if you ever hand-roll a fill, set it too.

## Flat forms (no fillable fields)

There are no widgets, so you overlay text/marks at coordinates.

1. Render pages: `python3 <skill>/scripts/pdf_to_images.py in.pdf /tmp/form`.
2. Read each PNG and locate the blanks/checkbox squares. Note the pixel position
   (image origin is **top-left**).
3. Convert image pixels → PDF points. **The y-axis is flipped** (PDF origin is
   bottom-left):
   ```
   pdf_x = image_x * page_width  / image_width
   pdf_y = page_height - image_y * page_height / image_height
   ```
4. Place text with a pypdf FreeText annotation per field:
   ```python
   from pypdf import PdfReader, PdfWriter
   from pypdf.annotations import FreeText

   r = PdfReader("in.pdf"); w = PdfWriter(); w.append(r)
   ph = float(r.pages[0].mediabox.height); pw = float(r.pages[0].mediabox.width)

   def place(page_idx, text, img_x, img_y, img_w, img_h, size=11):
       x = img_x * pw / img_w
       y = ph - img_y * ph / img_h          # y-axis flip
       a = FreeText(text=text, rect=(x, y - size, x + 200, y + size),
                    font_size=f"{size}pt", border_color=None, background_color=None)
       w.add_annotation(page_number=page_idx, annotation=a)

   place(0, "Simpson", 255, 175, 1700, 2200)   # img coords from step 2
   place(0, "X", 285, 640, 1700, 2200)          # a checkbox mark
   w.write("out.pdf")
   ```
5. **Verify** by re-rendering and reading the PNGs; nudge coordinates and repeat
   until each mark sits correctly.
