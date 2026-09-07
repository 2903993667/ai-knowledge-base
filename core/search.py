"""Search engine — FTS5 keyword + vector similarity + AI rerank."""

import json
import numpy as np
from . import database as db
from . import ai
from . import logger


def cosine_similarity(a, b):
    a, b = np.array(a, dtype=np.float32), np.array(b, dtype=np.float32)
    dot = np.dot(a, b)
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(dot / norm) if norm > 0 else 0.0


async def search(query, mode="keyword", limit=20, use_embedding=False):
    """
    Search documents. Modes:
      - keyword: FTS5 full-text search
      - semantic: vector similarity (requires embedding model)
      - hybrid: combine keyword + semantic via RRF
      - ai: AI-enhanced search (decompose query, expand, rerank)
    """
    if mode == "keyword":
        return await _keyword_search(query, limit)
    elif mode == "semantic":
        return await _semantic_search(query, limit)
    elif mode == "hybrid":
        return await _hybrid_search(query, limit)
    elif mode == "ai":
        return await _ai_search(query, limit)
    elif mode == "glossary":
        return await _glossary_search(query, limit)
    return await _keyword_search(query, limit)


async def _expand_query_with_ai(query, limit=8):
    """Use AI to expand query with English translations and related terms."""
    from . import ai
    import httpx
    
    # Check if query contains Chinese characters
    import re
    has_chinese = bool(re.search(r'[一-鿿]', query))
    
    if not has_chinese:
        # For English queries, just return as-is
        return [query]
    
    # Get AI config for direct API call (no context)
    cfg = ai.get_ai_config()
    if not cfg["chat_api_key"]:
        return [query]
    
    expand_prompt = f"""You are an academic search keyword expert. Convert Chinese academic queries to comprehensive English search terms.

Chinese query: {query}

Instructions:
1. Translate the query to English accurately, preserving ALL specific terms
2. Include synonyms and related technical terms
3. Include abbreviations and alternative spellings
4. Include terms that describe the same concept in different ways

Return a JSON array with 5-8 search terms. Example for "蛋白质折叠预测精度下降":
["protein folding prediction accuracy", "AlphaFold limitations", "structure prediction errors", "model confidence", "MSA depth", "cross-chain contacts", "multi-domain proteins"]

Return ONLY the JSON array, nothing else."""

    try:
        # Direct API call without chat history context
        url = f"{cfg['chat_base_url'].rstrip('/')}/chat/completions"
        headers = {"Authorization": f"Bearer {cfg['chat_api_key']}", "Content-Type": "application/json"}
        payload = {
            "model": cfg["chat_model"],
            "messages": [{"role": "user", "content": expand_prompt}],
            "temperature": 0.1,
            "max_tokens": 300
        }
        
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            response = resp.json()["choices"][0].get("message", {}).get("content", "")
            # Handle MiMo's reasoning_content
            if not response:
                response = resp.json()["choices"][0].get("message", {}).get("reasoning_content", "")
        
        # Parse JSON array from response
        import json
        match = re.search(r'\[.*\]', response, re.DOTALL)
        if match:
            terms = json.loads(match.group())
            logger.info("search", f"AI expanded query: {query} -> {terms[:5]}")
            return [query] + terms[:limit-1]
    except Exception as e:
        logger.warn("search", f"Query expansion failed: {e}")
    
    return [query]


