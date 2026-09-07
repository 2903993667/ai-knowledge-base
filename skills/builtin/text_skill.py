"""Plain text parser skill."""

SKILL = {
    "name": "Text",
    "description": "Parse plain text files",
    "extensions": [".txt", ".log", ".csv"],
    "mime_types": ["text/plain"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = data.decode("utf-8", errors="replace")

    lines = text.split("\n")
    records = [{"line_no": i + 1, "text": line} for i, line in enumerate(lines) if line.strip()]
    return {"records": records, "fields": ["line_no", "text"], "metadata": {"line_count": len(records), "format": "text"}}
