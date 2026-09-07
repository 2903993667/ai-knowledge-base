"""RAG chat — retrieve knowledge base chunks, generate answers with citations."""

import json
from . import database as db
from . import ai
from . import search
from . import logger


SYSTEM_PROMPT = """你是一个严谨的学术文档问答助手。请严格基于提供的上下文（Context）回答问题。

核心原则：
1. **逐字核对**：回答前，先从上下文中找到对应的原句，逐字核对后再总结。
2. **引用优先**：先引述原文，再给出你的解读。格式：原文 → 解读。

3. **突变体逻辑推导规则**（必须严格遵守）：
   - 步骤1：找到每个结构域的功能（例如：HNH 切割互补链，RuvC 切割非互补链）
   - 步骤2：确定突变影响哪个结构域（例如：D10A 影响 RuvC，H840A 影响 HNH）
   - 步骤3：**被影响的结构域失活**，**未影响的结构域保留功能**
   - 步骤4：推导保留的能力 = 未影响结构域的功能
   
   **示例**：
   - 原文："HNH cleaves complementary strand, RuvC cleaves noncomplementary strand"
   - D10A = RuvC突变 → RuvC失活 → HNH仍工作 → D10A保留切割【互补链】的能力
   - H840A = HNH突变 → HNH失活 → RuvC仍工作 → H840A保留切割【非互补链】的能力
   - **绝对不要**说 D10A 保留非互补链能力！

4. **禁止颠倒**：在输出前，用以下公式验证：
   - 突变体X = 结构域A突变 → 结构域A失活 → 结构域B保留 → X保留【结构域B的功能】
   - 不是"X保留结构域A的功能"！

5. **输出格式**：对于突变体问题，用以下格式：
   - "原文指出：[引述原文]"
   - "推导：[突变体] = [结构域]突变 → [结构域]失活 → [另一结构域]仍工作 → [突变体]保留[功能]"

注意事项：
- 如果上下文包含明确事实，直接给出结论并引述对应依据。
- 引用文献时使用 [Doc: title] 格式标注来源。
- 如果确实无法从上下文中找到答案，请明确说明"基于提供的文档内容，未能找到相关信息"。"""


async def _ai_filter_chunks(query, chunks, max_chunks=6):
    """Use AI to filter and select truly relevant chunks from search results."""
    if not chunks or len(chunks) <= max_chunks:
        return chunks
    
    # Build chunk summaries for AI evaluation
    chunk_summaries = []
    for i, ch in enumerate(chunks):
        summary = ch["content"][:300].replace("\n", " ")
        chunk_summaries.append(f"[{i}] {ch.get('doc_title', '')} / {ch.get('heading', '')}: {summary}")
    
    chunks_text = "\n".join(chunk_summaries)
    
    filter_prompt = f"""请分析以下文档片段，筛选出与用户问题相关的所有内容。

用户问题：{query}

文档片段：
{chunks_text}

请返回所有可能相关的片段编号（JSON数组格式），按相关性排序。
例如：[2, 0, 5, 3]

筛选原则（超宽松策略）：
1. 只要片段内容与问题主题有任何关联，就保留
2. 包含问题中任何关键词、相关术语、同义词的片段都保留
3. 涉及同一研究领域、同一方法论、同一概念的片段都保留
4. 实验结果、方法描述、讨论部分都可能相关
5. 宁可多保留10个，不要遗漏1个关键信息
6. 特别注意：如果问题涉及多个方面（如原因、影响、方法），每个方面的相关片段都要保留"""

    try:
        response = await ai.chat_complete(
            [{"role": "user", "content": filter_prompt}],
            temperature=0.1,
            max_tokens=200
        )
        
        # Parse response to get indices
        import re
        # Try to find JSON array in response - more flexible pattern
        match = re.search(r'\[[\d\s,]+\]', response)
        if match:
            try:
                indices = [int(x.strip()) for x in match.group().strip('[]').split(',') if x.strip().isdigit()]
            except:
                indices = []
            filtered = [chunks[i] for i in indices if 0 <= i < len(chunks)]
            logger.info("chat", f"AI filtered {len(chunks)} -> {len(filtered)} chunks")
            return filtered[:max_chunks] if filtered else chunks[:max_chunks]
        else:
            logger.warn("chat", "Could not parse AI filter response, using all chunks")
            return chunks[:max_chunks]
    except Exception as e:
        logger.error("chat", f"AI filtering failed: {e}, using all chunks")
        return chunks[:max_chunks]


