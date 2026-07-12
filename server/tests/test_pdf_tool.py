from pathlib import Path

import pypdf

from forge.tools.base import ToolContext
from forge.tools.pdf import MAX_VIEW_PAGES, ReadPdfTool, ViewTool


def ctx(tmp_path):
    return ToolContext(cwd=tmp_path)


def _text_pdf(path: Path, page_texts: list[str]) -> None:
    """Write a minimal valid multi-page PDF with a Helvetica text stream per page."""
    objs: list[bytes] = []

    def add(body: bytes) -> int:
        objs.append(body)
        return len(objs)

    font_id = add(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    kids: list[int] = []
    for text in page_texts:
        esc = text.replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 24 Tf 72 720 Td ({esc}) Tj ET".encode()
        content_id = add(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
        page_id = add(
            b"<< /Type /Page /Parent 999 0 R /MediaBox [0 0 612 792] "
            b"/Resources << /Font << /F1 %d 0 R >> >> /Contents %d 0 R >>"
            % (font_id, content_id))
        kids.append(page_id)

    kids_ref = b" ".join(b"%d 0 R" % k for k in kids)
    pages_id = add(b"<< /Type /Pages /Count %d /Kids [%s] >>" % (len(kids), kids_ref))
    for k in kids:
        objs[k - 1] = objs[k - 1].replace(b"/Parent 999 0 R", b"/Parent %d 0 R" % pages_id)
    root_id = add(b"<< /Type /Catalog /Pages %d 0 R >>" % pages_id)

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (i, body)
    xref_pos = len(out)
    out += b"xref\n0 %d\n" % (len(objs) + 1)
    out += b"0000000000 65535 f \n"
    for off in offsets:
        out += b"%010d 00000 n \n" % off
    out += (b"trailer\n<< /Size %d /Root %d 0 R >>\nstartxref\n%d\n%%%%EOF"
            % (len(objs) + 1, root_id, xref_pos))
    path.write_bytes(bytes(out))


def _blank_pdf(path: Path, n: int = 1) -> None:
    w = pypdf.PdfWriter()
    for _ in range(n):
        w.add_blank_page(width=612, height=792)
    with path.open("wb") as f:
        w.write(f)


async def test_multi_page_extraction(tmp_path):
    _text_pdf(tmp_path / "doc.pdf",
              ["Page one has plenty of readable text here to exceed the threshold length",
               "Page two also has plenty of readable text here to clear the threshold too"])
    r = await ReadPdfTool().run({"path": "doc.pdf"}, ctx(tmp_path))
    assert not r.is_error
    assert "PDF: doc.pdf — 2 pages" in r.output
    assert "--- page 1 ---" in r.output and "--- page 2 ---" in r.output
    assert "Page one" in r.output and "Page two" in r.output


async def test_pages_selection(tmp_path):
    _text_pdf(tmp_path / "doc.pdf",
              ["Page one has plenty of readable text here to exceed the threshold length",
               "Page two also has plenty of readable text here to clear the threshold too"])
    r = await ReadPdfTool().run({"path": "doc.pdf", "pages": "2"}, ctx(tmp_path))
    assert not r.is_error
    assert "--- page 2 ---" in r.output and "--- page 1 ---" not in r.output
    assert "Page two" in r.output and "Page one" not in r.output


async def test_scanned_pdf_flagged(tmp_path):
    _blank_pdf(tmp_path / "scan.pdf", n=2)
    r = await ReadPdfTool().run({"path": "scan.pdf"}, ctx(tmp_path))
    assert not r.is_error
    assert "scanned" in r.output.lower()
    assert "ocrmypdf" in r.output
    assert "2 pages" in r.output


async def test_missing_file_is_error(tmp_path):
    r = await ReadPdfTool().run({"path": "nope.pdf"}, ctx(tmp_path))
    assert r.is_error and "not found" in r.output.lower()


async def test_invalid_pages_spec_is_error(tmp_path):
    _text_pdf(tmp_path / "doc.pdf",
              ["Page one has plenty of readable text here to exceed the threshold length"])
    r = await ReadPdfTool().run({"path": "doc.pdf", "pages": "5"}, ctx(tmp_path))
    assert r.is_error and "out of range" in r.output.lower()


def test_display():
    assert ReadPdfTool().display({"path": "a.pdf"}) == "a.pdf"


async def test_view_pdf_renders_images(tmp_path):
    _blank_pdf(tmp_path / "doc.pdf", n=3)
    r = await ViewTool().run({"path": "doc.pdf"}, ctx(tmp_path))
    assert not r.is_error
    assert len(r.images) == 3
    assert all(u.startswith("data:image/png;base64,") for u in r.images)
    assert "Rendered 3 page(s)" in r.output


async def test_view_pdf_page_selection(tmp_path):
    _blank_pdf(tmp_path / "doc.pdf", n=3)
    r = await ViewTool().run({"path": "doc.pdf", "pages": "2"}, ctx(tmp_path))
    assert not r.is_error
    assert len(r.images) == 1
    assert "pages 2)" in r.output


async def test_view_pdf_caps_page_count(tmp_path):
    _blank_pdf(tmp_path / "doc.pdf", n=MAX_VIEW_PAGES + 3)
    r = await ViewTool().run({"path": "doc.pdf"}, ctx(tmp_path))
    assert not r.is_error
    assert len(r.images) == MAX_VIEW_PAGES
    assert "Showing the first" in r.output


async def test_view_pdf_missing_file_is_error(tmp_path):
    r = await ViewTool().run({"path": "nope.pdf"}, ctx(tmp_path))
    assert r.is_error and "not found" in r.output.lower()


async def test_view_pdf_invalid_pages_spec_is_error(tmp_path):
    _blank_pdf(tmp_path / "doc.pdf", n=1)
    r = await ViewTool().run({"path": "doc.pdf", "pages": "9"}, ctx(tmp_path))
    assert r.is_error and "out of range" in r.output.lower()


def test_view_pdf_display():
    assert ViewTool().display({"path": "a.pdf"}) == "a.pdf"


async def test_view_image_renders_inline(tmp_path):
    from PIL import Image

    Image.new("RGB", (40, 30), "red").save(tmp_path / "pic.png")
    r = await ViewTool().run({"path": "pic.png"}, ctx(tmp_path))
    assert not r.is_error
    assert len(r.images) == 1
    assert r.images[0].startswith("data:image/png;base64,")
    assert "Viewed image pic.png (40x30)" in r.output


async def test_view_image_downscales_wide(tmp_path):
    from PIL import Image

    Image.new("RGB", (2200, 1100), "blue").save(tmp_path / "wide.png")
    r = await ViewTool().run({"path": "wide.png"}, ctx(tmp_path))
    assert not r.is_error and len(r.images) == 1
    assert "(2200x1100)" in r.output  # summary reports the ORIGINAL dimensions


async def test_view_image_jpeg(tmp_path):
    from PIL import Image

    Image.new("RGB", (20, 20), "green").save(tmp_path / "pic.jpg")
    r = await ViewTool().run({"path": "pic.jpg"}, ctx(tmp_path))
    assert not r.is_error and len(r.images) == 1


async def test_view_unsupported_type_is_error(tmp_path):
    (tmp_path / "notes.txt").write_text("hello")
    r = await ViewTool().run({"path": "notes.txt"}, ctx(tmp_path))
    assert r.is_error and "unsupported" in r.output.lower()


async def test_view_missing_file_is_error(tmp_path):
    r = await ViewTool().run({"path": "nope.png"}, ctx(tmp_path))
    assert r.is_error and "not found" in r.output.lower()
