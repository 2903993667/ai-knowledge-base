"""File import pipeline — detect format, parse, AI-organize to Markdown, store."""

import os
import hashlib
import json
import mimetypes
from pathlib import Path
from datetime import datetime, timezone

from . import database as db
from . import skill_loader
from . import ai
from . import logger

UPLOAD_DIR = Path(__file__).resolve().parent.parent / "data" / "uploads"

# File signatures for format detection (magic bytes)
MAGIC_BYTES = {
    b"\x89PNG": "png",
    b"\xff\xd8\xff": "jpg",
    b"%PDF": "pdf",
    b"PK\x03\x04": "zip",
    b"\xd0\xcf\x11\xe0": "ole",  # .xls/.doc
    b"SQLite format 3": "sqlite",
    b"RIFF": "wav",
    b"\x1f\x8b": "gzip",
    b"\x7fELF": "elf",
}

EXT_MAP = {
    ".csv": "csv", ".tsv": "csv", ".json": "json", ".jsonl": "jsonl",
    ".txt": "text", ".md": "markdown", ".html": "html", ".htm": "html",
    ".xml": "xml", ".yaml": "yaml", ".yml": "yaml",
    ".xlsx": "excel", ".xls": "excel",
    ".docx": "docx", ".doc": "docx",
    ".pdf": "pdf", ".png": "image", ".jpg": "image", ".jpeg": "image",
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".sql": "sql", ".db": "sqlite", ".sqlite": "sqlite",
}


def detect_format(file_path):
    """Detect file format from extension + magic bytes."""
    path = Path(file_path)
    ext = path.suffix.lower()
    fmt = EXT_MAP.get(ext)
    if fmt:
        return fmt, ext
    try:
        with open(file_path, "rb") as f:
            header = f.read(16)
        for sig, fmt_name in MAGIC_BYTES.items():
            if header.startswith(sig):
                return fmt_name, ext
    except Exception:
        pass
    return "unknown", ext


def _sha256(file_path):
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


async def import_file(file_path, title=None):
    """Full import pipeline for a single file."""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

    # Copy to uploads if not already there
    import shutil
    dest = UPLOAD_DIR / path.name
    if path.resolve() != dest.resolve():
        shutil.copy2(str(path), str(dest))
    file_path = str(dest)

    size = os.path.getsize(file_path)
    sha = _sha256(file_path)
    fmt, ext = detect_format(file_path)

    # Create DB record
    doc = db.create_document(path.name, fmt, file_path, size)
    doc_id = doc["id"]
    db.update_document(doc_id, source_sha256=sha)

    task = db.create_import_task(doc_id)
    task_id = task["id"]

    try:
        # Step 1: Find matching skill
        db.update_import_task(task_id, stage="detecting_format", progress=10, status="running")
        logger.info("importer", f"Starting import: {path.name} (format: {fmt})")
        skill_info = db.get_skill_by_extension(ext)

        if skill_info:
            # Step 2: Parse with existing skill
            db.update_import_task(task_id, stage="parsing", progress=30)
            logger.info("importer", f"Parsing with skill: {skill_info['name']}")
            parsed = await skill_loader.parse_with_skill(skill_info, file_path)
            db.update_document(doc_id, skill_id=skill_info["id"], format_family=fmt)
        else:
            # Step 3: Try to learn the format using AI
            db.update_import_task(task_id, stage="learning_format", progress=20)
            parsed = await _learn_and_parse(file_path, fmt, ext, doc_id, task_id)

        # Step 4: AI organizes content to Markdown
        db.update_import_task(task_id, stage="ai_organizing", progress=60)
        logger.info("importer", f"Converting to Markdown...")
        md_content = await _organize_to_markdown(parsed, path.name, fmt)

        # Step 5: Store content & create chunks
        db.update_import_task(task_id, stage="chunking", progress=80)
        logger.info("importer", f"Chunking content...")
        chunks = _chunk_markdown(md_content)
        db.update_document(doc_id, md_content=md_content, title=title or path.name, status="ready")
        db.insert_chunks(doc_id, chunks)

        # Step 6: Embed if configured
        db.update_import_task(task_id, stage="embedding", progress=90)
        try:
            await _embed_chunks(doc_id, chunks)
        except Exception:
            pass  # Embedding is optional

        db.update_import_task(task_id, stage="done", progress=100, status="done", result=doc_id)
        logger.info("importer", f"Import complete: {title or path.name} ({len(chunks)} chunks)")
        return {"doc_id": doc_id, "title": title or path.name, "status": "ready", "chunks": len(chunks)}

    except Exception as e:
        db.update_import_task(task_id, status="error", error=str(e))
        logger.error("importer", f"Import failed: {str(e)}")
        db.update_document(doc_id, status="error", meta_json=json.dumps({"error": str(e)}))
        raise


