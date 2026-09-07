"""AI client — chat, embedding, web search (all OpenAI-compatible)."""

import json
import re
import struct
import httpx
from . import database as db
from . import logger


def _s(key, default=""):
    return db.get_setting(key, default)


def get_ai_config():
    return {
        "chat_base_url": _s("ai_chat_base_url", "https://api.openai.com/v1"),
        "chat_api_key": _s("ai_chat_api_key", ""),
        "chat_model": _s("ai_chat_model", "gpt-4o-mini"),
        "embedding_base_url": _s("ai_embedding_base_url", ""),
        "embedding_api_key": _s("ai_embedding_api_key", ""),
        "embedding_model": _s("ai_embedding_model", ""),
        "embedding_dim": int(_s("ai_embedding_dim", "0")),
        "search_provider": _s("search_provider", ""),
        "search_api_key": _s("search_api_key", ""),
        "search_endpoint": _s("search_endpoint", ""),
    }



def _parse_text_tool_calls(ct):
    calls = []
    for m in re.finditer(r'tool_call.*?<function=(\S+)>.*?<parameter=(\w+)>(.*?)\/parameter>', ct, re.DOTALL):
        calls.append({"id": "tc_" + str(len(calls)), "name": m.group(1), "arguments": json.dumps({m.group(2): m.group(3).strip()})})
    return calls
def _parse_tool_name_format(ct):
    calls = []
    for m in re.finditer(r"<tool_name>(\S+)</tool_name>.*?<query>(.*?)</query>", ct, re.DOTALL):
        calls.append({"id": "tc_" + str(len(calls)), "name": m.group(1), "arguments": json.dumps({"query": m.group(2).strip()})})
    return calls

def _parse_name_args_format(ct):
    calls = []
    for m in re.finditer(r"<name>(\S+)</name>.*?<arguments>(.*?)</arguments>", ct, re.DOTALL):
        calls.append({"id": "tc_" + str(len(calls)), "name": m.group(1), "arguments": m.group(2).strip()})
    return calls

async def chat_complete(messages, model=None, temperature=0.7, max_tokens=4096, system="", stream=False, web_search=False, web_search_tool=None):
    cfg = get_ai_config()
    if not cfg["chat_api_key"]:
        raise ValueError("请在设置页面配置 AI 对话模型的 API Key")
    all_msgs = []
    if system:
        all_msgs.append({"role": "system", "content": system})
    all_msgs.extend(messages)
    url = f"{cfg['chat_base_url'].rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg['chat_api_key']}", "Content-Type": "application/json"}
    model_name = model or cfg["chat_model"]
    payload = {"model": model_name, "messages": all_msgs, "temperature": temperature, "max_tokens": max_tokens}
    if web_search:
        ws_tool = web_search_tool or db.get_setting("web_search_tool", "web_search_preview")
        if ws_tool in ("web_search_preview", "\\$web_search"):
            payload["tools"] = [{"type": "builtin_function", "function": {"name": "\\$web_search"}}]
        else:
            payload["tools"] = [{"type": ws_tool}]
    if stream:
        return _stream_chat(url, headers, payload)
    try:
        async with httpx.AsyncClient(timeout=120) as client:
            # Tool calls loop for builtin_function (MiMo/Kimi \\)
            for _turn in range(5):  # max 5 rounds of tool calls
                resp = await client.post(url, headers=headers, json=payload)
                if resp.status_code == 400 and "tools" in str(payload):
                    logger.warn("ai", "Tools rejected (400), retrying without tools")
                    payload.pop("tools", None)
                    resp = await client.post(url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]
                msg = choice["message"]
                finish = choice.get("finish_reason", "stop")
                tool_calls_list = msg.get("tool_calls")
                content_text = msg.get("content", "") or ""
                # MiMo models put answer in reasoning_content
                if not content_text and msg.get("reasoning_content"):
                    content_text = msg["reasoning_content"]
                # Also check for tool calls in text format (MiMo quirk)
                if content_text and ('tool_call' in content_text and ('<function=' in content_text or '<$' in content_text or 'tool_name>' in content_text or '<name>' in content_text)):
                    tool_calls_list = _parse_text_tool_calls(content_text) or _parse_tool_name_format(content_text) or _parse_name_args_format(content_text)
                    if tool_calls_list:

                        logger.info("ai", f"Parsed {len(tool_calls_list)} tool calls from text")

                # No tool_calls means final answer
                if not tool_calls_list and content_text:
                    final = content_text or msg.get("content", "")
                    logger.debug("ai", f"chat_complete OK: {len(final)} chars")
                    return final

                # Normalize: handle both standard API and text-parsed format
                normalized = []
                for tc in tool_calls_list:
                    if "function" in tc:
                        # Standard API: {id, type, function: {name, arguments}}
                        normalized.append({
                            "id": tc.get("id", f"tc_{len(normalized)}"),
                            "name": tc["function"]["name"],
                            "arguments": tc["function"].get("arguments", "{}"),
                        })
                    else:
                        # Text-parsed: {id, name, arguments}
                        normalized.append({
                            "id": tc.get("id", f"tc_{len(normalized)}"),
                            "name": tc["name"],
                            "arguments": tc.get("arguments", "{}"),
                        })
                # Append assistant message, then echo back tool results
                all_msgs.append(msg)
                for tc in normalized:
                    logger.info("ai", f"Tool call: {tc['name']}({tc['arguments'][:100]})")
                    all_msgs.append({
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "name": tc["name"],
                        "content": tc["arguments"],
                    })
            # If we exhausted all turns, return whatever we got
            content = msg.get("content", "")
            logger.debug("ai", f"chat_complete OK (after tool loop): {len(content)} chars")
            return content
    except httpx.HTTPStatusError as e:
        body = ""
        if hasattr(e, "response") and e.response is not None:
            try:
                body = e.response.text[:500]
            except Exception:
                pass
        logger.error("ai", f"HTTP {e.response.status_code}: {body}")
        raise
    except Exception as e:
        logger.error("ai", f"Request failed: {e}")
        raise


