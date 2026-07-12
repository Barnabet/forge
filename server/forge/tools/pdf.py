from __future__ import annotations

import base64
import io

import pdfplumber

from forge.tools.base import Tool, ToolContext, ToolResult, truncate_middle

MAX_VIEW_PAGES = 8  # cap pages per call so the image payload stays bounded
MAX_RENDER_WIDTH = 1100  # px; large/high-dpi pages are scaled down to fit
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff"}


def _parse_pages(spec: str, n: int) -> list[int]:
    """Parse a 1-indexed page spec like "1-5,8" into a sorted list of 0-indexed pages."""
    pages: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a_s, _, b_s = part.partition("-")
            a, b = int(a_s), int(b_s)
            if a > b:
                raise ValueError(f"invalid range '{part}'")
            rng = range(a, b + 1)
        else:
            v = int(part)
            rng = range(v, v + 1)
        for p in rng:
            if p < 1 or p > n:
                raise ValueError(f"page {p} out of range (1-{n})")
            pages.append(p - 1)
    if not pages:
        raise ValueError("no pages selected")
    return sorted(set(pages))


class ReadPdfTool(Tool):
    name = "read_pdf"
    description = (
        "Read a PDF and return extracted text per page. Use `pages` (e.g. \"1-5,8\") to "
        "select 1-indexed pages/ranges, and `mode` (\"text\" or \"layout\") — layout "
        "preserves whitespace alignment for tables/columns. Flags scanned PDFs with no "
        "text layer.")
    params = {"type": "object", "properties": {
        "path": {"type": "string"},
        "pages": {"type": "string",
                  "description": "1-indexed pages/ranges, e.g. \"1-5,8\""},
        "mode": {"type": "string", "enum": ["text", "layout"], "default": "text",
                 "description": "layout preserves whitespace alignment for tables/columns"},
    }, "required": ["path"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("path", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)
        mode = args.get("mode") or "text"
        layout = mode == "layout"

        try:
            pdf = pdfplumber.open(str(path))
        except Exception as e:  # noqa: BLE001
            msg = str(e).lower()
            if "password" in msg or "encrypt" in msg:
                return ToolResult(
                    output=f"The PDF is password-protected/encrypted: {args['path']}",
                    is_error=True)
            return ToolResult(
                output=f"Could not open PDF (corrupt or not a PDF): {args['path']} — {e}",
                is_error=True)

        try:
            with pdf:
                n = len(pdf.pages)
                if n == 0:
                    return ToolResult(output=f"PDF has no pages: {args['path']}",
                                      is_error=True)

                pages_arg = args.get("pages")
                if pages_arg:
                    try:
                        selected = _parse_pages(pages_arg, n)
                    except ValueError as e:
                        return ToolResult(output=f"Invalid pages spec: {e}", is_error=True)
                    paginated = False
                else:
                    if n > 20:
                        selected = list(range(20))
                        paginated = True
                    else:
                        selected = list(range(n))
                        paginated = False

                def extract(idx: int) -> str:
                    return pdf.pages[idx].extract_text(layout=layout) or ""

                # Scanned detection: only when no explicit pages requested.
                if not pages_arg:
                    sample = min(5, n)
                    total = sum(len(extract(i).strip()) for i in range(sample))
                    if total < 50 * sample:
                        return ToolResult(output=(
                            f"PDF: {args['path']} — {n} pages\n\n"
                            "This PDF appears to be scanned / has no text layer "
                            f"(extracted only {total} chars from the first {sample} "
                            "pages). Load the `pdf` skill and run "
                            "`ocrmypdf --skip-text in.pdf out.pdf` to add a text layer, "
                            "then read the result."))

                blocks = [f"PDF: {args['path']} — {n} pages"]
                for idx in selected:
                    text = extract(idx).rstrip()
                    blocks.append(f"--- page {idx + 1} ---\n"
                                  f"{text if text else '(no extractable text)'}")
                if paginated:
                    blocks.append(
                        f"[showing pages 1-20 of {n}; pass pages=\"21-40\" for more]")
        except Exception as e:  # noqa: BLE001
            return ToolResult(output=f"Failed to read PDF: {args['path']} — {e}",
                              is_error=True)

        return ToolResult(output=truncate_middle("\n\n".join(blocks)))


class ViewTool(Tool):
    name = "view"
    description = (
        "View a PDF or an image file — renders it to images you can see, to "
        "visually inspect layout (overlap, clipping, spacing, colors, charts) that "
        "text extraction can't catch, e.g. the render-and-verify step after "
        "creating a PDF, or to look at a PNG/JPG directly. For PDFs, use `pages` "
        f"(e.g. \"1-5,8\") to select 1-indexed pages (max {MAX_VIEW_PAGES} per "
        "call) and `dpi` (default 120) for resolution; both are ignored for "
        "images.")
    params = {"type": "object", "properties": {
        "path": {"type": "string"},
        "pages": {"type": "string",
                  "description": "PDF only: 1-indexed pages/ranges, e.g. \"1-5,8\""},
        "dpi": {"type": "integer", "default": 120,
                "description": "PDF only: render resolution (default 120)"},
    }, "required": ["path"]}
    read_only = True

    def display(self, args: dict) -> str:
        return args.get("path", self.name)

    async def run(self, args: dict, ctx: ToolContext) -> ToolResult:
        path = ctx.resolve(args["path"])
        if not path.is_file():
            return ToolResult(output=f"File not found: {args['path']}", is_error=True)

        suffix = path.suffix.lower()
        if suffix in IMAGE_EXTS:
            return self._view_image(path, args["path"])
        if suffix == ".pdf":
            return self._view_pdf(path, args)
        return ToolResult(
            output=f"Unsupported file type '{suffix or path.name}': view accepts a "
                   f".pdf or an image ({', '.join(sorted(IMAGE_EXTS))}).",
            is_error=True)

    def _view_image(self, path, display: str) -> ToolResult:
        from PIL import Image

        try:
            with Image.open(path) as im:
                im.load()
                img = im.convert("RGB")
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                output=f"Could not open image (corrupt or not an image): "
                       f"{display} — {e}", is_error=True)

        w, h = img.size
        if w > MAX_RENDER_WIDTH:
            new_h = round(h * MAX_RENDER_WIDTH / w)
            img = img.resize((MAX_RENDER_WIDTH, new_h))
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode("ascii")
        out = f"Viewed image {display} ({w}x{h})."
        return ToolResult(output=out, images=[f"data:image/png;base64,{b64}"])

    def _view_pdf(self, path, args: dict) -> ToolResult:
        import pypdfium2 as pdfium

        try:
            doc = pdfium.PdfDocument(str(path))
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                output=f"Could not open PDF (corrupt, encrypted, or not a PDF): "
                       f"{args['path']} — {e}", is_error=True)

        try:
            n = len(doc)
            if n == 0:
                return ToolResult(output=f"PDF has no pages: {args['path']}",
                                  is_error=True)

            pages_arg = args.get("pages")
            if pages_arg:
                try:
                    selected = _parse_pages(pages_arg, n)
                except ValueError as e:
                    return ToolResult(output=f"Invalid pages spec: {e}", is_error=True)
            else:
                selected = list(range(n))

            truncated = len(selected) > MAX_VIEW_PAGES
            selected = selected[:MAX_VIEW_PAGES]

            try:
                dpi = int(args.get("dpi") or 120)
            except (TypeError, ValueError):
                dpi = 120
            dpi = max(48, min(dpi, 300))
            scale = dpi / 72.0

            images: list[str] = []
            for idx in selected:
                page = doc[idx]
                pt_width = page.get_size()[0]
                s = min(scale, MAX_RENDER_WIDTH / pt_width)
                img = page.render(scale=s).to_pil().convert("RGB")
                buf = io.BytesIO()
                img.save(buf, format="PNG")
                b64 = base64.b64encode(buf.getvalue()).decode("ascii")
                images.append(f"data:image/png;base64,{b64}")
        finally:
            doc.close()

        shown = ", ".join(str(i + 1) for i in selected)
        out = f"Rendered {len(images)} page(s) of {args['path']} (pages {shown}) at {dpi} dpi."
        if truncated:
            out += (f" Showing the first {MAX_VIEW_PAGES}; pass `pages` to view others.")
        return ToolResult(output=out, images=images)