async def _learn_and_parse(file_path, fmt, ext, doc_id, task_id):
    """Use AI + web search to learn how to parse an unknown format."""
    # Read first few KB for analysis
    preview = ""
    try:
        with open(file_path, "rb") as f:
            raw = f.read(4096)
            try:
                preview = raw.decode("utf-8", errors="replace")[:2000]
            except Exception:
                preview = raw[:500].hex()
    except Exception:
        pass

    # AI generates a parse strategy
    system_prompt = """You are a data format expert. Given a file preview and its format hint,
generate a Python async function called `parse` that reads this file type and returns
a dict with keys: 'records' (list of dicts), 'fields' (list of field names), 'metadata' (dict).
The function takes a parameter `file_path` (str).
Use standard library + openpyxl + pypdf + python-docx + pyyaml + beautifulsoup4 + lxml.
Also provide: SKILL dict with name, description, extensions, mime_types.
Return ONLY valid Python code, no markdown."""

    # Try web search for format info
    search_results = ""
    try:
        results = await ai.web_search(f"how to parse {ext or fmt} file format in Python programmatically", max_results=3)
        search_results = "\n".join([f"- {r['title']}: {r['snippet']}" for r in results])
    except Exception:
        pass

    user_msg = f"""File format: {fmt} (extension: {ext})
Preview (first 2000 chars):
{preview}

Search results for parsing this format:
{search_results if search_results else '(no search results available)'}

Generate a Python parser for this format. Return only executable Python code."""

    response = await ai.chat_complete(
        [{"role": "user", "content": user_msg}],
        system=system_prompt,
        temperature=0.2,
        max_tokens=3000,
    )

    # Clean response: extract Python code
    code = response
    if "```python" in code:
        code = code.split("```python")[1].split("```")[0]
    elif "```" in code:
        code = code.split("```")[1].split("```")[0]

    # Save as learned skill
    skill_name = f"learned_{fmt}_{ext}".replace(".", "_")
    skill_path = skill_loader.save_learned_skill(code, skill_name, [ext] if ext else [])

    # Register in DB
    import uuid
    skill_id = f"learned_{skill_name}"
    db.register_skill(skill_id, skill_name, f"AI-learned parser for {fmt}", "learned", skill_path,
                      [ext] if ext else [], [])

    # Reload and parse
    skills = skill_loader.discover_skills()
    for s in skills:
        if s["id"] == skill_id:
            return await skill_loader.parse_with_skill(s, file_path)

    raise ValueError(f"Failed to load learned skill for {fmt}")


async def _organize_to_markdown(parsed, filename, fmt):
    """Convert parsed data to Markdown. Uses AI for small files, direct conversion for large ones."""
    if not parsed:
        return f"# {filename}\n\n*No content could be extracted.*"

    records = parsed.get("records", [])
    fields = parsed.get("fields", [])
    metadata = parsed.get("metadata", {})

    # Estimate total text size
    total_chars = sum(len(json.dumps(r, ensure_ascii=False, default=str)) for r in records)

    # For large files (>15000 chars), skip AI and convert directly to preserve ALL content
    if total_chars > 15000:
        logger.info("importer", f"Large file detected ({total_chars} chars), using direct conversion for: {filename}")
        return _direct_to_markdown(parsed, filename, fmt)

    # For small files, use AI to organize nicely
    preview = json.dumps(parsed, ensure_ascii=False, default=str)[:8000]
    logger.info("importer", f"Using AI to organize {total_chars} chars for: {filename}")


    system_prompt = """Organize the following parsed data into clean, well-structured Markdown.
Use appropriate headings, tables, and lists. Preserve all important information.
Return only valid Markdown, no code blocks."""

    try:
        response = await ai.chat_complete(
            [{"role": "user", "content": f"Filename: {filename}\nFormat: {fmt}\n\nParsed data:\n{preview}"}],
            system=system_prompt,
            temperature=0.3,
            max_tokens=16000,
        )
        return response
    except Exception as e:
        logger.warn("importer", f"AI organize failed, using direct conversion: {e}")
        return _direct_to_markdown(parsed, filename, fmt)


