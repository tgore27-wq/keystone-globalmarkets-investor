#!/usr/bin/env python3
"""
convert_to_pdf.py
Converts a market report markdown file to a styled PDF.
Uses xhtml2pdf (pure Python, no system dependencies).

Usage:
  python convert_to_pdf.py Open/Open_06-29-26.md
  python convert_to_pdf.py Open/Open_06-29-26.md --out /tmp/report.pdf
"""

import argparse
import sys
from pathlib import Path

try:
    import markdown
    from xhtml2pdf import pisa
except ImportError:
    import subprocess
    subprocess.check_call([sys.executable, "-m", "pip", "install",
                           "xhtml2pdf", "markdown", "-q"])
    import markdown
    from xhtml2pdf import pisa

# ---------------------------------------------------------------------------
# Stylesheet
# ---------------------------------------------------------------------------

CSS = """
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 9.5pt;
    line-height: 1.55;
    color: #111827;
    margin: 0;
    padding: 0;
}

/* Report title */
h1 {
    font-size: 18pt;
    font-weight: bold;
    color: #0a2342;
    border-bottom: 3px solid #0a2342;
    padding-bottom: 6px;
    margin-top: 0;
    margin-bottom: 4px;
}

/* Section headers */
h2 {
    font-size: 12pt;
    font-weight: bold;
    color: #0a2342;
    border-bottom: 1px solid #c8d6e5;
    padding-bottom: 3px;
    margin-top: 18px;
    margin-bottom: 5px;
}

/* Sub-headers */
h3 {
    font-size: 10.5pt;
    font-weight: bold;
    color: #1e3a5f;
    margin-top: 10px;
    margin-bottom: 4px;
}

/* Tables */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 8px 0 12px 0;
    font-size: 8pt;
}

th {
    background-color: #0a2342;
    color: #ffffff;
    font-weight: bold;
    padding: 5px 7px;
    text-align: left;
}

td {
    padding: 4px 7px;
    border-bottom: 1px solid #e5ecf4;
}

tr.even td {
    background-color: #f5f8fc;
}

/* Text elements */
p {
    margin: 5px 0;
}

ul, ol {
    margin: 5px 0 8px 0;
    padding-left: 18px;
}

li {
    margin: 3px 0;
}

strong {
    font-weight: bold;
    color: #0a2342;
}

hr {
    border: 1px solid #dde6f0;
    margin: 10px 0;
}

code {
    font-family: Courier, monospace;
    font-size: 7.5pt;
    background: #f0f4f9;
    padding: 1px 3px;
    color: #374151;
}

em {
    color: #6b7280;
    font-size: 7.5pt;
}

blockquote {
    border-left: 3px solid #0a2342;
    margin: 8px 0 8px 4px;
    padding: 4px 12px;
    background: #f5f8fc;
    color: #374151;
    font-style: italic;
}

@page {
    size: letter;
    margin: 0.70in 0.75in 0.80in 0.75in;
    @frame footer {
        -pdf-frame-content: footer-content;
        bottom: 0.35in;
        margin-left: 0.75in;
        margin-right: 0.75in;
        height: 0.3in;
    }
}
"""

FOOTER_HTML = """
<div id="footer-content" style="font-size:7pt; color:#9ca3af;
     text-align:center; border-top:1px solid #e5ecf4; padding-top:4px;">
  GlobalMarkets Investor &nbsp;·&nbsp; For informational purposes only
  &nbsp;·&nbsp; Page <pdf:pagenumber> of <pdf:pagecount>
</div>
"""

# ---------------------------------------------------------------------------
# Zebra-stripe helper
# ---------------------------------------------------------------------------

def _zebra_tables(html: str) -> str:
    """Add alternating row classes to all tables for even/odd styling."""
    import re
    result = []
    row_count = [0]
    in_tbody = [False]

    for line in html.splitlines(keepends=True):
        if "<tbody>" in line.lower():
            in_tbody[0] = True
            row_count[0] = 0
        elif "</tbody>" in line.lower():
            in_tbody[0] = False
        elif in_tbody[0] and "<tr>" in line.lower():
            cls = "even" if row_count[0] % 2 == 1 else "odd"
            line = line.replace("<tr>", f'<tr class="{cls}">', 1)
            row_count[0] += 1
        result.append(line)

    return "".join(result)

# ---------------------------------------------------------------------------
# Converter
# ---------------------------------------------------------------------------

def convert(md_path: Path, out_path: Path = None) -> Path:
    if out_path is None:
        out_path = md_path.with_suffix(".pdf")

    md_text = md_path.read_text(encoding="utf-8")

    html_body = markdown.markdown(
        md_text,
        extensions=["tables", "fenced_code", "nl2br"],
    )

    html_body = _zebra_tables(html_body)

    full_html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <style>{CSS}</style>
</head>
<body>
{html_body}
{FOOTER_HTML}
</body>
</html>"""

    with open(out_path, "wb") as fh:
        result = pisa.CreatePDF(full_html, dest=fh, encoding="utf-8")

    if result.err:
        raise RuntimeError(f"PDF conversion failed with {result.err} error(s)")

    return out_path

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Convert market report markdown to PDF")
    parser.add_argument("files", nargs="+", help=".md files to convert")
    parser.add_argument("--out", default=None,
                        help="Output path (single-file only)")
    args = parser.parse_args()

    if args.out and len(args.files) > 1:
        print("[ERROR] --out can only be used with a single input file.")
        sys.exit(1)

    success = True
    for f in args.files:
        p = Path(f)
        out = Path(args.out) if args.out else None
        try:
            result = convert(p, out)
            print(f"✓ {p.name} → {result}")
        except Exception as e:
            print(f"✗ {p.name} failed: {e}")
            success = False

    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
