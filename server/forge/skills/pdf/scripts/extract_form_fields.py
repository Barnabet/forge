#!/usr/bin/env python3
"""Extract PDF AcroForm fields to JSON on stdout.

Walks reader.get_fields() for logical field data AND every page's widget
annotations to attach a 1-based page number and rect to each field. Checkbox
on/off states come from the widget's /AP /N appearance dictionary (the
"/Yes" vs "/On" trap), so we report them explicitly.

Output: {"fields": [{name, page, type, rect, value, states?, options?}]}
Flat PDF (no AcroForm) -> {"fields": []} plus a stderr note.
"""
from __future__ import annotations

import argparse
import json
import sys

from pypdf import PdfReader
from pypdf.generic import IndirectObject


def _resolve(obj):
    return obj.get_object() if isinstance(obj, IndirectObject) else obj


_TYPE_MAP = {"/Tx": "text", "/Btn": "button", "/Ch": "choice", "/Sig": "signature"}


def main() -> int:
    ap = argparse.ArgumentParser(description="Extract PDF form fields to JSON.")
    ap.add_argument("pdf", help="input PDF path")
    args = ap.parse_args()

    try:
        reader = PdfReader(args.pdf)
    except Exception as e:  # noqa: BLE001 - surface any read error cleanly
        print(f"error: cannot read {args.pdf}: {e}", file=sys.stderr)
        return 1

    fields = reader.get_fields()
    if not fields:
        print("note: no AcroForm found; this PDF has no fillable fields (flat).",
              file=sys.stderr)
        print(json.dumps({"fields": []}, indent=2))
        return 0

    # Map each field name -> (page_number, rect, states) from page widgets.
    widget_info: dict[str, dict] = {}
    for pidx, page in enumerate(reader.pages):
        annots = page.get("/Annots")
        if not annots:
            continue
        for a in annots:
            w = _resolve(a)
            if w.get("/Subtype") != "/Widget":
                continue
            # Field name: on the widget, or on its parent for kids.
            name = w.get("/T")
            parent = _resolve(w.get("/Parent")) if w.get("/Parent") else None
            if name is None and parent is not None:
                name = parent.get("/T")
            if name is None:
                continue
            name = str(name)
            rect = [float(x) for x in w.get("/Rect", [0, 0, 0, 0])]
            info = widget_info.setdefault(
                name, {"page": pidx + 1, "rect": rect, "states": set()})
            # Checkbox/radio appearance states under /AP /N.
            ap = _resolve(w.get("/AP")) if w.get("/AP") else None
            if ap and ap.get("/N"):
                n = _resolve(ap.get("/N"))
                if hasattr(n, "keys"):
                    for k in n.keys():
                        info["states"].add(str(k))

    out = []
    for name, f in fields.items():
        ft = f.get("/FT")
        ftype = _TYPE_MAP.get(ft, str(ft) if ft else "unknown")
        wi = widget_info.get(name, {})
        entry = {
            "name": name,
            "page": wi.get("page"),
            "type": ftype,
            "rect": wi.get("rect"),
            "value": f.get("/V"),
        }
        states = {s for s in wi.get("states", set()) if s != "/Off"}
        if ftype == "button":
            if states:
                # Checkbox: single on-state; radio: multiple.
                if len(states) == 1:
                    entry["type"] = "checkbox"
                    entry["states"] = {
                        "checked_value": next(iter(states)),
                        "unchecked_value": "/Off",
                    }
                else:
                    entry["type"] = "radio"
                    entry["options"] = sorted(states)
        elif ftype == "choice":
            opts = f.get("/_States_") or f.get("/Opt")
            if opts:
                entry["options"] = [
                    (o[1] if isinstance(o, (list, tuple)) else str(o)) for o in opts
                ]
        out.append(entry)

    print(json.dumps({"fields": out}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
