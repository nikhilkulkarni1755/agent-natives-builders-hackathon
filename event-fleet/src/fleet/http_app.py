"""HTTP + SSE front door for the fleet, alongside -- never instead of -- stdio MCP.

`fleet.server` stays exactly as it is: a stdio MCP process whose stdout is the
JSON-RPC channel. This module opens a *second* door onto the same pipeline for the
two consumers that cannot speak stdio -- a public Cloudflare tunnel, and a browser
front end that streams from an SSE backend (D-017). It imports
`fleet.server.prep_conference` and `fleet.render.render`; it re-implements no part of
the orchestration and defines no shape of its own.

Routes
------
* ``GET  /health``       -- liveness. No model calls, no network, safe to poll.
* ``POST /prep``         -- the full `ConferenceBriefing` as JSON, one shot.
* ``POST /prep/stream``  -- the same run as Server-Sent Events, stage by stage.
* ``GET  /prep/stream``  -- ditto with query params, for `EventSource` clients.

Why the stream exists
---------------------
A real run is 40-70s (roster ~20s, enrich ~11s, rank ~22s, judge ~12s). A front end
staring at a closed socket for a minute looks hung, and an idle minute is also exactly
what a proxy like Cloudflare kills. So the stream reports the work as it happens and
never goes quiet for more than `HEARTBEAT_S`.

How progress is observed without duplicating the pipeline
---------------------------------------------------------
Every lane already logs its own structured progress to stderr. Rather than re-walking
the pipeline to narrate it -- which would fork the orchestration and rot -- this module
attaches a temporary `logging.Handler` to the ``fleet`` logger for the duration of one
run and republishes those records as SSE events. The lanes stay the single source of
truth for what happened; this is a tap, not a second implementation.

That tap is only unambiguous because runs are serialised (`MAX_CONCURRENCY`, default 1):
with one run in flight, every ``fleet.*`` record in the window belongs to it. The
concurrency cap is therefore load-bearing for correctness, not only for cost.

Public-internet posture
-----------------------
This is about to be reachable by strangers, so:

* No secret ever leaves the process. `_public()` scrubs every republished log line of
  key/token shapes and of absolute filesystem paths before it reaches the wire, and no
  route echoes the environment.
* No auth system (out of scope), but a run costs the human's real Nebius/Tavily quota
  and touches a rate-limited LinkedIn account (D-015), so `_Budget` caps concurrency,
  per-caller frequency and total runs per hour, and refuses politely.
* D-005 holds absolutely: nothing here writes to stdout. Logging goes to stderr.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from contextvars import ContextVar
import json
import logging
import os
import re
import time
from collections import deque
from collections.abc import AsyncIterator, Callable
from typing import Any

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import (
    FileResponse,
    JSONResponse,
    PlainTextResponse,
    Response,
    StreamingResponse,
)
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from fleet.models import PrepRequest
from fleet.render import render
from fleet import enrich
from fleet.server import _load_env, prep_conference

AGENT = "H1/http"

log = logging.getLogger("fleet.http")

HEARTBEAT_S = float(os.getenv("FLEET_HTTP_HEARTBEAT_S", "2.0"))
"""Longest the stream may stay silent. Well under any proxy's idle timeout."""

MAX_CONCURRENCY = int(os.getenv("FLEET_HTTP_CONCURRENCY", "1"))
"""Runs in flight at once. Keep at 1: the log tap assumes it (see module docstring)."""

IP_COOLDOWN_S = float(os.getenv("FLEET_HTTP_IP_COOLDOWN_S", "60"))
"""Minimum gap between two runs from the same caller."""

HOURLY_CAP = int(os.getenv("FLEET_HTTP_HOURLY_CAP", "20"))
"""Total runs per rolling hour across all callers -- the actual spend ceiling."""

MAX_EVENT_NAME = 200
MAX_INTENT = 1000

# --------------------------------------------------------------------------- #
# Scrubbing: everything below this line may end up on the public internet.
# --------------------------------------------------------------------------- #

_SECRET_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # key=value / "token": "value" / Authorization: Bearer xxx
    (re.compile(r"(?i)\b(api[_-]?key|apikey|token|secret|password|authorization)\b\s*[=:]\s*\"?\S+"),
     r"\1=<redacted>"),
    (re.compile(r"(?i)\bbearer\s+\S+"), "bearer <redacted>"),
    # Vendor-shaped credentials, wherever they appear.
    (re.compile(r"\b(?:sk-|irid_|tvly-|eyJ)[A-Za-z0-9._\-]{8,}"), "<redacted>"),
    # Absolute paths leak the host's layout (and the .env's location). Not needed publicly.
    (re.compile(r"(?:/Users|/home|/root|/var|/tmp)/\S+"), "<path>"),
)