def _direct_to_markdown(parsed, filename, fmt):
    """Convert parsed data directly to Markdown without AI, preserving all content."""
    records = parsed.get("records", [])
    fields = parsed.get("fields", [])
    metadata = parsed.get("metadata", {})
    images = parsed.get("images", [])

    # Build a map of page -> images
    page_images = {}
    for img in images:
        p = img.get("page", 0)
        page_images.setdefault(p, []).append(img)

    lines = ["# %s" % filename, ""]

    if not records:
        return "\n".join(lines) + "\n*No records found.*"

    # If records have a "text" field (e.g., PDF pages), render as sections with images
    if "text" in fields and len(records) > 1:
        for rec in records:
            page_num = rec.get("page", "")
            text = rec.get("text", "").strip()
            if not text and page_num not in page_images:
                continue
            heading = "Page %s" % page_num if page_num else "Section"
            lines.append("## %s" % heading)
            lines.append("")
            if text:
                lines.append(text)
                lines.append("")
            # Insert images for this page
            p_imgs = page_images.get(page_num, [])
            for img in p_imgs:
                rel = img.get("relative_path", "")
                w = img.get("width", 0)
                h = img.get("height", 0)
                lines.append("![Page %s image](/%s)" % (page_num, rel))
                lines.append("*%dx%d*" % (w, h))
                lines.append("")

    # If records have structured fields, render as table + details
    elif fields and len(records) > 0:
        lines.append("## Records")
        lines.append("")
        lines.append("| " + " | ".join(str(f) for f in fields) + " |")
        lines.append("|" + "|".join(["---"] * len(fields)) + "|")
        for rec in records:
            row = [str(rec.get(f, "")).replace("|", "¦").replace("\n", " ") for f in fields]
            lines.append("| " + " | ".join(row) + " |")
        lines.append("")
        if len(records) <= 200:
            lines.append("## Details")
            lines.append("")
            for i, rec in enumerate(records):
                lines.append("### Record %d" % (i + 1))
                lines.append("")
                for f in fields:
                    val = rec.get(f, "")
                    if val:
                        lines.append("- **%s**: %s" % (f, val))
                lines.append("")
    else:
        lines.append("## Content")
        lines.append("")
        for rec in records:
            text = json.dumps(rec, ensure_ascii=False, default=str)
            lines.append(text)
            lines.append("")

    # Append any standalone images (not attached to a page)
    standalone = [img for img in images if img.get("page", 0) == 0]
    if standalone:
        lines.append("## Images")
        lines.append("")
        for img in standalone:
            rel = img.get("relative_path", "")
            lines.append("![image](/%s)" % rel)
            lines.append("")

    # Append metadata if present
    if metadata:
        lines.append("## Metadata")
        lines.append("")
        for k, v in metadata.items():
            lines.append("- **%s**: %s" % (k, v))
        lines.append("")

    return "\n".join(lines)



