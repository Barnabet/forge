from pypdf import PdfReader, PdfWriter

src = PdfReader("showcase.pdf")
stamp = PdfReader("stamp.pdf").pages[0]
w = PdfWriter()

# Stamp every page except the cover (index 0).
for i, page in enumerate(src.pages):
    if i != 0:
        page.merge_page(stamp)
    w.add_page(page)

# Document metadata.
w.add_metadata({
    "/Title": "Impression No. 01 — A Specimen of Everything This Press Can Print",
    "/Author": "The Forge Press",
    "/Subject": "A risograph spot-color specimen, composed in Typst, finished with pypdf",
    "/Keywords": "typst, pypdf, vector, riso, specimen, halftone, overprint",
    "/Creator": "Typst 0.15",
    "/Producer": "pypdf post-press",
})

# Navigable outline (bookmarks) — page indexes are 0-based.
outline = [
    ("Cover", 0),
    ("Run Sheet — Contents", 1),
    ("Plate 03 · The Ink Library", 2),
    ("Plate 04 · Halftones & Gradients", 3),
    ("Plate 05 · Data, Plotted", 4),
    ("Plate 06 · The Press, Diagrammed", 5),
    ("Plate 07 · Registration & Overprint", 6),
    ("Plate 08 · Quality Control & Colophon", 7),
]
for title, idx in outline:
    w.add_outline_item(title, idx)

with open("impression_specimen.pdf", "wb") as f:
    w.write(f)

# Verify.
out = PdfReader("impression_specimen.pdf")
print("pages:", len(out.pages))
print("title:", out.metadata.title)
print("author:", out.metadata.author)
print("outline items:", len(out.outline))