async def _keyword_search(query, limit):
    import re
    
    # Extract hyphenated words that FTS5 can't handle properly
    hyphenated = re.findall(r'\w+(?:-\w+)+', query)
    
    # Use AI to expand Chinese queries to English
    search_terms = await _expand_query_with_ai(query)
    
    # Search with each expanded term
    rows = []
    seen = set()
    for term in search_terms:
        try:
            results = db.search_chunks_fts(term, limit=limit//2)
            for r in results:
                rid = r.get("id")
                if rid not in seen:
                    rows.append(r)
                    seen.add(rid)
        except:
            pass
    
    # For hyphenated words, supplement with LIKE search
    if hyphenated:
        like_rows = []
        for word in hyphenated:
            try:
                more = db.db_fetch_all(
                    "SELECT c.id, c.doc_id, c.seq, c.heading, c.content FROM chunks c "
                    "WHERE c.content LIKE ? OR c.content LIKE ? LIMIT ?",
                    (f"%{word}%", f"%{word.lower()}%", limit)
                )
                like_rows.extend(more)
            except:
                pass
        
        # Merge results, dedup by id
        seen = set(r.get("id") for r in rows)
        for r in like_rows:
            if r.get("id") not in seen:
                rows.append(r)
                seen.add(r.get("id"))
    
    if not rows:
        # Final fallback: full LIKE search
        rows = db.db_fetch_all(
            "SELECT c.id, c.doc_id, c.seq, c.heading, c.content FROM chunks c "
            "WHERE c.content LIKE ? OR c.heading LIKE ? ORDER BY c.seq LIMIT ?",
            (f"%{query}%", f"%{query}%", limit)
        )
    
    return [_enrich(r) for r in rows[:limit]]


async def _semantic_search(query, limit):
    query_emb = await ai.get_embedding(query)
    if query_emb is None:
        raise ValueError("Embedding model not configured. Please configure it in Settings.")

    all_chunks = db.get_chunks_with_embeddings(limit=10000)
    scored = []
    for ch in all_chunks:
        if ch["embedding"]:
            emb = ai.deserialize_embedding(ch["embedding"])
            sim = cosine_similarity(query_emb, emb)
            scored.append((sim, ch))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [_enrich(ch, score=sim) for sim, ch in scored[:limit]]


async def _hybrid_search(query, limit):
    keyword_results = await _keyword_search(query, limit=limit * 2)
    semantic_results = await _semantic_search(query, limit=limit * 2)

    # Reciprocal Rank Fusion
    k = 60
    scores = {}
    for rank, r in enumerate(keyword_results):
        key = r["id"]
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)
    for rank, r in enumerate(semantic_results):
        key = r["id"]
        scores[key] = scores.get(key, 0) + 1.0 / (k + rank + 1)

    all_map = {r["id"]: r for r in keyword_results + semantic_results}
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:limit]
    return [_enrich(all_map[cid], score=score) for cid, score in ranked]


async def _ai_search(query, limit):
    # Step 1: AI decomposes the query
    system = """You are a search assistant. Given a user query, generate:
1. 3-5 keyword variations for full-text search
2. A semantic search query
Return JSON: {"keywords": [...], "semantic_query": "..."}"""

    response = await ai.chat_complete(
        [{"role": "user", "content": query}],
        system=system, temperature=0.3, max_tokens=500
    )
    try:
        plan = json.loads(response)
    except Exception:
        plan = {"keywords": [query], "semantic_query": query}

    # Step 2: Multi-keyword search
    all_results = {}
    for kw in plan.get("keywords", [query]):
        try:
            results = await _keyword_search(kw, limit=limit)
            for r in results:
                all_results[r["id"]] = r
        except Exception:
            pass

    # Step 3: Semantic search
    sem_query = plan.get("semantic_query", query)
    try:
        sem_results = await _semantic_search(sem_query, limit=limit)
        for r in sem_results:
            all_results[r["id"]] = r
    except Exception:
        pass

    results_list = list(all_results.values())[:limit * 2]

    # Step 4: AI rerank top results
    if len(results_list) > 3:
        rerank_prompt = f"""Rank these search results by relevance to the query: "{query}"

Results:
{json.dumps([{"id": r["id"], "heading": r.get("heading",""), "preview": r["content"][:200]} for r in results_list[:20]], ensure_ascii=False)}

Return a JSON array of result IDs in order of relevance (most relevant first)."""

        rerank_response = await ai.chat_complete(
            [{"role": "user", "content": rerank_prompt}],
            temperature=0.1, max_tokens=500
        )
        try:
            ranked_ids = json.loads(rerank_response)
            if isinstance(ranked_ids, list):
                id_to_r = {r["id"]: r for r in results_list}
                results_list = [id_to_r[rid] for rid in ranked_ids if rid in id_to_r][:limit]
        except Exception:
            results_list = results_list[:limit]
    else:
        results_list = results_list[:limit]

    return [_enrich(r) for r in results_list]



def group_results_by_doc(results):
    """Group search results by document, collecting matching chunks per doc."""
    docs = {}
    for r in results:
        doc_key = r.get("doc_id") or r.get("chunk_id", "")
        if doc_key not in docs:
            docs[doc_key] = {
                "doc_id": r.get("doc_id", ""),
                "doc_title": r.get("doc_title", ""),
                "doc_filename": r.get("doc_filename", ""),
                "chunks": [],
                "chunk_count": 0,
            }
        docs[doc_key]["chunks"].append({
            "chunk_id": r.get("chunk_id", ""),
            "heading": r.get("heading", ""),
            "content": r.get("content", ""),
            "seq": r.get("seq", 0),
            "score": r.get("score"),
        })
        docs[doc_key]["chunk_count"] += 1
    return list(docs.values())

