"""Render docs/QA_GUIDE.md to docs/QA_GUIDE.pdf.

Handles the markdown subset the guide uses: # headings, --- rules, fenced code
blocks, pipe tables, ![images](relative paths), - lists, and inline bold /
italic / code / links (via fpdf2's markdown=True cells).

Run from the repo root:  .venv/Scripts/python.exe scripts/build_qa_guide_pdf.py
"""

import re
import sys
from pathlib import Path

from fpdf import FPDF

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "docs" / "QA_GUIDE.md"
OUT = REPO / "docs" / "QA_GUIDE.pdf"
FONT_DIR = Path("C:/Windows/Fonts")


class GuidePDF(FPDF):
    def header(self):
        self.set_font("Arial", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, "QA Guide — Data Reconciliation Testing Framework", new_x="LMARGIN",
                  new_y="NEXT", align="R")
        self.set_text_color(0)

    def footer(self):
        self.set_y(-12)
        self.set_font("Arial", "", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"{self.page_no()}", align="C")


def add_fonts(pdf):
    pdf.add_font("Arial", "", str(FONT_DIR / "arial.ttf"))
    pdf.add_font("Arial", "B", str(FONT_DIR / "arialbd.ttf"))
    pdf.add_font("Arial", "I", str(FONT_DIR / "ariali.ttf"))
    pdf.add_font("Arial", "BI", str(FONT_DIR / "arialbi.ttf"))
    pdf.add_font("CourierNew", "", str(FONT_DIR / "cour.ttf"))
    pdf.add_font("CourierNew", "B", str(FONT_DIR / "courbd.ttf"))


def heading(pdf, level, text):
    size = {1: 17, 2: 13, 3: 11}.get(level, 10)
    pdf.ln(2)
    pdf.set_font("Arial", "B", size)
    if level == 1:
        pdf.set_fill_color(235, 240, 246)
        pdf.multi_cell(0, 9, f" {text}", fill=True, markdown=True, new_x="LMARGIN", new_y="NEXT")
    else:
        pdf.multi_cell(0, 7, text, markdown=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1)


def code_block(pdf, lines):
    pdf.set_font("CourierNew", "", 8)
    pdf.set_fill_color(243, 243, 243)
    pdf.set_draw_color(200)
    pdf.set_x(pdf.l_margin)
    for line in lines:
        pdf.multi_cell(0, 4.2, " " + line, fill=True, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(1.5)


def strip_md(text):
    """Remove inline markers fpdf2 can't render (italics, code spans, bold in
    table cells) so nothing shows up literally in the PDF."""
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*+([^*]+)\*+", r"\1", text)
    return text


def table_block(pdf, rows):
    parsed = [
        [strip_md(c.strip()) for c in row.strip().strip("|").split("|")]
        for row in rows
        if not re.fullmatch(r"\|[\s\-|]+\|?", row.strip())
    ]
    if not parsed:
        return
    pdf.set_font("Arial", "", 8.5)
    with pdf.table(text_align="LEFT", line_height=5, width=pdf.epw,
                   headings_style=fpdf_fonts_style()) as tbl:
        for i, row in enumerate(parsed):
            tr = tbl.row()
            for cell in row:
                pdf.set_font("Arial", "B" if i == 0 else "", 8.5)
                tr.cell(cell)


def fpdf_fonts_style():
    from fpdf.fonts import FontFace

    return FontFace(emphasis="BOLD", fill_color=(235, 240, 246))


def paragraph(pdf, text):
    pdf.set_font("Arial", "", 10)
    pdf.multi_cell(0, 5, text, markdown=True, new_x="LMARGIN", new_y="NEXT")


def image(pdf, alt, rel):
    path = SRC.parent / rel
    if not path.exists():
        paragraph(pdf, f"[missing image: {rel}]")
        return
    pdf.image(str(path), w=min(pdf.epw * 0.85, 150), x=pdf.l_margin + (pdf.epw - min(pdf.epw * 0.85, 150)) / 2)
    pdf.ln(1)


def main():
    pdf = GuidePDF()
    pdf.set_margins(18, 14, 18)
    pdf.set_auto_page_break(True, margin=16)
    add_fonts(pdf)
    pdf.add_page()

    lines = SRC.read_text(encoding="utf-8").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        if m := re.match(r"^(#{1,3})\s+(.*)", line):
            heading(pdf, len(m.group(1)), m.group(2))
        elif line.strip() == "---":
            pdf.ln(1)
            pdf.set_draw_color(180)
            pdf.line(pdf.l_margin, pdf.get_y(), pdf.w - pdf.r_margin, pdf.get_y())
            pdf.ln(2)
        elif line.lstrip().startswith("```"):
            block = []
            i += 1
            while i < len(lines) and not lines[i].lstrip().startswith("```"):
                block.append(lines[i])
                i += 1
            code_block(pdf, block)
        elif line.strip().startswith("|"):
            block = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                block.append(lines[i])
                i += 1
            i -= 1
            table_block(pdf, block)
        elif m := re.match(r"^!\[([^\]]*)\]\(([^)]+)\)", line.strip()):
            image(pdf, m.group(1), m.group(2))
        elif line.strip():
            buf = [line.strip()]
            while i + 1 < len(lines) and lines[i + 1].strip() and not re.match(
                r"^(#{1,3} |```|\||!|\- |---$)", lines[i + 1].strip()
            ) and not lines[i + 1].lstrip().startswith("```"):
                i += 1
                buf.append(lines[i].strip())
            text = " ".join(buf)
            if text.startswith("- "):
                text = "•  " + "\n•  ".join(t[2:] for t in buf)
            # fpdf2 markdown=True handles **bold** and [links] only — strip the
            # italics/code markers it leaves literal. The (?<!\*)...(?!\*)
            # boundaries keep **bold** intact while removing *italic* markers.
            text = re.sub(r"`([^`]+)`", r"\1", text)
            text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"\1", text)
            paragraph(pdf, text)

        i += 1

    pdf.output(str(OUT))
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {pdf.pages_count} pages)")


if __name__ == "__main__":
    sys.exit(main())
