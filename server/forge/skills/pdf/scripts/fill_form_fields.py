#!/usr/bin/env python3
"""Fill a fillable PDF form from a values JSON, validating first.

values.json is a flat object: {"field_name": value, ...}. Every name must
exist in the form and every value must be legal for its field type. All
errors are collected and printed BEFORE any write; the script exits 1 without
producing output if anything is invalid.

Fill uses pypdf update_page_form_field_values(page, {...}, auto_regenerate=False)
per page, then writer.set_need_appearances_writer(True) — the canonical fix for
values that fill but render invisible in many viewers.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader, PdfWriter


def _schema(pdf: str) -> dict:
    here = Path(__file__).resolve().parent
    res = subprocess.run(
        [sys.executable, str(here / "extract_form_fields.py"), pdf],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        print(res.stderr, file=sys.stderr, end="")
        raise SystemExit(1)
    data = json.loads(res.stdout)
    return {f["name"]: f for f in data["fields"]}


def _coerce(name: str, value, spec: dict, errors: list[str]):
    ftype = spec["type"]
    if ftype == "checkbox":
        st = spec.get("states", {})
        checked, unchecked = st.get("checked_value", "/Yes"), st.get("unchecked_value", "/Off")
        if isinstance(value, bool):
            return checked if value else unchecked
        if value in (checked, unchecked):
            return value
        errors.append(
            f"{name}: checkbox value {value!r} not allowed; use true/false or "
            f"{checked!r}/{unchecked!r}")
        return None
    if ftype in ("radio", "choice"):
        opts = spec.get("options", [])
        if opts and value not in opts:
            errors.append(f"{name}: value {value!r} not in options {opts}")
            return None
        return value
    return str(value)


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate and fill a PDF form.")
    ap.add_argument("pdf")
    ap.add_argument("values", help="JSON object {field_name: value}")
    ap.add_argument("out")
    args = ap.parse_args()

    try:
        schema = _schema(args.pdf)
    except SystemExit:
        return 1
    try:
        values = json.loads(Path(args.values).read_text())
    except Exception as e:  # noqa: BLE001
        print(f"error: cannot read values JSON: {e}", file=sys.stderr)
        return 1
    if not isinstance(values, dict):
        print("error: values JSON must be an object {field_name: value}", file=sys.stderr)
        return 1

    errors: list[str] = []
    coerced: dict[str, object] = {}
    for name, value in values.items():
        spec = schema.get(name)
        if spec is None:
            errors.append(f"{name}: no such field. Known fields: {sorted(schema)}")
            continue
        cv = _coerce(name, value, spec, errors)
        if cv is not None:
            coerced[name] = cv

    if errors:
        print("validation failed:", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        return 1

    reader = PdfReader(args.pdf)
    writer = PdfWriter()
    writer.append(reader)

    # Group values by the page each field lives on.
    by_page: dict[int, dict] = {}
    for name, cv in coerced.items():
        by_page.setdefault(schema[name]["page"] or 1, {})[name] = cv
    for page_no, vals in by_page.items():
        writer.update_page_form_field_values(
            writer.pages[page_no - 1], vals, auto_regenerate=False)

    writer.set_need_appearances_writer(True)
    with open(args.out, "wb") as fh:
        writer.write(fh)

    print(f"filled {len(coerced)} field(s) -> {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