async def _glossary_search(query, limit):
    """Glossary/term search: AI defines the term + finds all mentions in the knowledge base."""
    # Step 1: Find all chunks mentioning the term
    try:
        mention_results = await _keyword_search(query, limit=limit * 2)
    except Exception:
        mention_results = []

    # Also try broader search
    if len(mention_results) < 2:
        try:
            more = await _keyword_search(query, limit=limit * 3)
            seen = set(r["chunk_id"] for r in mention_results)
            for r in more:
                if r["chunk_id"] not in seen:
                    mention_results.append(r)
                    seen.add(r["chunk_id"])
        except Exception:
            pass

    # Step 2: Build context from matching chunks for AI to define the term
    context_parts = []
    for r in mention_results[:8]:
        dt = r.get("doc_title", "")
        hd = r.get("heading", "")
        ct = r["content"][:500]
        context_parts.append("[" + dt + " / " + hd + "]\n" + ct)
    context_snippet = "\n---\n".join(context_parts) if context_parts else "(知识库中暂无相关内容)"

    # Step 3: Ask AI to define the term and explain its meaning in context
    system_prompt = (
        "你是一个词条解释助手。根据用户给出的术语/词条，完成以下任务：\n"
        "1. **词条定义**：用简洁清晰的语言解释这个术语的含义（2-4句话）。\n"
        "2. **关键要点**：列出该术语的3-5个核心要点。\n"
        "3. **知识库关联**：基于提供的知识库内容，说明该术语在相关文章中的具体语境和使用方式。\n\n"
        "如果知识库中没有相关内容，仅基于你的知识给出定义和要点，并说明该术语在当前知识库中暂未找到直接提及。\n\n"
        "请用以下JSON格式回答：\n"
        "{\n"
        '  "term": "术语名称",\n'
        '  "definition": "词条定义...",\n'
        '  "key_points": ["要点1", "要点2", ...],\n'
        '  "context_analysis": "在知识库中的语境分析..."\n'
        "}"
    )

    user_content = "术语/词条：" + query + "\n\n知识库相关内容：\n" + context_snippet

    try:
        ai_response = await ai.chat_complete(
            [{"role": "user", "content": user_content}],
            system=system_prompt,
            temperature=0.3,
            max_tokens=2000,
        )
    except Exception as e:
        ai_response = json.dumps({
            "term": query,
            "definition": "AI 服务暂时不可用: " + str(e),
            "key_points": [],
            "context_analysis": ""
        }, ensure_ascii=False)

    # Step 4: Parse AI response and build result
    try:
        glossary_data = json.loads(ai_response)
    except Exception:
        glossary_data = {
            "term": query,
            "definition": ai_response[:500],
            "key_points": [],
            "context_analysis": ""
        }

    mentions = []
    for r in mention_results[:limit]:
        mentions.append({
            "doc_title": r.get("doc_title", ""),
            "doc_filename": r.get("doc_filename", ""),
            "heading": r.get("heading", ""),
            "content": r.get("content", ""),
            "chunk_id": r.get("chunk_id", ""),
        })

    result = {
        "chunk_id": "glossary_" + query,
        "doc_id": "",
        "doc_title": "📖 词条：" + glossary_data.get("term", query),
        "doc_filename": "",
        "seq": -1,
        "heading": "词条解释",
        "content": glossary_data.get("definition", ""),
        "glossary": glossary_data,
        "mentions": mentions,
        "mention_count": len(mention_results),
    }
    return [result]


def _enrich(chunk, score=None):
    doc = db.get_document(chunk.get("doc_id", ""))
    result = {
        "id": chunk["id"],
        "chunk_id": chunk["id"],
        "doc_id": chunk.get("doc_id", ""),
        "doc_title": doc["title"] if doc else "",
        "doc_filename": doc["filename"] if doc else "",
        "seq": chunk.get("seq", 0),
        "heading": chunk.get("heading", ""),
        "content": chunk.get("content", ""),
    }
    if score is not None:
        result["score"] = round(score, 4)
    return result
