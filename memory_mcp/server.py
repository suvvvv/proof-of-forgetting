"""memory-mcp: an agent memory store that can forget a user while keeping privacy-safe lessons.

Run:  python -m memory_mcp.server        (streamable HTTP on :8765/mcp)

If MCP_AUTH_TOKEN is set, every request must carry "Authorization: Bearer <token>". A public MCP
without it would let anyone call export_memory directly and bypass ArmorIQ entirely.
"""

import json
import os

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from memory_mcp import store
from memory_mcp.compost import compost

load_dotenv(".env")

mcp = FastMCP(
    "memory-mcp",
    instructions="Agent memory with provable forgetting. Raw memories can be composted into lessons, then deleted.",
    host="0.0.0.0",
    port=int(os.getenv("MCP_PORT", "8765")),
    stateless_http=True,
    json_response=True,
    # Served through a public tunnel/host, so the Host header won't be localhost.
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


@mcp.tool()
def store_memory(user_email: str, content: str) -> dict:
    """Save a raw memory about a user."""
    return {"memory_id": store.add_memory(user_email, content)}


@mcp.tool()
def recall_memory(user_email: str) -> dict:
    """Read every raw memory stored about a user (contains private data)."""
    memories = store.get_memories(user_email)
    return {"user_email": user_email, "count": len(memories), "memories": [m["content"] for m in memories]}


@mcp.tool()
def compost_memory(user_email: str) -> dict:
    """Turn each of a user's raw memories into an abstract, privacy-safe lesson and store the lessons.
    Raw memories are NOT returned and NOT deleted by this tool."""
    results = []
    for memory in store.get_memories(user_email):
        outcome = compost(memory["content"])
        if not outcome["lesson"] or outcome["leaks"]:
            continue
        if outcome["lesson"] not in store.get_lessons():
            store.add_lesson(outcome["lesson"], store.digest(memory["content"]))
        results.append({"lesson": outcome["lesson"], "method": outcome["method"]})
    return {"memories_processed": len(store.get_memories(user_email)), "lessons": results}


@mcp.tool()
def delete_memory(user_email: str) -> dict:
    """Permanently delete all raw memories about a user. Returns a deletion receipt with digests only."""
    return {"receipt": store.delete_memories(user_email, lessons_kept=len(store.get_lessons()))}


@mcp.tool()
def get_lessons() -> dict:
    """Read the abstract lessons learned from past (possibly deleted) memories. Contains no private data."""
    return {"lessons": store.get_lessons()}


@mcp.tool()
def export_memory(user_email: str, destination: str) -> dict:
    """Send a copy of a user's raw memories to an external destination (email address or URL).
    SANDBOX: writes to a local outbox table instead of really sending."""
    memories = [m["content"] for m in store.get_memories(user_email)]
    store.write_outbox(destination, json.dumps({"user_email": user_email, "memories": memories}))
    return {"exported": len(memories), "destination": destination}


@mcp.tool()
def send_email(to: str, subject: str, body: str) -> dict:
    """Send an email on the user's behalf. SANDBOX: writes to a local outbox instead of really sending."""
    store.write_outbox(to, json.dumps({"subject": subject, "body": body}))
    return {"sent": True, "to": to}


@mcp.tool()
def verify_forgotten(secrets: list[str]) -> dict:
    """Check whether any of the given strings still exist anywhere in storage (memories, lessons,
    outbox, or the raw database file). Used to prove a deletion actually worked."""
    report = {s: store.find_anywhere(s) for s in secrets}
    return {"forgotten": all(not hits for hits in report.values()), "found_in": report}


class BearerAuth:
    """Pure-ASGI guard: reject HTTP requests without the shared bearer token."""

    def __init__(self, app, token: str):
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and dict(scope["headers"]).get(b"authorization") != self.expected:
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json")]})
            await send({"type": "http.response.body", "body": b'{"error":"unauthorized"}'})
            return
        await self.app(scope, receive, send)


def http_app():
    """The MCP endpoint as an ASGI app (routes /mcp). Its session manager must be running:
    standalone, the app's own lifespan does it; when mounted elsewhere, run mcp.session_manager.run()."""
    app = mcp.streamable_http_app()
    token = os.getenv("MCP_AUTH_TOKEN")
    return BearerAuth(app, token) if token else app


if __name__ == "__main__":
    import uvicorn

    app = mcp.streamable_http_app()
    token = os.getenv("MCP_AUTH_TOKEN")
    uvicorn.run(BearerAuth(app, token) if token else app, host="0.0.0.0", port=int(os.getenv("MCP_PORT", "8765")))
