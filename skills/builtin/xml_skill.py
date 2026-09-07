"""XML parser skill."""
import xml.etree.ElementTree as ET

SKILL = {
    "name": "XML",
    "description": "Parse XML files into structured records",
    "extensions": [".xml"],
    "mime_types": ["application/xml", "text/xml"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        tree = ET.parse(file_path)
    else:
        import io
        tree = ET.parse(io.BytesIO(data))
    root = tree.getroot()

    def elem_to_dict(elem):
        d = {"tag": elem.tag}
        if elem.text and elem.text.strip():
            d["text"] = elem.text.strip()
        if elem.attrib:
            d["attrs"] = dict(elem.attrib)
        children = [elem_to_dict(child) for child in elem]
        if children:
            d["children"] = children
        return d

    records = [elem_to_dict(root)]
    return {"records": records, "fields": ["tag", "text"], "metadata": {"root_tag": root.tag, "format": "xml"}}
