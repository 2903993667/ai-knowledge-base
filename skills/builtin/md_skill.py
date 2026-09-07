"""Markdown parser skill."""
import re

SKILL = {
    "name": "Markdown",
    "description": "Parse Markdown files preserving structure",
    "extensions": [".md", ".markdown"],
    "mime_types": ["text/markdown"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = data.decode("utf-8", errors="replace")

    headings = []
    current_heading = ""
    for line in text.split("\n"):
        m = re.match(r'^(#{1,6})\s+(.*)', line)
        if m:
            current_heading = m.group(2).strip()
            headings.append(current_heading)

    return {"records": [{"text": text}], "fields": ["text"], "metadata": {"headings": headings, "format": "markdown"}}
