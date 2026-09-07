"""HTML parser skill using BeautifulSoup."""
from bs4 import BeautifulSoup

SKILL = {
    "name": "HTML",
    "description": "Parse HTML files extracting text and structure",
    "extensions": [".html", ".htm"],
    "mime_types": ["text/html", "application/xhtml+xml"],
}

async def parse(file_path: str = "", data: bytes = None):
    if data is None:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            html = f.read()
    else:
        html = data.decode("utf-8", errors="replace")

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    title = soup.title.string if soup.title else ""
    headings = []
    records = []
    for h in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        headings.append(h.get_text(strip=True))

    text = soup.get_text(separator="\n", strip=True)
    return {"records": [{"title": title, "text": text}], "fields": ["title", "text"], "metadata": {"headings": headings, "format": "html"}}
