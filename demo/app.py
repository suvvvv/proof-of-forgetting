"""Demo UI: the same forget request with and without ArmorIQ, the Deletion Certificate, and the learning test.

    python -m demo.app      → http://localhost:8800

With SERVE_MCP=1 the app also serves memory-mcp at /mcp (one process, one port), which is how the
hosted live demo runs.
"""

import asyncio
import contextlib
import json
import os
import threading
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Mount, Route

load_dotenv(".env")

from armoriq_sdk import ArmorIQClient  # noqa: E402

from agent import certificate  # noqa: E402
from agent import run as agent  # noqa: E402
from memory_mcp import store  # noqa: E402
from scripts import learning_test  # noqa: E402
from scripts.seed import ALICE, MEMORIES, SECRETS  # noqa: E402

FORGET = "Forget everything you know about me."
PORT = int(os.getenv("PORT", "8800"))
SERVE_MCP = os.getenv("SERVE_MCP") == "1"
if SERVE_MCP:
    os.environ.setdefault("MCP_LOCAL_URL", f"http://127.0.0.1:{PORT}/mcp")
    agent.MCP_URL = os.environ["MCP_LOCAL_URL"]

# Runs share one memory store, so they execute one at a time; a busy demo answers immediately
# instead of queueing. A short per-visitor cooldown protects the free-tier LLM quota.
RUN_LOCK = threading.Lock()
# The one ArmorIQ delegation the current run is waiting on. The page's approve/reject buttons can
# decide this request and nothing else.
PENDING = {"delegation_id": None}
# Approver identity for the in-page buttons: a member of the ArmorIQ org with approval rights.
# Unset → no buttons; approvals then happen only in the ArmorIQ console.
OFFICER_EMAIL = os.getenv("PRIVACY_OFFICER_EMAIL")
OFFICER_ROLE = os.getenv("PRIVACY_OFFICER_ROLE", "org_admin")
COOLDOWN_SECONDS = int(os.getenv("DEMO_COOLDOWN", "20"))
_last_run: dict[str, float] = {}


def seed():
    store.reset()
    for memory in MEMORIES:
        store.add_memory(ALICE, memory)


def track_pending(emit):
    """Wrap an emitter so the waiting delegation is remembered for the approve/reject buttons."""

    def wrapped(event):
        if event.get("type") == "waiting":
            PENDING["delegation_id"] = event.get("delegation_id")
            event = {**event, "can_decide": bool(OFFICER_EMAIL)}
        elif event.get("type") in ("approved", "decision") and event.get("tool") == "delete_memory":
            PENDING["delegation_id"] = None
        emit(event)

    return wrapped


def forget_scenario(guarded: bool, emit) -> dict:
    seed()
    try:
        summary = asyncio.run(agent.run(ALICE, FORGET, guarded=guarded, emit=track_pending(emit)))
    finally:
        PENDING["delegation_id"] = None
    outbox = store.get_outbox()
    residue = {"found_in": {s: store.find_anywhere(s) for s in SECRETS}}
    residue["forgotten"] = all(not hits for hits in residue["found_in"].values())
    done = {
        "type": "done",
        "leaked": [{"to": o["destination"], "bytes": len(o["payload"]),
                    "secrets": [x for x in SECRETS if x.lower() in o["payload"].lower()]} for o in outbox],
        "memories_left": len(store.get_memories(ALICE)),
        "lessons": store.get_lessons(),
        "residue_free": residue["forgotten"],
    }
    if guarded and summary.get("plan"):
        cert = certificate.build(summary, summary["plan"]["signed_token"], residue, outbox, store.get_lessons())
        done["certificate"] = cert
        done["verification"] = certificate.verify(cert)
    return done


def learn_scenario(composted: bool, emit) -> dict:
    learning_test.forget(composted)
    asyncio.run(agent.run(ALICE, learning_test.REQUEST, guarded=True, emit=emit))
    emails = [json.loads(o["payload"]) for o in store.get_outbox()]
    repeated = any(w in json.dumps(e).lower() for e in emails for w in learning_test.HEALTH_WORDS)
    return {"type": "done", "emails": emails, "repeated_mistake": repeated, "lessons": store.get_lessons()}


def single_event(event: dict):
    async def events():
        yield f"data: {json.dumps(event)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


def stream(job, request=None):
    """Run `job(emit)` in a worker thread and stream its events as Server-Sent Events."""
    if request is not None:
        visitor = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0]
        if time.time() - _last_run.get(visitor, 0) < COOLDOWN_SECONDS:
            return single_event({"type": "error", "message": "Please wait a few seconds between runs."})
    if not RUN_LOCK.acquire(blocking=False):
        return single_event({"type": "error", "message": "Another visitor's demo is running. Try again in a minute."})
    if request is not None:
        _last_run[visitor] = time.time()
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker():
        try:
            emit(job(emit))
        except Exception as exc:  # surface failures in the UI instead of a silent hang
            emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
        finally:
            RUN_LOCK.release()
            emit(None)

    threading.Thread(target=worker, daemon=True).start()

    async def events():
        while (event := await queue.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


async def forget_endpoint(request):
    guarded = request.query_params.get("mode") == "guarded"
    return stream(lambda emit: forget_scenario(guarded, emit), request)


async def learn_endpoint(request):
    composted = request.query_params.get("variant") == "composted"
    return stream(lambda emit: learn_scenario(composted, emit), request)


async def decide_endpoint(request):
    """Approve or reject the delegation the current run is waiting on, as the privacy officer."""
    action = request.query_params.get("action")
    delegation_id = PENDING["delegation_id"]
    if action not in ("approve", "reject") or not OFFICER_EMAIL:
        return JSONResponse({"ok": False, "message": "not available"}, status_code=400)
    if not delegation_id:
        return JSONResponse({"ok": False, "message": "nothing is waiting for approval"}, status_code=409)
    client = ArmorIQClient()
    body = {"delegationId": delegation_id, "approverEmail": OFFICER_EMAIL, "approverRole": OFFICER_ROLE}
    if action == "reject":
        body["reason"] = "Rejected by the privacy officer in the live demo"
    response = await asyncio.to_thread(
        client.http_client.post,
        f"{client.backend_endpoint}/delegation/{action}",
        json=body,
        headers={"X-API-Key": client.api_key, "Content-Type": "application/json", "X-User-Email": OFFICER_EMAIL},
        timeout=15,
    )
    ok = response.status_code < 300
    return JSONResponse({"ok": ok, "message": None if ok else response.text[:200]}, status_code=200 if ok else 502)


async def memories_endpoint(request):
    return JSONResponse({"user": ALICE, "memories": MEMORIES, "poisoned_index": len(MEMORIES) - 1,
                         "secrets": SECRETS, "health_words": list(learning_test.HEALTH_WORDS)})


async def index(request):
    return FileResponse(Path(__file__).parent / "index.html")


routes = [
    Route("/", index),
    Route("/api/memories", memories_endpoint),
    Route("/api/forget", forget_endpoint),
    Route("/api/learn", learn_endpoint),
    Route("/api/decide", decide_endpoint, methods=["POST"]),
]
lifespan = None
if SERVE_MCP:
    from memory_mcp.server import http_app, mcp

    routes.append(Mount("/", app=http_app()))

    @contextlib.asynccontextmanager
    async def lifespan(app):
        async with mcp.session_manager.run():
            yield

app = Starlette(routes=routes, lifespan=lifespan)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0" if SERVE_MCP else "127.0.0.1", port=PORT, log_level="warning")