def _chunk_markdown(md_content, chunk_size=800, chunk_overlap=200):
    """Split Markdown into chunks with semantic awareness and overlap.
    
    Uses paragraph-aware splitting with configurable overlap to prevent
    key terms from being split across chunk boundaries.
    """
    import re
    
    chunks = []
    current_heading = ""
    
    # First, split by headings to preserve document structure
    sections = []
    current_section_lines = []
    current_section_heading = ""
    
    for line in md_content.split("\n"):
        if line.startswith("#"):
            # Save previous section
            if current_section_lines:
                sections.append({
                    "heading": current_section_heading,
                    "text": "\n".join(current_section_lines)
                })
            current_section_heading = line.lstrip("#").strip()
            current_section_lines = [line]
        else:
            current_section_lines.append(line)
    
    # Don't forget the last section
    if current_section_lines:
        sections.append({
            "heading": current_section_heading,
            "text": "\n".join(current_section_lines)
        })
    
    # If no sections found, treat entire content as one section
    if not sections:
        sections = [{"heading": "", "text": md_content}]
    
    # Now split each section into chunks with overlap
    for section in sections:
        text = section["text"]
        heading = section["heading"]
        
        if len(text) <= chunk_size:
            # Section fits in one chunk
            if text.strip():
                chunks.append({"heading": heading, "content": text.strip()})
            continue
        
        # Split by paragraphs first (double newlines)
        paragraphs = re.split(r'(?:\n\n|\n(?=\n))', text)
        
        current_chunk_parts = []
        current_chunk_len = 0
        
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            
            para_len = len(para)
            
            # If adding this paragraph exceeds chunk_size, save current chunk
            if current_chunk_len + para_len > chunk_size and current_chunk_parts:
                chunk_text = "\n\n".join(current_chunk_parts)
                if chunk_text.strip():
                    chunks.append({"heading": heading, "content": chunk_text.strip()})
                
                # Keep overlap: keep last part of current chunk
                overlap_text = ""
                for part in reversed(current_chunk_parts):
                    if len(overlap_text) + len(part) <= chunk_overlap:
                        overlap_text = part + "\n\n" + overlap_text
                    else:
                        # Take the end of the last part for overlap
                        overlap_text = part[-chunk_overlap:] + "\n\n" + overlap_text
                        break
                
                current_chunk_parts = [overlap_text.strip()] if overlap_text.strip() else []
                current_chunk_len = len(overlap_text)
            
            # If single paragraph is too long, split it by sentences
            if para_len > chunk_size:
                sentences = re.split(r'(?<=[.!?])\s+', para)
                for sent in sentences:
                    if current_chunk_len + len(sent) > chunk_size and current_chunk_parts:
                        chunk_text = " ".join(current_chunk_parts)
                        if chunk_text.strip():
                            chunks.append({"heading": heading, "content": chunk_text.strip()})
                        # Overlap
                        overlap = current_chunk_parts[-1] if current_chunk_parts else ""
                        current_chunk_parts = [overlap] if overlap else []
                        current_chunk_len = len(overlap)
                    current_chunk_parts.append(sent)
                    current_chunk_len += len(sent)
            else:
                current_chunk_parts.append(para)
                current_chunk_len += para_len
        
        # Save remaining parts
        if current_chunk_parts:
            chunk_text = "\n\n".join(current_chunk_parts)
            if chunk_text.strip():
                chunks.append({"heading": heading, "content": chunk_text.strip()})
    
    if not chunks:
        chunks = [{"heading": "", "content": md_content[:chunk_size]}]

    # Split oversized chunks (safety net)
    final = []
    for ch in chunks:
        if len(ch["content"]) > chunk_size * 1.5:
            parts = [ch["content"][i:i+chunk_size] for i in range(0, len(ch["content"]), chunk_size)]
            for i, part in enumerate(parts):
                final.append({"heading": "%s (part %d)" % (ch["heading"], i+1) if ch["heading"] else "Part %d" % (i+1), "content": part})
        else:
            final.append(ch)

    for i, ch in enumerate(final):
        ch["seq"] = i
    return final




async def _embed_chunks(doc_id, chunks):
    """Generate embeddings for all chunks of a document."""
    texts = [ch["content"] for ch in chunks]
    embeddings = await ai.get_embeddings_batch(texts)
    conn = db.get_conn()
    for ch, emb in zip(chunks, embeddings):
        if emb is not None:
            blob = ai.serialize_embedding(emb)
            conn.execute("UPDATE chunks SET embedding=? WHERE doc_id=? AND content=?", (blob, doc_id, ch["content"]))
    conn.commit()
    conn.close()
