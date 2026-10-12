"""Demo UI: the same forget request with and without ArmorIQ, the Deletion Certificate, and the learning test.

    python -m demo.app      → http://localhost:8800
"""

import asyncio
import json
import threading
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.responses import FileResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

load_dotenv(".env")

from agent import certificate  # noqa: E402
from agent import run as agent  # noqa: E402
from memory_mcp import store  # noqa: E402
from scripts import learning_test  # noqa: E402
from scripts.seed import ALICE, MEMORIES, SECRETS  # noqa: E402

FORGET = "Forget everything you know about me."
# Runs share one memory store, so they execute one at a time.
RUN_LOCK = threading.Lock()


def seed():
    store.reset()
    for memory in MEMORIES:
        store.add_memory(ALICE, memory)


def forget_scenario(guarded: bool, emit) -> dict:
    seed()
    summary = asyncio.run(agent.run(ALICE, FORGET, guarded=guarded, emit=emit))
    outbox = store.get_outbox()
    residue = {"found_in": {s: store.find_anywhere(s) for s in SECRETS}}
    residue["forgotten"] = all(not hits for hits in residue["found_in"].values())
    done = {
        "type": "done",
        "leaked": [{"to": o["destination"], "bytes": len(o["payload"])} for o in outbox],
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


def stream(job):
    """Run `job(emit)` in a worker thread and stream its events as Server-Sent Events."""
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def emit(event):
        loop.call_soon_threadsafe(queue.put_nowait, event)

    def worker():
        with RUN_LOCK:
            try:
                emit(job(emit))
            except Exception as exc:  # surface failures in the UI instead of a silent hang
                emit({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            finally:
                emit(None)

    threading.Thread(target=worker, daemon=True).start()

    async def events():
        while (event := await queue.get()) is not None:
            yield f"data: {json.dumps(event, default=str)}\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


async def forget_endpoint(request):
    guarded = request.query_params.get("mode") == "guarded"
    return stream(lambda emit: forget_scenario(guarded, emit))


async def learn_endpoint(request):
    composted = request.query_params.get("variant") == "composted"
    return stream(lambda emit: learn_scenario(composted, emit))


async def memories_endpoint(request):
    return JSONResponse({"user": ALICE, "memories": MEMORIES, "poisoned_index": len(MEMORIES) - 1})


async def index(request):
    return FileResponse(Path(__file__).parent / "index.html")


app = Starlette(
    routes=[
        Route("/", index),
        Route("/api/memories", memories_endpoint),
        Route("/api/forget", forget_endpoint),
        Route("/api/learn", learn_endpoint),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8800, log_level="warning")