_LOG_PREFIX = re.compile(r"^(?:[a-z_]+:\s*)?agent=\S+\s+")
"""Lane log lines start with an internal `agent=` tag; readers do not need it."""


def _public(text: str, limit: int = 240) -> str:
    """A log line reduced to something safe to hand a stranger."""
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    text = _LOG_PREFIX.sub("", " ".join(text.split()))
    return text[:limit]


# --------------------------------------------------------------------------- #
# Stage vocabulary -- derived from the logger that emitted the record, so it stays
# correct as lanes change what they say.
# --------------------------------------------------------------------------- #

_STAGES: dict[str, tuple[str, str]] = {
    "fleet.roster": ("roster", "Fetching the published roster for this event"),
    "fleet.enrich": ("enrich", "Enriching the people on it with verifiable facts"),
    # The Iridium client logs on enrichment's behalf; without this the stage flaps.
    "fleet.iridium": ("enrich", "Enriching the people on it with verifiable facts"),
    "fleet.rank": ("rank", "Ranking everyone against what you want out of the event"),
    "fleet.judge": ("judge", "Auditing every cited fact before you see it"),
    "fleet.store": ("store", "Saving the run"),
    "fleet.server": ("pipeline", "Running the pipeline"),
}


def _stage_of(logger_name: str) -> tuple[str, str]:
    """Longest-prefix match, so `fleet.enrich.iridium` still reads as the enrich stage."""
    while logger_name:
        if logger_name in _STAGES:
            return _STAGES[logger_name]
        logger_name = logger_name.rpartition(".")[0]
    return ("pipeline", "Working")


class _LogTap(logging.Handler):
    """Republishes `fleet.*` log records to a callback, for the length of one run."""

    def __init__(self, sink: Callable[[logging.LogRecord], None]) -> None:
        super().__init__(level=logging.INFO)
        self._sink = sink

    def emit(self, record: logging.LogRecord) -> None:
        if record.name.startswith(log.name):
            return  # this module is itself a `fleet.*` logger; do not echo ourselves
        try:
            self._sink(record)
        except Exception:  # a broken tap must never break the run it is watching
            self.handleError(record)


# --------------------------------------------------------------------------- #
# Abuse control
# --------------------------------------------------------------------------- #


class _Budget:
    """Three cheap, honest limits. Not auth -- a spend ceiling.

    Asyncio is single-threaded, so the counters need no locking.
    """

    def __init__(self) -> None:
        self._active = 0
        self._last_seen: dict[str, float] = {}
        self._hour: deque[float] = deque()

    def _prune(self, now: float) -> None:
        while self._hour and now - self._hour[0] > 3600:
            self._hour.popleft()
        stale = [ip for ip, t in self._last_seen.items() if now - t > IP_COOLDOWN_S]
        for ip in stale:
            del self._last_seen[ip]

    def claim(self, caller: str) -> str | None:
        """Reserve a run slot. Returns None on success, or a reason to refuse."""
        now = time.monotonic()
        self._prune(now)
        if self._active >= MAX_CONCURRENCY:
            return (
                f"A briefing is already running (limit {MAX_CONCURRENCY} at a time). "
                "Each run costs real model and search quota, so they are serialised. "
                "Try again in a minute."
            )
        if len(self._hour) >= HOURLY_CAP:
            return (
                f"This deployment is capped at {HOURLY_CAP} briefings per hour and has "
                "reached it. Try again later."
            )
        last = self._last_seen.get(caller)
        if last is not None and now - last < IP_COOLDOWN_S:
            wait = int(IP_COOLDOWN_S - (now - last)) + 1
            return f"Rate limited: one briefing per {int(IP_COOLDOWN_S)}s per caller. Retry in {wait}s."
        self._active += 1
        self._last_seen[caller] = now
        self._hour.append(now)
        return None

    def release(self) -> None:
        self._active = max(0, self._active - 1)


_budget = _Budget()


def _caller(request: Request) -> str:
    """Best available caller identity. Behind the tunnel the peer is always localhost.

    Cloudflare overwrites `CF-Connecting-IP` on every request it proxies, so it is
    trustworthy *when the only route in is the tunnel*. It is a cost control, not a
    security boundary, and is treated as one.
    """
    for header in ("cf-connecting-ip", "x-forwarded-for"):
        value = request.headers.get(header)
        if value:
            return value.split(",")[0].strip()[:64]
    return request.client.host if request.client else "unknown"


# --------------------------------------------------------------------------- #
# Request parsing
# --------------------------------------------------------------------------- #


class _BadRequest(Exception):
    """A caller-fixable problem. Carries the message shown to the caller."""


