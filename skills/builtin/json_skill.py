"""JSON / JSONL parser skill."""
import json

SKILL = {
    "name": "JSON/JSONL",
    "description": "Parse JSON and JSONL files into records",
    "extensions": [".json", ".jsonl"],
    "mime_types": ["application/json"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = data.decode("utf-8", errors="replace")

    try:
        obj = json.loads(text)
        if isinstance(obj, list):
            records = [dict(item) if isinstance(item, dict) else {"value": item} for item in obj]
        elif isinstance(obj, dict):
            if "data" in obj and isinstance(obj["data"], list):
                records = [dict(item) if isinstance(item, dict) else {"value": item} for item in obj["data"]]
            else:
                records = [obj]
        else:
            records = [{"value": obj}]
    except json.JSONDecodeError:
        # JSONL fallback
        records = []
        for line in text.strip().split("\n"):
            line = line.strip()
            if line:
                try:
                    obj = json.loads(line)
                    records.append(dict(obj) if isinstance(obj, dict) else {"value": obj})
                except json.JSONDecodeError:
                    continue

    fields = list(records[0].keys()) if records else []
    return {"records": records, "fields": fields, "metadata": {"row_count": len(records), "format": "json"}}
