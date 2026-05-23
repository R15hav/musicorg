"""Example 05 — embed musicorg in a FastAPI web app.

ILLUSTRATIVE SKELETON — not a runnable server out of the box.
Requires:  pip install fastapi uvicorn websockets

This file shows the integration shape, not a production implementation.
The upcoming official musicorgd daemon (see _organizer/LIBRARY_PLAN.md §7
Step 4) will implement exactly this pattern with full job persistence,
authentication, and multi-library support.  This example is a preview of
what bespoke integration looks like if you need musicorg inside your own
FastAPI app before musicorgd ships.

Design decisions shown here:
  - Jobs run on a background thread (scan blocks; asyncio event loop must not block).
  - ProgressEvent callbacks enqueue JSON-serialisable dicts into a per-job queue.
  - The WebSocket /events endpoint drains that queue in a tight asyncio loop.
  - Job state is in-process (dict); swap for Redis/DB in production.

Run (if fastapi + uvicorn are installed):
    uvicorn 05_embed_in_fastapi:app --reload
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Illustrative imports — these require `pip install fastapi uvicorn`.
# The musicorg imports below are real and come from the installed library.
# ---------------------------------------------------------------------------
try:
    from fastapi import FastAPI, WebSocket, WebSocketDisconnect
    from fastapi.responses import JSONResponse
    _FASTAPI_AVAILABLE = True
except ImportError:
    _FASTAPI_AVAILABLE = False
    # Stub types so the rest of the file parses cleanly even without fastapi.
    class FastAPI:  # type: ignore[no-redef]
        def post(self, *a: Any, **kw: Any) -> Any: ...
        def get(self, *a: Any, **kw: Any) -> Any: ...
        def websocket(self, *a: Any, **kw: Any) -> Any: ...
    class WebSocket:  # type: ignore[no-redef]
        async def accept(self) -> None: ...
        async def send_json(self, data: Any) -> None: ...
        async def receive_text(self) -> str: ...
    class WebSocketDisconnect(Exception): ...  # type: ignore[no-redef]
    class JSONResponse:  # type: ignore[no-redef]
        def __init__(self, content: Any, status_code: int = 200) -> None: ...

from musicorg import load_config, scan, ProgressEvent


# ---------------------------------------------------------------------------
# In-process job registry.  Replace with a real store (Redis, SQLite) for
# anything beyond a single-process prototype.
# ---------------------------------------------------------------------------
_jobs: dict[str, dict[str, Any]] = {}


app = FastAPI(title="musicorg API skeleton")


# ---------------------------------------------------------------------------
# POST /scan
# ---------------------------------------------------------------------------
@app.post("/scan")
async def start_scan(body: dict[str, str]) -> JSONResponse:
    """Accept a scan request and immediately return a job ID.

    The scan runs on a background thread so this endpoint returns fast.
    The caller polls GET /jobs/{id} or subscribes to WS /events for progress.

    Expected body: {"music_root": "/path/to/music"}
    """
    music_root = body.get("music_root", "")
    if not music_root:
        return JSONResponse({"error": "music_root required"}, status_code=400)

    job_id = str(uuid.uuid4())
    # Per-job queue: the scan thread pushes progress dicts; the WS drains them.
    event_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    _jobs[job_id] = {"status": "running", "results": None, "queue": event_queue}

    # Grab the running event loop so the background thread can put() into it.
    loop = asyncio.get_event_loop()

    def _run_scan() -> None:
        """Background thread: calls the blocking musicorg.scan()."""
        cfg = load_config(state_root=Path("/tmp/musicorg-fastapi-example"))

        def on_progress(ev: ProgressEvent) -> None:
            # ProgressEvent arrives on the worker thread; schedule an asyncio
            # put() on the main loop so the WebSocket handler can read it.
            loop.call_soon_threadsafe(
                event_queue.put_nowait,
                {
                    "phase": ev.phase,
                    "current": ev.current,
                    "total": ev.total,
                    "path": ev.path,
                    "error": ev.error,
                },
            )

        try:
            tracks = scan(cfg, root=Path(music_root), progress=on_progress)
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["results"] = {
                "total": len(tracks),
                "tracks": [
                    {
                        "path": str(t.path),
                        "title": t.title,
                        "artist": t.artist,
                        "fingerprint": t.fingerprint_sha256[:16],
                    }
                    for t in tracks[:100]  # cap response size; paginate in prod
                ],
            }
        except Exception as exc:  # noqa: BLE001
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)
        finally:
            # Sentinel: tells the WS handler the stream is finished.
            loop.call_soon_threadsafe(event_queue.put_nowait, {"done": True})

    threading.Thread(target=_run_scan, daemon=True).start()
    return JSONResponse({"job_id": job_id}, status_code=202)


# ---------------------------------------------------------------------------
# GET /jobs/{id}
# ---------------------------------------------------------------------------
@app.get("/jobs/{job_id}")
async def get_job(job_id: str) -> JSONResponse:
    """Return the current status and results (if complete) for a job."""
    job = _jobs.get(job_id)
    if job is None:
        return JSONResponse({"error": "not found"}, status_code=404)
    return JSONResponse({
        "job_id": job_id,
        "status": job["status"],
        "results": job.get("results"),
        "error": job.get("error"),
    })


# ---------------------------------------------------------------------------
# WebSocket /events
# ---------------------------------------------------------------------------
@app.websocket("/events")
async def events_ws(ws: WebSocket) -> None:
    """Push ProgressEvent JSON to the client as the scan runs.

    The client sends the job_id as its first message, then receives one
    JSON object per file until {"done": true} is delivered.
    """
    await ws.accept()
    try:
        job_id = await ws.receive_text()
        job = _jobs.get(job_id)
        if job is None:
            await ws.send_json({"error": "unknown job_id"})
            return

        queue: asyncio.Queue[dict[str, Any]] = job["queue"]
        while True:
            event = await queue.get()
            await ws.send_json(event)
            if event.get("done"):
                break
    except WebSocketDisconnect:
        pass


if __name__ == "__main__":
    if not _FASTAPI_AVAILABLE:
        print("fastapi is not installed.  Run: pip install fastapi uvicorn")
        raise SystemExit(1)
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
