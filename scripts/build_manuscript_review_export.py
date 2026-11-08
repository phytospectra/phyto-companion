#!/usr/bin/env python
"""Build the manuscript REVIEW export DOCX from the Markdown draft.

Handles headings, bold/italic inline markers, blockquotes, bullet/numbered
lists, pipe tables, and horizontal rules. Author/decaration placeholders are
preserved verbatim. Output is labeled NOT AUTHOR APPROVED. Run from the
phyto-companion checkout venv (python-docx is installed there).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

from docx import Document
from docx.oxml import OxmlElement

ROOT = Path(__file__).resolve().parents[1]  # phyto-companion
WORKSPACE = ROOT.parent
SOURCE = WORKSPACE / "phytospectra-paper" / "PHYTOSPECTRA-MANUSCRIPT-DRAFT1.md"
OUTDIR = WORKSPACE / "phytospectra-paper" / "review-export-2026-11-09"
OUT = OUTDIR / "PHYTOSPECTRA-MANUSCRIPT-REVIEW-NOT-AUTHOR-APPROVED.docx"

_INLINE = re.compile(r"(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)")


def add_runs(paragraph, text: str) -> None:
    for piece in _INLINE.split(text):
        if not piece:
            continue
        content = piece.replace("**", "").replace("*", "").replace("`", "")
        run = paragraph.add_run(content)
        if piece.startswith("**"):
            run.bold = True
        elif piece.startswith("*"):
            run.italic = True
        elif piece.startswith("`"):
            run.font.name = "Menlo"


def _row_properties(row, header: bool) -> None:
    properties = row._tr.get_or_add_trPr()
    keep = OxmlElement("w:cantSplit")
    properties.append(keep)
    if header:
        repeat = OxmlElement("w:tblHeader")
        properties.append(repeat)


def _render_table(doc: Document, rows: list[list[str]]) -> None:
    table = doc.add_table(rows=len(rows), cols=len(rows[0]))
    table.style = "Light Grid Accent 1"
    for row_index, cells in enumerate(rows):
        _row_properties(table.rows[row_index], header=(row_index == 0))
        for col_index, cell in enumerate(cells):
            add_runs(table.cell(row_index, col_index).paragraphs[0], cell)
    doc.add_paragraph()


def is_table_separator(line: str) -> bool:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return False
    cells = [cell.strip() for cell in stripped.strip("|").split("|")]
    return all(re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells)


def is_table_row(line: str) -> bool:
    return line.strip().startswith("|") and not is_table_separator(line)


def build() -> Path:
    OUTDIR.mkdir(exist_ok=True)
    doc = Document()
    doc.add_heading("PHYTOSPECTRA MANUSCRIPT — REVIEW EXPORT", level=0)
    banner = doc.add_paragraph()
    banner.add_run(
        "NOT AUTHOR APPROVED · NOT SUBMISSION READY · Generated 2026-11-09 from "
        "PHYTOSPECTRA-MANUSCRIPT-DRAFT1.md for internal review only. Author "
        "facts, declarations, data permissions and final approval remain pending."
    ).bold = True

    pending_table: list[list[str]] = []
    in_references = False
    lines = SOURCE.read_text().splitlines()
    index = 0
    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped or stripped == "---":
            index += 1
            continue
        if stripped.startswith("## ") and "References" in stripped:
            in_references = True
        if is_table_row(stripped):
            pending_table.append(
                [cell.strip() for cell in stripped.strip("|").split("|")]
            )
            index += 1
            continue
        if pending_table:
            rows = pending_table
            pending_table = []
            _render_table(doc, rows)
        if in_references and re.match(r"^\s*\d+\. ", stripped):
            # Preserve literal citation numbers: reference lists are not
            # renumbered by the document editor.
            add_runs(doc.add_paragraph(), stripped)
        elif stripped.startswith("# "):
            doc.add_heading(stripped[2:], level=1)
        elif stripped.startswith("## "):
            doc.add_heading(stripped[3:], level=2)
        elif stripped.startswith("### "):
            doc.add_heading(stripped[4:], level=3)
        elif stripped.startswith(">"):
            paragraph = doc.add_paragraph()
            add_runs(paragraph, stripped.lstrip("> ").strip())
            for run in paragraph.runs:
                if not run.bold:
                    run.italic = True
        elif re.match(r"^\s*[-*] ", stripped):
            paragraph = doc.add_paragraph(style="List Bullet")
            add_runs(paragraph, stripped[2:])
        elif re.match(r"^\s*\d+\. ", stripped):
            paragraph = doc.add_paragraph(style="List Number")
            add_runs(paragraph, re.sub(r"^\s*\d+\. ", "", stripped).strip())
        elif is_table_separator(stripped):
            pass  # separator consumed by the pending-table branch above
        else:
            add_runs(doc.add_paragraph(), stripped)
        index += 1

    if pending_table:
        _render_table(doc, pending_table)

    doc.save(OUT)

    back = Document(OUT)
    paragraphs = [p.text for p in back.paragraphs]
    assert any("NOT AUTHOR APPROVED" in text for text in paragraphs)
    raw_markers = sum(
        "**" in text or "*" in text or text.strip().startswith("|") or text.strip().startswith(">")
        for text in paragraphs
    )
    assert raw_markers == 0, f"{raw_markers} unrendered markdown artifacts remain"
    print(f"wrote {OUT} ({len(paragraphs)} paragraphs, "
          f"{len(back.tables)} tables, {OUT.stat().st_size} bytes)")
    return OUT


if __name__ == "__main__":
    sys.exit(0 if build() else 1)