async def _read_request(request: Request) -> PrepRequest:
    """Build the contract's `PrepRequest` from a JSON body or a query string."""
    if request.method == "POST":
        try:
            raw = await request.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _BadRequest(f"Body must be JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise _BadRequest('Body must be a JSON object: {"event_name": "...", "intent": "..."}')
    else:
        raw = dict(request.query_params)

    event_name = str(raw.get("event_name") or "").strip()
    intent = str(raw.get("intent") or "").strip()
    if not event_name or not intent:
        raise _BadRequest(
            'Both "event_name" and "intent" are required, e.g. '
            '{"event_name": "AI Engineer World\'s Fair 2026", "intent": "hiring inference engineers"}'
        )
    if len(event_name) > MAX_EVENT_NAME or len(intent) > MAX_INTENT:
        raise _BadRequest(
            f"Too long: event_name <= {MAX_EVENT_NAME} chars, intent <= {MAX_INTENT} chars."
        )
    return PrepRequest(event_name=event_name, intent=intent)


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


_pending_key: ContextVar[str | None] = ContextVar("fleet_http_caller_key", default=None)


_WEB = Path(__file__).resolve().parents[3] / "web"


async def index(request: Request) -> Response:
    """Serve the front end at the root.

    The public URL is what a first-time visitor pastes, so it has to show the product
    rather than a 404. Falls back to plain text if the page is missing, since the API
    is useful on its own and a missing asset must not take the endpoint down.
    """
    page = _WEB / "index.html"
    if page.is_file():
        return FileResponse(page, media_type="text/html")
    return PlainTextResponse(
        "Conference Prep Fleet -- API is up. Try GET /health, or POST /prep with "
        '{"event_name": "...", "intent": "..."}.',
        status_code=200,
    )


def _bind_caller(request: Request) -> None:
    """Bind this HTTP caller's own Iridium identity, never the operator's.

    The server process holds the operator's key, so without this every public request
    would be enriched as -- and told it is -- the person who started the server. A
    caller who wants their own profile supplies their own key; one who does not gets a
    briefing ranked on their stated goal alone. The key is read from the request and
    never stored, logged, or persisted.
    """
    key = request.headers.get("x-iridium-key") or None
    if not key:
        auth = request.headers.get("authorization") or ""
        if auth.lower().startswith("bearer "):
            key = auth[7:].strip() or None
    _pending_key.set(key)
    enrich.set_caller(key, remote=True)


async def health(request: Request) -> JSONResponse:
    """Liveness only. Touches no model, no network, no key -- safe for a tunnel to poll."""
    return JSONResponse({
        "status": "ok",
        "service": "conference-prep-fleet",
        "transports": ["stdio-mcp", "http-sse"],
        "routes": ["/health", "/prep", "/prep/stream"],
        "runs_in_flight": _budget._active,
        "limits": {
            "concurrency": MAX_CONCURRENCY,
            "per_caller_cooldown_s": IP_COOLDOWN_S,
            "hourly_cap": HOURLY_CAP,
        },
    })


async def prep(request: Request) -> JSONResponse:
    """One `ConferenceBriefing`, as JSON, exactly as the MCP tool returns it."""
    try:
        req = await _read_request(request)
    except _BadRequest as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    refusal = _budget.claim(_caller(request))
    if refusal:
        log.warning("agent=%s step=refuse route=/prep why=%s", AGENT, refusal)
        return JSONResponse({"error": refusal}, status_code=429)

    started = time.monotonic()
    try:
        _bind_caller(request)
        briefing = await asyncio.to_thread(prep_conference, req.event_name, req.intent)
    except Exception as exc:
        log.error("agent=%s step=prep event=%r exc=%r", AGENT, req.event_name, exc, exc_info=True)
        return JSONResponse(
            {"error": f"The pipeline raised {type(exc).__name__}. The run was not completed."},
            status_code=500,
        )
    finally:
        _budget.release()

    log.info(
        "agent=%s step=prep run_id=%s picks=%d elapsed_s=%.1f",
        AGENT, briefing.run_id, len(briefing.picks), time.monotonic() - started,
    )
    return JSONResponse(briefing.model_dump())


def _sse(event: str, payload: dict[str, Any]) -> bytes:
    """One SSE frame. `data` is always a single-line JSON object, so no framing edge cases."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n".encode()


def _run_bound(req: PrepRequest):
    """Run the pipeline in a worker thread with the caller identity already bound.

    contextvars set on the event loop do not propagate into `asyncio.to_thread`, so the
    binding is re-applied inside the thread that actually runs enrichment.
    """
    enrich.set_caller(_pending_key.get(), remote=True)
    return prep_conference(req.event_name, req.intent)


async def _stream(req: PrepRequest, request: Request) -> AsyncIterator[bytes]:
    """The run, narrated. Never silent for longer than `HEARTBEAT_S`."""
    started = time.monotonic()

    def since() -> float:
        return round(time.monotonic() - started, 2)

    refusal = _budget.claim(_caller(request))
    if refusal:
        log.warning("agent=%s step=refuse route=/prep/stream why=%s", AGENT, refusal)
        yield _sse("error", {"message": refusal, "retryable": True})
        return

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[logging.LogRecord] = asyncio.Queue()
    # The lanes log from worker threads (enrichment fans out over two pools), so the
    # hop back onto the event loop has to be thread-safe.
    tap = _LogTap(lambda record: loop.call_soon_threadsafe(queue.put_nowait, record))
    fleet_logger = logging.getLogger("fleet")
    fleet_logger.addHandler(tap)

    stage = ""
    future: asyncio.Future[Any] | None = None
    try:
        yield _sse("stage", {
            "stage": "start", "elapsed_s": since(),
            "message": f"Prepping {req.event_name}. A full run takes 40-70 seconds.",
        })

        future = asyncio.ensure_future(
            asyncio.to_thread(_run_bound, req)
        )
        # Bound to the run, not to the connection: if the caller hangs up mid-run the
        # thread keeps spending quota, so the slot stays held until it really finishes.
        future.add_done_callback(lambda _: _budget.release())
        while True:
            try:
                record = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_S)
            except asyncio.TimeoutError:
                if future.done() and queue.empty():
                    break
                # Silence is the one thing a tunnel will not tolerate.
                yield _sse("heartbeat", {"stage": stage or "start", "elapsed_s": since()})
                continue

            name, headline = _stage_of(record.name)
            if name != stage:
                stage = name
                yield _sse("stage", {"stage": stage, "elapsed_s": since(), "message": headline})
            yield _sse("progress", {
                "stage": stage,
                "elapsed_s": since(),
                "level": record.levelname.lower(),
                "message": _public(record.getMessage()),
            })

        briefing = await future
    except Exception as exc:
        log.error("agent=%s step=stream event=%r exc=%r", AGENT, req.event_name, exc, exc_info=True)
        yield _sse("error", {
            "message": f"The pipeline raised {type(exc).__name__}. The run was not completed.",
            "elapsed_s": since(),
        })
        return
    finally:
        fleet_logger.removeHandler(tap)
        if future is None:
            _budget.release()

    yield _sse("briefing", {
        "run_id": briefing.run_id,
        "elapsed_s": since(),
        "text": render(briefing),          # the same text the MCP client renders
        "briefing": briefing.model_dump(),  # the full contract shape, for programmatic use
    })
    yield _sse("done", {
        "run_id": briefing.run_id,
        "elapsed_s": since(),
        "picks": len(briefing.picks),
        "degradations": len(briefing.degradations),
    })
    log.info(
        "agent=%s step=stream run_id=%s picks=%d elapsed_s=%.1f",
        AGENT, briefing.run_id, len(briefing.picks), since(),
    )


async def prep_stream(request: Request) -> StreamingResponse:
    """SSE. Accepts a POST body or `EventSource`-friendly query params."""
    try:
        req = await _read_request(request)
    except _BadRequest as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)

    _bind_caller(request)
    return StreamingResponse(
        _stream(req, request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            # Tells nginx-family proxies not to buffer the stream into uselessness.
            "X-Accel-Buffering": "no",
        },
    )


# Any origin may read this: there is nothing here that is not already public, and a
# browser widget served from anywhere has to be able to open the stream.
app = Starlette(
    routes=[
        Route("/", index, methods=["GET"]),
        Mount("/vendor", app=StaticFiles(directory=str(_WEB / "vendor")), name="vendor"),
        Route("/health", health, methods=["GET"]),
        Route("/prep", prep, methods=["POST"]),
        Route("/prep/stream", prep_stream, methods=["GET", "POST"]),
    ],
    middleware=[
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=["content-type"],
        )
    ],
)

# The lane modules need their API keys however this process was launched, including
# when uvicorn imports `fleet.http_app:app` directly. `_load_env` never overrides an
# already-set variable and logs only that it ran.
_load_env()


def main() -> None:
    """Serve the HTTP door. Runs alongside the stdio server, never in place of it."""
    host = os.getenv("FLEET_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("FLEET_HTTP_PORT", "8787"))
    log.info(
        "agent=%s step=boot transport=http host=%s port=%d concurrency=%d hourly_cap=%d",
        AGENT, host, port, MAX_CONCURRENCY, HOURLY_CAP,
    )
    # stdout stays clean (D-005): this process may share a terminal with the stdio one.
    uvicorn.run(app, host=host, port=port, log_config=None, access_log=False)


if __name__ == "__main__":
    main()
