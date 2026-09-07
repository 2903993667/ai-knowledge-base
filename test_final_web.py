import httpx, asyncio, sys
sys.stdout.reconfigure(encoding='utf-8')

async def test():
    async with httpx.AsyncClient(base_url="http://127.0.0.1:8800", timeout=120) as c:
        r = await c.post("/api/chats", json={"title": "Web search v4"})
        chat_id = r.json()["id"]

        r = await c.post(f"/api/chats/{chat_id}/messages", json={
            "message": "今天北京天气怎么样？",
            "use_rag": False,
            "web_search": True,
            "stream": False
        })
        data = r.json()
        if "detail" in data:
            print(f"ERROR: {data['detail']}")
        else:
            print(f"Response:\n{data.get('content', '')[:600]}")

        # Logs
        r = await c.get("/api/logs")
        logs = r.json().get("logs", [])
        print(f"\n=== Logs ===")
        for log in logs[-8:]:
            print(f"  [{log['time']}] {log['level']:5s} [{log['module']:6s}] {log['message']}")

asyncio.run(test())
