"""Executive Word exports are produced in memory; no client document temp files."""
from __future__ import annotations

import io
import re

from docx import Document
from docx.enum.section import WD_ORIENT, WD_SECTION_START
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Inches, Pt, RGBColor

from audit_engine import AuditResult, DISCLAIMER, FACT_FIELDS


def clean(value: object) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(value))


def _fill(cell, color: str) -> None:
    node = OxmlElement("w:shd")
    node.set(qn("w:fill"), color)
    cell._tc.get_or_add_tcPr().append(node)


def _table(doc, headers: list[str], rows: list[list[str]], widths: list[float]):
    table = doc.add_table(rows=1, cols=len(headers))
    table.autofit = False
    for col, width in zip(table.columns, widths):
        col.width = Inches(width)
    properties = table._tbl.tblPr
    borders = OxmlElement("w:tblBorders")
    for side in ("top", "left", "bottom", "right", "insideH", "insideV"):
        border = OxmlElement("w:" + side)
        for key, value in (("val", "single"), ("sz", "4"), ("color", "D9D9D9")):
            border.set(qn("w:" + key), value)
        borders.append(border)
    properties.append(borders)
    margins = OxmlElement("w:tblCellMar")
    for side in ("top", "left", "bottom", "right"):
        margin = OxmlElement("w:" + side)
        margin.set(qn("w:w"), "100")
        margin.set(qn("w:type"), "dxa")
        margins.append(margin)
    properties.append(margins)
    repeat = OxmlElement("w:tblHeader")
    table.rows[0]._tr.get_or_add_trPr().append(repeat)
    for cell, text, width in zip(table.rows[0].cells, headers, widths):
        cell.width = Inches(width)
        cell.text = text
        _fill(cell, "0F172A")
        for run in cell.paragraphs[0].runs:
            run.font.bold = True
            run.font.color.rgb = RGBColor.from_string("FFFFFF")
    for index, values in enumerate(rows):
        cells = table.add_row().cells
        for cell, text, width in zip(cells, values, widths):
            cell.width = Inches(width)
            cell.text = clean(text)
            cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            if index % 2 == 1:
                _fill(cell, "F1F5F9")
    for row in table.rows:
        for cell in row.cells:
            for paragraph in cell.paragraphs:
                paragraph.paragraph_format.space_after = Pt(3)
                paragraph.paragraph_format.space_before = Pt(2)
                paragraph.paragraph_format.line_spacing = 1.05
                for run in paragraph.runs:
                    run.font.size = Pt(9)
    doc.add_paragraph().paragraph_format.space_after = Pt(2)
    return table


def _citation(evidence) -> str:
    v = evidence.verification
    if v.verified:
        return f"Verified in Text (Page {v.page})" + ("" if v.exact else f" — approximate {v.score:.0%}; compare wording")
    return f"Unverified — Check Manually (model claimed page {evidence.claimed_page})"


