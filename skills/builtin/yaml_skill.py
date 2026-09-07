"""YAML parser skill."""
import yaml

SKILL = {
    "name": "YAML",
    "description": "Parse YAML files into structured records",
    "extensions": [".yaml", ".yml"],
    "mime_types": ["application/x-yaml", "text/yaml"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = data.decode("utf-8", errors="replace")

    docs = list(yaml.safe_load_all(text))
    records = []
    for doc in docs:
        if isinstance(doc, dict):
            records.append(doc)
        elif doc is not None:
            records.append({"value": doc})

    fields = list(records[0].keys()) if records and isinstance(records[0], dict) else []
    return {"records": records, "fields": fields, "metadata": {"doc_count": len(records), "format": "yaml"}}