async def _stream_chat(url, headers, payload):
    logger.info("ai", "Stream chat starting")
    async with httpx.AsyncClient(timeout=120) as client:
        # If 400 with tools, retry without
        resp = await client.send(client.build_request("POST", url, headers=headers, json=payload), stream=True)
        if resp.status_code == 400 and "tools" in str(payload):
            logger.warn("ai", "Stream rejected (400), retrying without tools")
            await resp.aclose()
            payload2 = {k: v for k, v in payload.items() if k != "tools"}
            resp = await client.send(client.build_request("POST", url, headers=headers, json=payload2), stream=True)
        resp.raise_for_status()
        logger.info("ai", f"Stream response status: {resp.status_code}")
        line_count = 0
        async for raw_line in resp.aiter_lines():
            line_count += 1
            if not raw_line.strip():
                continue
            # Handle SSE format (data: {...})
            if raw_line.startswith("data: "):
                ds = raw_line[6:]
                if ds.strip() == "[DONE]":
                    break
            elif raw_line.startswith("{"):
                ds = raw_line
            else:
                continue
            try:
                chunk = json.loads(ds)
                choices = chunk.get("choices", [])
                if not choices:
                    continue
                c0 = choices[0]
                delta = c0.get("delta") or {}
                message = c0.get("message") or {}
                content = delta.get("content") or delta.get("reasoning_content") or message.get("content") or message.get("reasoning_content")
                if content:
                    yield content
            except (json.JSONDecodeError, KeyError, IndexError) as e:
                logger.warn("ai", f"Stream parse error: {e}")
                continue