def build_report(result: AuditResult) -> io.BytesIO:
    doc = Document()
    section = doc.sections[0]
    section.page_width, section.page_height = Inches(8.5), Inches(11)
    section.top_margin = section.bottom_margin = Inches(0.65)
    section.left_margin = section.right_margin = Inches(0.65)
    normal = doc.styles["Normal"]
    normal.font.name, normal.font.size = "Calibri", Pt(10)
    normal.paragraph_format.space_after = Pt(7)
    for name in ("Title", "Subtitle", "Heading 1", "Heading 2"):
        doc.styles[name].font.color.rgb = RGBColor(0, 0, 0)
    doc.styles["Title"].font.size = Pt(27)
    doc.styles["Heading 1"].font.size = Pt(15)
    header = section.header.paragraphs[0]
    header.text = "VERIPACT AI  |  PRIVILEGED REVIEW WORKSPACE"
    header.runs[0].font.size = Pt(8)
    footer = section.footer.paragraphs[0]
    footer.text = "Attorney review required  •  Local execution  |  Page "
    field = OxmlElement("w:fldSimple")
    field.set(qn("w:instr"), "PAGE")
    footer._p.append(field)
    for run in footer.runs:
        run.font.size = Pt(8)
    doc.core_properties.title = "Contract Audit Report"
    doc.core_properties.author = clean(result.reviewer)
    doc.core_properties.subject = "AI assisted contract review for independent attorney verification"
    doc.add_paragraph("Contract Audit Report", "Title")
    doc.add_paragraph(clean(result.firm), "Subtitle")
    counts = result.risk_counts
    status = "Review processing completed" if result.complete else "INCOMPLETE REVIEW"
    doc.add_paragraph(f"{status}. {len(result.risks)} potential deviations were identified: {counts['HIGH']} high, {counts['MEDIUM']} medium and {counts['LOW']} low. Counsel must verify the source evidence and approve any proposed language before use.")
    doc.add_heading("Firm Audit Certificate", 1)
    doc.add_paragraph("This record identifies the reviewed file and review settings. It does not certify enforceability, compliance, completeness or attorney approval.")
    _table(doc, ["Review metadata", "Recorded value"], [["Document", result.file_name], ["Firm and reviewer", f"{result.firm}\n{result.reviewer}"], ["UTC timestamp", result.timestamp], ["Model", result.model], ["Review ID", result.review_id], ["SHA 256 of original file", result.sha256], ["Page references", result.pagination]], [1.7, 5.5])
    if result.failures or result.warnings:
        doc.add_heading("Review Limitations", 1)
        for note in result.failures + result.warnings:
            doc.add_paragraph(clean(note))
    doc.add_heading("Structured Fact Sheet", 1)
    rows = []
    for name in FACT_FIELDS:
        items = [fact for fact in result.facts if fact.field == name]
        if not items:
            rows.append([name, "Not found — verify manually", "No supporting excerpt"])
        for fact in items:
            rows.append([name, fact.value, f"{fact.evidence.quote}\n{_citation(fact.evidence)}"])
    _table(doc, ["Fact", "Extracted value", "Source evidence"], rows, [1.25, 2.1, 3.85])
    doc.add_heading("Defined Terms Glossary", 1)
    if result.glossary:
        _table(doc, ["Defined term", "Extracted meaning", "Source evidence"], [[t.term, t.definition, f"{t.evidence.quote}\n{_citation(t.evidence)}"] for t in result.glossary], [1.25, 2.1, 3.85])
    else:
        doc.add_paragraph("No defined terms were extracted. Verify this against the original document.")
    landscape = doc.add_section(WD_SECTION_START.NEW_PAGE)
    landscape.orientation = WD_ORIENT.LANDSCAPE
    landscape.page_width, landscape.page_height = Inches(11), Inches(8.5)
    doc.add_heading("Risk and Suggested Redline Table", 1)
    doc.add_paragraph("Source matching confirms textual similarity only. It does not validate the model's legal interpretation. Approximate matches and all proposed redlines require attorney review.")
    if result.risks:
        rows = [[f"{r.severity}\nPage {r.evidence.page or '?'}", f"{r.evidence.quote}\n\n{_citation(r.evidence)}", r.trap_explanation, r.suggested_redline] for r in result.risks]
        table = _table(doc, ["Severity", "Verbatim quote and source check", "Trap explanation", "Suggested redline"], rows, [0.8, 3.0, 2.8, 3.1])
        colors = {"HIGH": ("FEE2E2", "991B1B"), "MEDIUM": ("FEF3C7", "92400E"), "LOW": ("DBEAFE", "1E40AF")}
        for row, risk in zip(table.rows[1:], result.risks):
            fill, ink = colors[risk.severity]
            _fill(row.cells[0], fill)
            for run in row.cells[0].paragraphs[0].runs:
                run.font.bold = True
                run.font.color.rgb = RGBColor.from_string(ink)
    else:
        doc.add_paragraph("No risks were returned by the model. This is not an assurance that the agreement complies with the playbook.")
    doc.add_heading("Firm Playbook Applied", 1)
    doc.add_paragraph(clean(result.playbook))
    doc.add_paragraph(DISCLAIMER)
    buffer = io.BytesIO()
    doc.save(buffer)
    buffer.seek(0)
    return buffer
