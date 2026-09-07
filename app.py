"""AI-Driven Knowledge Base — FastAPI main application."""

import os
import asyncio
import shutil
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles

from core import database as db
from core import ai
from core import search
from core import chat as rag_chat
from core import importer
from core import skill_loader
from core import logger

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app):
    db.init_db()
    skills = skill_loader.discover_skills()
    for s in skills:
        db.register_skill(s["id"], s["name"], s.get("description", ""), s["source"],
                          s["path"], s.get("extensions", []), s.get("mime_types", []))
    print(f"Loaded {len(skills)} skills")
    yield

app = FastAPI(title="AI Knowledge Base", version="1.0.0", lifespan=lifespan)

STATIC_DIR = Path(__file__).parent / "static"



# ─── Frontend ────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


# ─── Documents ───────────────────────────────────────────────────

@app.post("/api/documents/upload")
async def upload_document(file: UploadFile = File(...), title: Optional[str] = Form(None)):
    upload_dir = Path("data/uploads")
    upload_dir.mkdir(parents=True, exist_ok=True)
    dest = upload_dir / file.filename
    with open(dest, "wb") as f:
        content = await file.read()
        f.write(content)
    try:
        result = await importer.import_file(str(dest), title=title)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/documents")
async def list_documents(limit: int = 100, offset: int = 0):
    docs = db.list_documents(limit=limit, offset=offset)
    total = db.count_documents()
    return {"documents": docs, "total": total}


@app.get("/api/documents/{doc_id}")
async def get_document(doc_id: str):
    try:
        doc = db.get_document(doc_id)
        if not doc:
            raise HTTPException(status_code=404, detail="Document not found")
        chunks = db.get_chunks_for_doc(doc_id)
        # Remove embedding binary data - not needed for display and causes serialization errors
        clean_chunks = [{k: v for k, v in c.items() if k != "embedding"} for c in chunks]
        return {"document": doc, "chunks": clean_chunks}
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    db.delete_document(doc_id)
    return {"ok": True}


# ─── Search ──────────────────────────────────────────────────────

@app.get("/api/search")
async def search_documents(q: str, mode: str = "keyword", limit: int = 20):
    try:
        results = await search.search(q, mode=mode, limit=limit)
        grouped = search.group_results_by_doc(results)
        return {"results": results, "grouped": grouped, "count": len(results), "doc_count": len(grouped)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ─── Chat ────────────────────────────────────────────────────────

@app.post("/api/chats")
async def create_chat(title: str = "New Chat"):
    return db.create_chat(title)


@app.get("/api/chats")
async def list_chats():
    return db.list_chats()


@app.get("/api/chats/{chat_id}")
async def get_chat(chat_id: str):
    chat = db.get_chat(chat_id)
    if not chat:
        raise HTTPException(status_code=404, detail="Chat not found")
    messages = db.get_messages(chat_id)
    return {"chat": chat, "messages": messages}


@app.delete("/api/chats/{chat_id}")
async def delete_chat(chat_id: str):
    db.delete_chat(chat_id)
    return {"ok": True}


@app.post("/api/chats/{chat_id}/messages")
async def send_message(chat_id: str, request: Request):
    body = await request.json()
    message = body.get("message", "")
    use_rag = body.get("use_rag", True)
    search_mode = body.get("search_mode", "keyword")
    web_search = body.get("web_search", False)
    stream = body.get("stream", False)

    if stream:
        async def event_stream():
            async for chunk in rag_chat.chat_stream(chat_id, message, use_rag=use_rag, search_mode=search_mode, web_search=web_search):
                yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
        return StreamingResponse(event_stream(), media_type="text/event-stream")

    result = await rag_chat.chat(chat_id, message, use_rag=use_rag, search_mode=search_mode, web_search=web_search)
    return result


# ─── Settings ────────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    return db.get_settings()


@app.post("/api/settings")
async def update_settings(request: Request):
    body = await request.json()
    db.set_settings(body)
    return {"ok": True}


@app.post("/api/settings/test-ai")
async def test_ai_connection(request: Request):
    try:
        result = await ai.chat_complete(
            [{"role": "user", "content": "Say 'connection OK' in 5 words or less."}],
            temperature=0.1, max_tokens=50
        )
        return {"ok": True, "response": result}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/test-embedding")
async def test_embedding():
    try:
        emb = await ai.get_embedding("test embedding")
        if emb:
            return {"ok": True, "dimensions": len(emb)}
        return {"ok": False, "error": "Embedding model not configured"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/test-reranker")
async def test_reranker():
    try:
        test_docs = ["Cas9 requires PAM for DNA cleavage", "The weather is nice today", "Single-stranded DNA cleavage is unaffected by PAM mutations"]
        indices = await ai.rerank("PAM sequence Cas9", test_docs, top_n=3)
        if indices and indices[0] == 0:
            return {"ok": True, "message": "Reranker working correctly"}
        return {"ok": True, "message": f"Reranker returned: {indices}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@app.post("/api/settings/test-search")
async def test_search():
    try:
        results = await ai.web_search("test", max_results=2)
        return {"ok": True, "results": results}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ─── Skills ──────────────────────────────────────────────────────

@app.get("/api/skills")
async def list_skills():
    return db.list_skills(include_inactive=True)


@app.post("/api/skills/refresh")
async def refresh_skills():
    skills = skill_loader.discover_skills()
    for s in skills:
        db.register_skill(s["id"], s["name"], s.get("description", ""), s["source"],
                          s["path"], s.get("extensions", []), s.get("mime_types", []))
    return {"count": len(skills)}





# ─── Images ──────────────────────────────────────────────────────

@app.get("/api/images/{path:path}")
async def serve_image(path: str):
    import os
    img_path = Path("data/images") / path
    if img_path.exists():
        return FileResponse(str(img_path), media_type="image/png")
    raise HTTPException(status_code=404, detail="Image not found")


# ─── Logs (SSE) ──────────────────────────────────────────────────

@app.get("/api/logs")
async def get_logs(limit: int = 200):
    return {"logs": logger.get_recent(limit)}


@app.get("/api/logs/stream")
async def stream_logs():
    queue, _ = logger.subscribe()

    async def event_stream():
        # Send recent history first
        for entry in logger.get_recent(50):
            yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
        # Then stream live
        while True:
            try:
                entry = await asyncio.wait_for(queue.get(), timeout=30)
                yield f"data: {json.dumps(entry, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield f": keepalive\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")

# ─── Stats ───────────────────────────────────────────────────────

@app.get("/api/stats")
async def get_stats():
    return {
        "documents": db.count_documents(),
        "skills": len(db.list_skills()),
        "chats": len(db.list_chats()),
    }


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

@app.post("/api/settings/fetch-models")
async def fetch_models(request: Request):
    body = await request.json()
    base_url = body.get("base_url", "")
    api_key = body.get("api_key", "")
    if not base_url:
        return {"ok": False, "error": "请填写 API Base URL"}
    try:
        models = await ai.fetch_models(base_url, api_key)
        return {"ok": True, "models": models}
    except Exception as e:
        return {"ok": False, "error": str(e)}