#!/usr/bin/env python3
"""Render PDF pages to PNG with pypdfium2 (no AGPL/GPL deps).

Used for the render-and-verify loop: render a generated or edited PDF, then
read the PNGs back to confirm layout before declaring the task done.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pypdfium2 as pdfium


def _parse_pages(spec: str, n: int) -> list[int]:
    """Parse '1-5,8' into 0-based page indexes."""
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a) - 1, int(b)))
        else:
            out.append(int(part) - 1)
    return [i for i in out if 0 <= i < n]


def main() -> int:
    ap = argparse.ArgumentParser(description="Render PDF pages to PNG.")
    ap.add_argument("pdf")
    ap.add_argument("outdir")
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--pages", help='e.g. "1-5,8"; default all')
    args = ap.parse_args()

    try:
        doc = pdfium.PdfDocument(args.pdf)
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot open {args.pdf}: {e}", file=sys.stderr)
        return 1

    n = len(doc)
    idxs = _parse_pages(args.pages, n) if args.pages else list(range(n))
    if not idxs:
        print("error: no pages selected", file=sys.stderr)
        return 1

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)

    scale = args.dpi / 72.0
    for i in idxs:
        page = doc[i]
        # Cap render width at 1000px so large/high-dpi pages stay manageable.
        pt_width = page.get_size()[0]
        if pt_width * scale > 1000:
            scale_i = 1000.0 / pt_width
        else:
            scale_i = scale
        bitmap = page.render(scale=scale_i)
        img = bitmap.to_pil()
        path = outdir / f"page-{i + 1}.png"
        img.save(path)
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
