"""CSV / TSV parser skill."""
import csv, io, json

SKILL = {
    "name": "CSV/TSV",
    "description": "Parse CSV and TSV files into records",
    "extensions": [".csv", ".tsv"],
    "mime_types": ["text/csv", "text/tab-separated-values"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = data.decode("utf-8", errors="replace")

    sniffer = csv.Sniffer()
    try:
        dialect = sniffer.sniff(text[:4096])
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    records = []
    for row in reader:
        records.append(dict(row))

    fields = list(records[0].keys()) if records else []
    return {"records": records, "fields": fields, "metadata": {"row_count": len(records), "format": "csv"}}
