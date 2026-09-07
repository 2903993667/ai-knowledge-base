import httpx, asyncio, json, sys
sys.stdout.reconfigure(encoding='utf-8')

async def test():
    base_url = "https://api.xiaomimimo.com/v1"
    api_key = "sk-c9czml0e6i7084fwm0x2gvbbt73uj78t2vfc11nl6kaw9a6r"
    system = """You are an intelligent assistant integrated with a knowledge base database.
You can search and reference documents from the knowledge base to answer questions.
Always cite your sources when referencing knowledge base content. Use [Doc: title] format.
If you don't have enough information from the knowledge base, say so clearly."""

    async with httpx.AsyncClient(timeout=60) as c:
        print("=== With app system prompt ===")
        r = await c.post(
            base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json={
                "model": "mimo-v2.5",
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": "What is the weather in Beijing today?"}
                ],
                "tools": [{"type": "builtin_function", "function": {"name": "$web_search"}}],
                "max_tokens": 1000
            }
        )
        data = r.json()
        choice = data["choices"][0]
        msg = choice["message"]
        print("finish_reason:", choice["finish_reason"])
        print("content:", repr(msg.get("content", ""))[:300])
        tc = msg.get("tool_calls")
        if tc:
            print("tool_calls:", json.dumps(tc, ensure_ascii=False)[:300])
        else:
            print("No tool_calls")
        print("keys:", list(msg.keys()))

asyncio.run(test())
