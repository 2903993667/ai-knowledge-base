"""DOCX parser skill using python-docx."""
from docx import Document

SKILL = {
    "name": "DOCX",
    "description": "Parse Word .docx files into structured content",
    "extensions": [".docx"],
    "mime_types": ["application/vnd.openxmlformats-officedocument.wordprocessingml.document"],
}

async def parse(file_path: str = "", data: bytes = None):
    doc = Document(file_path)
    records = []
    current_heading = ""

    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            continue
        style = para.style.name if para.style else ""
        if "Heading" in style:
            current_heading = text
        records.append({"heading": current_heading, "text": text, "style": style})

    # Also extract tables
    for i, table in enumerate(doc.tables):
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            records.append({"heading": f"Table {i+1}", "text": " | ".join(cells), "style": "table"})

    return {"records": records, "fields": ["heading", "text", "style"], "metadata": {"paragraph_count": len(records), "format": "docx"}}
