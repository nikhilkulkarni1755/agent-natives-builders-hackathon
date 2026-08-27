"""Tiny mesh helper -- the one way other lanes touch Cotal.

Two functions: `post` for progress, `error` for failures/degradations (spec section
6 -- every fleet error posts to the mesh so the audit log shows who failed, at what
step, why). Both shell out to the already-verified `cotal send` CLI (real mint +
round-trip + ACL-denial proof in coord/log/M2.jsonl) instead of reimplementing a NATS
client in Python -- that binary already knows how to resolve the local mesh,
authenticate, and publish; duplicating it here would be unjustified surface.

Contract: NEITHER FUNCTION EVER RAISES. A down or missing mesh is a degradation, not
a crash -- callers get a bool back and keep going. stdout is the MCP JSON-RPC channel
(D-005 HARD), so every failure here is logged to stderr via `logging`, never printed.
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess

log = logging.getLogger("fleet.mesh")

_TIMEOUT_S = 5
_PROGRESS_CHANNEL = "fleet.progress"
_ERROR_CHANNEL = "fleet.errors"
_MAX_CHARS = 500
# Callers pass raw exception text, which routinely carries the operator's home
# directory. The audit log gets shown on a projector, so a path never reaches it.
_ABS_PATH = re.compile(r"/(?:Users|home|private|var|tmp|opt)/[^\s'\"]*")


def _scrub(text: str) -> str:
    """One projector-safe line: no filesystem paths, no newlines, bounded length."""
    line = _ABS_PATH.sub("<path>", " ".join(text.split()))
    return line[:_MAX_CHARS] + "..." if len(line) > _MAX_CHARS else line


def _send(channel: str, text: str) -> bool:
    """Best-effort `cotal send msg <channel> "<text>"`. True on success, False (and
    logged to stderr) on anything else -- missing binary, unreachable mesh, denied
    ACL, timeout. Never raises."""
    text = _scrub(text)
    if shutil.which("cotal") is None:
        log.warning("mesh: cotal not on PATH, dropping #%s message: %s", channel, text)
        return False
    try:
        result = subprocess.run(
            ["cotal", "send", "msg", channel, text],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT_S,
        )
    except Exception:
        # Mesh down, socket gone, timeout -- a down mesh must degrade the demo, not
        # break it, so every failure mode funnels through this one broad catch.
        log.warning("mesh: send to #%s failed, mesh unreachable: %s", channel, text, exc_info=True)
        return False
    if result.returncode != 0:
        log.warning(
            "mesh: send to #%s denied or failed (exit %d): %s -- %s",
            channel, result.returncode, text, result.stderr.strip(),
        )
        return False
    return True


def post(text: str) -> bool:
    """Post a one-line progress update to the shared #fleet.progress channel.

    Best-effort, never raises. Returns whether it actually landed on the mesh."""
    return _send(_PROGRESS_CHANNEL, text)


def error(text: str, source: str) -> bool:
    """Post a one-line error/degradation to #fleet.errors, tagged with the lane it
    came from (e.g. "roster", "judge", "server").

    Best-effort, never raises. Returns whether it actually landed on the mesh."""
    return _send(_ERROR_CHANNEL, f"[{source}] {text}")
