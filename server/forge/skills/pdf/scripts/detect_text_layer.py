#!/usr/bin/env python3
"""Report whether a PDF has an extractable text layer (vs scanned images).

Prints a per-page char-count table and a verdict line the agent can grep:
  TEXT_LAYER: yes | no | partial
"no" = average < 50 chars/page (likely scanned -> needs OCR).
"partial" = some pages have text, others near-empty.
"""
from __future__ import annotations

import argparse
import sys

import pdfplumber


def main() -> int:
    ap = argparse.ArgumentParser(description="Detect a PDF text layer.")
    ap.add_argument("pdf")
    args = ap.parse_args()

    try:
        pdf = pdfplumber.open(args.pdf)
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot open {args.pdf}: {e}", file=sys.stderr)
        return 1

    counts = []
    with pdf:
        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            n = len(text.strip())
            counts.append(n)
            print(f"page {i + 1}: {n} chars")

    if not counts:
        print("TEXT_LAYER: no")
        return 0

    avg = sum(counts) / len(counts)
    empty = sum(1 for c in counts if c < 50)
    if avg < 50:
        verdict = "no"
    elif empty > 0:
        verdict = "partial"
    else:
        verdict = "yes"
    print(f"avg {avg:.0f} chars/page, {empty}/{len(counts)} pages near-empty")
    print(f"TEXT_LAYER: {verdict}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