async def chat(chat_id, user_message, use_rag=True, search_mode="keyword", web_search=False):
    """Process a chat message with optional RAG."""
    db.append_message(chat_id, "user", user_message)

    history = db.get_messages(chat_id, limit=20)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]

    context = ""
    sources = []

    if use_rag:
        try:
            logger.info("chat", f"Searching knowledge base, mode={search_mode}")
            results = await search.search(user_message, mode=search_mode, limit=15)
            logger.info("chat", f"Search returned {len(results) if results else 0} results")
            
            if results:
                # Log search results for debugging
                for i, r in enumerate(results[:15]):
                    logger.info("chat", f"Search[{i}]: {r.get('doc_title', '')} / {r.get('heading', '')} (score={r.get('score', 'N/A')})")
                
                # Stage 1: Rerank results
                logger.info("chat", "Reranking results...")
                doc_texts = [r["content"][:500] for r in results]
                reranked_indices = await ai.rerank(user_message, doc_texts, top_n=12)
                reranked_results = [results[i] for i in reranked_indices if i < len(results)]
                
                # Log reranked results
                for i, r in enumerate(reranked_results[:10]):
                    logger.info("chat", f"Reranked[{i}]: {r.get('doc_title', '')} / {r.get('heading', '')}")
                
                # Stage 2: AI filter for relevance
                logger.info("chat", "AI filtering relevant chunks...")
                filtered_results = await _ai_filter_chunks(user_message, reranked_results[:12])
                
                if filtered_results:
                    context_parts = []
                    for r in filtered_results[:8]:
                        context_parts.append(f"[Source: {r['doc_title']} / {r['heading']}]\n{r['content'][:1500]}")
                        sources.append({"doc_id": r["doc_id"], "doc_title": r["doc_title"], "heading": r["heading"]})
                    context = "\n\n---\n\n".join(context_parts)
                    logger.info("chat", f"Final context: {len(filtered_results)} chunks")
        except Exception as e:
            logger.error("chat", f"RAG error: {e}")

    system = SYSTEM_PROMPT
    if context:
        system += f"\n\nRelevant knowledge base content:\n\n{context}"

    try:
        logger.info("chat", "Calling AI model...")
        response = await ai.chat_complete(
            messages + [{"role": "user", "content": user_message}],
            system=system,
            temperature=0.7,
            max_tokens=4096,
            stream=False,
            web_search=web_search,
        )
    except Exception as e:
        response = f"AI 服务错误: {str(e)}"
        logger.error("chat", f"Chat error: {e}")

    db.append_message(chat_id, "assistant", response, json.dumps(sources, ensure_ascii=False))
    return {"content": response, "sources": sources}


async def chat_stream(chat_id, user_message, use_rag=True, search_mode="keyword", web_search=False):
    """Stream a chat response with optional RAG."""
    db.append_message(chat_id, "user", user_message)

    history = db.get_messages(chat_id, limit=20)
    messages = [{"role": m["role"], "content": m["content"]} for m in history[:-1]]

    context = ""
    sources = []

    if use_rag:
        try:
            logger.info("chat", f"Searching knowledge base, mode={search_mode}")
            results = await search.search(user_message, mode=search_mode, limit=15)
            logger.info("chat", f"Search returned {len(results) if results else 0} results")
            
            if results:
                # Log search results for debugging
                for i, r in enumerate(results[:15]):
                    logger.info("chat", f"Search[{i}]: {r.get('doc_title', '')} / {r.get('heading', '')} (score={r.get('score', 'N/A')})")
                
                # Stage 1: Rerank results
                logger.info("chat", "Reranking results...")
                doc_texts = [r["content"][:500] for r in results]
                reranked_indices = await ai.rerank(user_message, doc_texts, top_n=12)
                reranked_results = [results[i] for i in reranked_indices if i < len(results)]
                
                # Log reranked results
                for i, r in enumerate(reranked_results[:10]):
                    logger.info("chat", f"Reranked[{i}]: {r.get('doc_title', '')} / {r.get('heading', '')}")
                
                # Stage 2: AI filter for relevance
                logger.info("chat", "AI filtering relevant chunks...")
                filtered_results = await _ai_filter_chunks(user_message, reranked_results[:12])
                
                if filtered_results:
                    context_parts = []
                    for r in filtered_results[:8]:
                        context_parts.append(f"[Source: {r['doc_title']} / {r['heading']}]\n{r['content'][:1500]}")
                        sources.append({"doc_id": r["doc_id"], "doc_title": r["doc_title"], "heading": r["heading"]})
                    context = "\n\n---\n\n".join(context_parts)
                    logger.info("chat", f"Final context: {len(filtered_results)} chunks")
        except Exception as e:
            logger.error("chat", f"RAG error: {e}")

    system = SYSTEM_PROMPT
    if context:
        system += f"\n\nRelevant knowledge base content:\n\n{context}"

    full_response = ""
    try:
        logger.info("chat", "Calling AI model (stream)...")
        gen = await ai.chat_complete(
            messages + [{"role": "user", "content": user_message}],
            system=system,
            temperature=0.7,
            max_tokens=4096,
            stream=True,
            web_search=web_search,
        )
        logger.info("chat", f"Got generator: {type(gen)}")
        chunk_count = 0
        async for chunk in gen:
            chunk_count += 1
            full_response += chunk
            logger.debug("chat", f"Stream chunk {chunk_count}: {len(chunk)} chars")
            yield {"type": "chunk", "content": chunk}
        logger.info("chat", f"Stream done: {chunk_count} chunks, {len(full_response)} chars")
    except Exception as e:
        full_response = f"AI 服务错误: {str(e)}"
        logger.error("chat", f"Stream chat error: {e}")

    db.append_message(chat_id, "assistant", full_response, json.dumps(sources, ensure_ascii=False))
    yield {"type": "done", "sources": sources}