async def get_embedding(text):
    cfg = get_ai_config()
    if not cfg["embedding_model"] or not cfg["embedding_api_key"]:
        return None
    base = cfg["embedding_base_url"] or cfg["chat_base_url"]
    url = f"{base.rstrip('/')}/embeddings"
    headers = {"Authorization": f"Bearer {cfg['embedding_api_key']}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(url, headers=headers, json={"model": cfg["embedding_model"], "input": text})
        resp.raise_for_status()
        return resp.json()["data"][0]["embedding"]


async def rerank(query, documents, top_n=5):
    """Rerank documents using cross-encoder model for better relevance."""
    cfg = get_ai_config()
    
    # Get reranker config - reuse embedding config or add separate
    rerank_base = db.get_setting("reranker_base_url", "") or cfg.get("embedding_base_url", "")
    rerank_key = db.get_setting("reranker_api_key", "") or cfg.get("embedding_api_key", "")
    rerank_model = db.get_setting("reranker_model", "BAAI/bge-reranker-v2-m3")
    
    if not rerank_key:
        logger.info("ai", "No reranker configured, returning original order")
        return list(range(len(documents)))[:top_n]
    
    url = f"{rerank_base.rstrip('/')}/rerank"
    headers = {"Authorization": f"Bearer {rerank_key}", "Content-Type": "application/json"}
    payload = {
        "model": rerank_model,
        "query": query,
        "documents": documents,
        "top_n": min(top_n, len(documents))
    }
    
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            results = resp.json().get("results", [])
            # Return indices sorted by relevance score (descending)
            sorted_indices = [r["index"] for r in sorted(results, key=lambda x: x["relevance_score"], reverse=True)]
            logger.info("ai", f"Reranked {len(documents)} docs, top scores: {[round(r['relevance_score'], 3) for r in results[:3]]}")
            return sorted_indices
    except Exception as e:
        logger.error("ai", f"Reranker failed: {e}, returning original order")
        return list(range(len(documents)))[:top_n]


async def get_embeddings_batch(texts, batch_size=32):
    cfg = get_ai_config()
    if not cfg["embedding_model"] or not cfg["embedding_api_key"]:
        return [None] * len(texts)
    results = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        base = cfg["embedding_base_url"] or cfg["chat_base_url"]
        url = f"{base.rstrip('/')}/embeddings"
        headers = {"Authorization": f"Bearer {cfg['embedding_api_key']}", "Content-Type": "application/json"}
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(url, headers=headers, json={"model": cfg["embedding_model"], "input": batch})
            resp.raise_for_status()
            sorted_data = sorted(resp.json()["data"], key=lambda x: x["index"])
            results.extend([item["embedding"] for item in sorted_data])
    return results


def serialize_embedding(vec):
    return struct.pack(f"{len(vec)}f", *vec)


def deserialize_embedding(blob):
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))


async def web_search(query, max_results=5):
    cfg = get_ai_config()
    provider = cfg["search_provider"]
    api_key = cfg["search_api_key"]
    if not provider or not api_key:
        raise ValueError("请在设置页面配置搜索 API（支持 Tavily / Bing / 自定义）")
    if provider == "tavily":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post("https://api.tavily.com/search", json={"api_key": api_key, "query": query, "max_results": max_results})
            resp.raise_for_status()
            return [{"title": r.get("title",""), "url": r.get("url",""), "snippet": r.get("content","")} for r in resp.json().get("results", [])]
    elif provider == "bing":
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get("https://api.bing.microsoft.com/v7.0/search",
                headers={"Ocp-Apim-Subscription-Key": api_key}, params={"q": query, "count": max_results, "mkt": "zh-CN"})
            resp.raise_for_status()
            return [{"title": r.get("name",""), "url": r.get("url",""), "snippet": r.get("snippet","")} for r in resp.json().get("webPages", {}).get("value", [])]
    elif provider == "custom":
        endpoint = cfg["search_endpoint"]
        if not endpoint:
            raise ValueError("自定义搜索需要配置搜索端点 URL")
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(endpoint, headers={"Authorization": f"Bearer {api_key}"}, params={"q": query, "max_results": max_results})
            resp.raise_for_status()
            return resp.json()
    raise ValueError(f"未知搜索提供商: {provider}")

async def fetch_models(base_url: str, api_key: str) -> list:
    """Fetch available models from an OpenAI-compatible /v1/models endpoint."""
    if not api_key:
        raise ValueError("请先填写 API Key")
    url = f"{base_url.rstrip('/')}/models"
    headers = {"Authorization": f"Bearer {api_key}"}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
    models = []
    for m in data.get("data", []):
        mid = m.get("id", "")
        owned = m.get("owned_by", "")
        models.append({"id": mid, "owned_by": owned})
    models.sort(key=lambda x: x["id"])
    return models
