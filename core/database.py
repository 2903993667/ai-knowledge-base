"""SQLite database layer."""

import sqlite3, json, uuid
from datetime import datetime, timezone
from pathlib import Path

def _get_base_dir():
    if getattr(__import__('sys'), 'frozen', False):
        return Path(__import__('sys').executable).resolve().parent
    return Path(__file__).resolve().parent.parent

_base = _get_base_dir()
DB_DIR = _base / "data"
DB_PATH = DB_DIR / "database.sqlite"

def _now(): return datetime.now(timezone.utc).isoformat()
def _uid(): return uuid.uuid4().hex

def get_conn():
    DB_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn

def init_db():
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    file_type TEXT NOT NULL DEFAULT '',
    format_family TEXT NOT NULL DEFAULT '',
    skill_id TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    source_path TEXT NOT NULL DEFAULT '',
    source_size INTEGER NOT NULL DEFAULT 0,
    source_sha256 TEXT NOT NULL DEFAULT '',
    md_content TEXT NOT NULL DEFAULT '',
    meta_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chunks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    seq INTEGER NOT NULL DEFAULT 0,
    heading TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    embedding BLOB,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON chunks(doc_id);
CREATE TABLE IF NOT EXISTS skills (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL DEFAULT 'builtin',
    path TEXT NOT NULL DEFAULT '',
    extensions TEXT NOT NULL DEFAULT '[]',
    mime_types TEXT NOT NULL DEFAULT '[]',
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS chats (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL DEFAULT 'New Chat',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    chat_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    sources TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id);
CREATE TABLE IF NOT EXISTS import_tasks (
    id TEXT PRIMARY KEY,
    doc_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    stage TEXT NOT NULL DEFAULT '',
    progress INTEGER NOT NULL DEFAULT 0,
    total INTEGER NOT NULL DEFAULT 100,
    error TEXT NOT NULL DEFAULT '',
    result TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(doc_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    content, heading, doc_id UNINDEXED, doc_title UNINDEXED, seq UNINDEXED, chunk_id UNINDEXED
);
"""

def _rl(rows): return [dict(r) for r in rows]

def db_fetch_all(sql, params=()):
    conn = get_conn(); rows = conn.execute(sql, params).fetchall(); conn.close()
    return _rl(rows)

def db_fetch_one(sql, params=()):
    conn = get_conn(); row = conn.execute(sql, params).fetchone(); conn.close()
    return dict(row) if row else None

def db_execute(sql, params=()):
    conn = get_conn(); cur = conn.execute(sql, params); conn.commit(); conn.close()
    return cur

def db_executescript(script):
    conn = get_conn(); conn.executescript(script); conn.commit(); conn.close()

def get_settings():
    return {r["key"]: r["value"] for r in db_fetch_all("SELECT key, value FROM settings")}

def get_setting(key, default=""):
    row = db_fetch_one("SELECT value FROM settings WHERE key=?", (key,))
    return row["value"] if row else default

def set_setting(key, value):
    now = _now()
    db_execute("INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at", (key, value, now))

def set_settings(d):
    for k, v in d.items(): set_setting(k, v)

def create_document(filename, file_type, source_path, size):
    doc_id = _uid(); now = _now()
    db_execute("INSERT INTO documents(id,filename,file_type,source_path,source_size,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (doc_id, filename, file_type, source_path, size, "pending", now, now))
    return {"id": doc_id, "filename": filename}

def update_document(doc_id, **kw):
    if not kw: return
    kw["updated_at"] = _now()
    s = ", ".join(f"{k}=?" for k in kw)
    db_execute(f"UPDATE documents SET {s} WHERE id=?", list(kw.values()) + [doc_id])

def get_document(doc_id):
    return db_fetch_one("SELECT * FROM documents WHERE id=?", (doc_id,))

def list_documents(limit=100, offset=0):
    return db_fetch_all("SELECT id,title,filename,file_type,format_family,status,source_size,created_at,updated_at FROM documents ORDER BY created_at DESC LIMIT ? OFFSET ?", (limit, offset))

def count_documents():
    r = db_fetch_one("SELECT COUNT(*) as cnt FROM documents")
    return r["cnt"] if r else 0

def delete_document(doc_id):
    conn = get_conn()
    conn.execute("DELETE FROM chunks_fts WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    conn.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    conn.commit(); conn.close()

def insert_chunks(doc_id, chunks):
    conn = get_conn()
    doc = get_document(doc_id)
    doc_title = doc["title"] if doc else ""
    for i, ch in enumerate(chunks):
        cid = ch.get("id") or _uid()
        conn.execute("INSERT INTO chunks(id,doc_id,seq,heading,content) VALUES(?,?,?,?,?)",
            (cid, doc_id, ch.get("seq", i), ch.get("heading", ""), ch["content"]))
        conn.execute("INSERT INTO chunks_fts(chunk_id,doc_id,seq,heading,content,doc_title) VALUES(?,?,?,?,?,?)",
            (cid, doc_id, ch.get("seq", i), ch.get("heading", ""), ch["content"], doc_title))
    conn.commit(); conn.close()

def search_chunks_fts(query, limit=20):
    return db_fetch_all(
        "SELECT chunk_id as id, doc_id, seq, heading, content FROM chunks_fts "
        "WHERE chunks_fts MATCH ? ORDER BY rank LIMIT ?", (query, limit))

def get_chunks_for_doc(doc_id):
    return db_fetch_all("SELECT * FROM chunks WHERE doc_id=? ORDER BY seq", (doc_id,))

def get_chunks_with_embeddings(limit=5000):
    return db_fetch_all("SELECT id,doc_id,heading,content,embedding FROM chunks WHERE embedding IS NOT NULL LIMIT ?", (limit,))

def update_chunk_embedding(chunk_id, embedding):
    db_execute("UPDATE chunks SET embedding=? WHERE id=?", (embedding, chunk_id))

def list_skills(include_inactive=False):
    w = "" if include_inactive else "WHERE active=1"
    return db_fetch_all(f"SELECT * FROM skills {w} ORDER BY source,name")

def get_skill(skill_id):
    return db_fetch_one("SELECT * FROM skills WHERE id=?", (skill_id,))

def get_skill_by_extension(ext):
    for r in db_fetch_all("SELECT * FROM skills WHERE active=1"):
        exts = json.loads(r["extensions"]) if r["extensions"] else []
        if ext.lower() in [e.lower() for e in exts]: return r
    return None

def register_skill(skill_id, name, desc, source, path, extensions, mime_types):
    now = _now()
    db_execute("INSERT INTO skills(id,name,description,source,path,extensions,mime_types,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,description=excluded.description,path=excluded.path,extensions=excluded.extensions,mime_types=excluded.mime_types,updated_at=excluded.updated_at",
        (skill_id, name, desc, source, path, json.dumps(extensions), json.dumps(mime_types), now, now))

def create_chat(title="New Chat"):
    cid = _uid(); now = _now()
    db_execute("INSERT INTO chats(id,title,created_at,updated_at) VALUES(?,?,?,?)", (cid, title, now, now))
    return {"id": cid, "title": title}

def list_chats(limit=50):
    return db_fetch_all("SELECT id,title,created_at,updated_at FROM chats ORDER BY updated_at DESC LIMIT ?", (limit,))

def get_chat(chat_id):
    return db_fetch_one("SELECT * FROM chats WHERE id=?", (chat_id,))

def delete_chat(chat_id):
    db_execute("DELETE FROM messages WHERE chat_id=?", (chat_id,))
    db_execute("DELETE FROM chats WHERE id=?", (chat_id,))

def update_chat(chat_id, **kw):
    kw["updated_at"] = _now()
    s = ", ".join(f"{k}=?" for k in kw)
    db_execute(f"UPDATE chats SET {s} WHERE id=?", list(kw.values()) + [chat_id])

def append_message(chat_id, role, content, sources="[]"):
    mid = _uid(); now = _now()
    db_execute("INSERT INTO messages(id,chat_id,role,content,sources,created_at) VALUES(?,?,?,?,?,?)",
        (mid, chat_id, role, content, sources, now))
    db_execute("UPDATE chats SET updated_at=? WHERE id=?", (now, chat_id))
    return {"id": mid}

def get_messages(chat_id, limit=100):
    return db_fetch_all("SELECT * FROM messages WHERE chat_id=? ORDER BY created_at ASC LIMIT ?", (chat_id, limit))

def create_import_task(doc_id):
    tid = _uid(); now = _now()
    db_execute("INSERT INTO import_tasks(id,doc_id,status,created_at,updated_at) VALUES(?,?,?,?,?)", (tid, doc_id, "pending", now, now))
    return {"id": tid, "doc_id": doc_id}

def update_import_task(task_id, **kw):
    kw["updated_at"] = _now()
    s = ", ".join(f"{k}=?" for k in kw)
    db_execute(f"UPDATE import_tasks SET {s} WHERE id=?", list(kw.values()) + [task_id])

def get_import_task(task_id):
    return db_fetch_one("SELECT * FROM import_tasks WHERE id=?", (task_id,))

def reindex_fts():
    """Rebuild the FTS index from all existing chunks."""
    conn = get_conn()
    conn.execute("DELETE FROM chunks_fts")
    conn.execute("""
        INSERT INTO chunks_fts(chunk_id, doc_id, seq, heading, content, doc_title)
        SELECT c.id, c.doc_id, c.seq, c.heading, c.content, COALESCE(d.title, d.filename)
        FROM chunks c LEFT JOIN documents d ON c.doc_id = d.id
    """)
    conn.commit(); conn.close()

