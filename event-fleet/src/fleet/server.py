"""MCP server for the Conference Prep Fleet -- the spine every lane plugs into.

Exposes one tool, `prep_conference`, whose signature and return shape are fixed by
coord/CONTRACTS.md. Every shape is imported from `fleet.models`; nothing is redefined
here and no ad-hoc dict is ever returned.

The lane modules (`fleet.roster`, `fleet.enrich`, `fleet.rank`, `fleet.judge`) are
resolved at call time against their CONTRACTS.md signatures. A lane that has not
landed, or that raises, is reported loudly -- traceback to stderr plus a
human-readable entry in the briefing's `degradations` list. Nothing is stubbed and
nothing is invented: a lane that contributes no data leaves its field empty and says
so. Errors are never silent.
"""

from __future__ import annotations

import logging
import sys
import threading
import traceback
import uuid
from importlib import import_module
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server import MCPServer

from fleet import mesh
from fleet.models import ConferenceBriefing, EvalResult, PrepRequest, UserProfile

AGENT = "S1/server"

# stdout is the JSON-RPC channel under stdio transport -- all logging goes to stderr.
logging.basicConfig(
    level=logging.INFO,
    stream=sys.stderr,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
log = logging.getLogger("fleet.server")

mcp = MCPServer(
    "conference-prep-fleet",
    version="0.1.0",
    instructions=(
        "Conference prep. Given an event name and what the user wants out of it, "
        "returns a briefing of who to meet, built from the real published roster."
    ),
)


def _summarize(args: tuple[Any, ...]) -> str:
    """Compact, log-safe rendering of a call's inputs."""
    return ", ".join(repr(a)[:120] for a in args)


def _step(module: str, func: str, degradations: list[str], *args: Any, **kwargs: Any) -> Any | None:
    """Run one lane's contract function, surfacing every failure.

    Returns the lane's value, or None if the lane has not landed or raised. Both
    cases are logged with a traceback and appended to `degradations` -- a fallback
    that fires is recorded as loudly as an outright failure.
    """
    target = f"fleet.{module}.{func}"
    try:
        fn = getattr(import_module(f"fleet.{module}"), func)
    except (ImportError, AttributeError) as exc:
        log.error(
            "agent=%s step=resolve target=%s exc=%r\n%s",
            AGENT, target, exc, traceback.format_exc(),
        )
        degradations.append(
            f"{target} is not available yet ({type(exc).__name__}: {exc}) -- "
            "that lane contributed no data to this briefing"
        )
        return None

    try:
        return fn(*args, **kwargs)
    except Exception as exc:
        log.error(
            "agent=%s step=call target=%s input=%s exc=%r\n%s",
            AGENT, target, _summarize(args), exc, traceback.format_exc(),
        )
        degradations.append(f"{target} raised {type(exc).__name__}: {exc}")
        return None


def _publish(tag: str, briefing: ConferenceBriefing, roster: int) -> None:
    """Mirror one finished run onto the Cotal mesh (spec section 6).

    One progress line saying what each lane produced, then one error line per
    degradation -- which lane, which step, why -- so the durable audit log can be
    read back after the run. `mesh.post`/`mesh.error` never raise and log their own
    failures to stderr, so a down mesh costs the audit trail and nothing else.
    """
    mesh.post(
        f"{tag} roster={roster} profile={briefing.user.source} picks={len(briefing.picks)} "
        f"confidence={briefing.evaluation.confidence:.2f} "
        f"degradations={len(briefing.degradations)}"
    )
    for line in briefing.degradations:
        mesh.error(f"{tag} {line}", AGENT)


@mcp.tool()
def prep_conference(event_name: str, intent: str) -> ConferenceBriefing:
    """Prepare for a conference: who is worth meeting there, and why.

    Args:
        event_name: The event to prep for, e.g. "AI Engineer NY".
        intent: What the user wants out of the event, in their own words --
            hiring, fundraising, a specific technical problem, partnerships.
    """
    run_id = str(uuid.uuid4())
    degradations: list[str] = []
    log.info(
        "agent=%s step=start run_id=%s event=%r intent=%r",
        AGENT, run_id, event_name, intent[:200],
    )

    roster = _step("roster", "fetch_roster", degradations, event_name)
    speakers, event_description, roster_partial = roster if roster else ([], "", False)

    user = _step("enrich", "enrich_user", degradations, intent)
    if user is None:
        # No profile source has resolved. Report an empty profile rather than invent one.
        user = UserProfile(summary="", interests=[], source="none")

    # event_name is a second disambiguation anchor: it lets the public plane confirm
    # "this speaker, at this event" and lifts confirmed coverage 10 -> 12 for free.
    enriched = _step("enrich", "enrich_speakers", degradations, speakers,
                     event_name=event_name) or []

    # The enrichment plane records its own fallbacks internally -- an Iridium failure
    # answered from public web data, a speaker it refused to guess at. Draining them
    # here is what carries them into the briefing; without it they are logged and then
    # lost, which is exactly the silent failure spec section 6 forbids.
    drain = _step("enrich", "take_degradations", degradations)
    if drain:
        degradations.extend(drain)

    picks = _step("rank", "rank", degradations, user, enriched, intent) or []
    evaluation = _step("judge", "judge", degradations, user, picks, intent)
    if evaluation is None:
        # No judge ran, so there is no confidence to report. Say exactly that.
        evaluation = EvalResult(
            confidence=0.0,
            low_confidence=True,
            notes="No judge ran for this briefing; confidence was not computed.",
        )

    briefing = ConferenceBriefing(
        run_id=run_id,
        event_name=event_name,
        event_description=event_description,
        user=user,
        picks=picks,
        evaluation=evaluation,
        roster_partial=roster_partial,
        degradations=degradations,
    )
    # Persist last, so the stored row is exactly the briefing as returned. Passing the
    # briefing's own degradations list means a storage failure is recorded on this
    # briefing rather than losing the run silently.
    _step("store", "save_run", briefing.degradations,
          PrepRequest(event_name=event_name, intent=intent), briefing)

    # Off the critical path in a thread: `cotal send` costs ~0.6s per message and can
    # block on an unreachable mesh, and the briefing must never wait on the audit log.
    # Reads `degradations`, never changes it -- other lanes render that same list.
    threading.Thread(
        target=_publish,
        args=(f"run={run_id[:8]} event={event_name!r}", briefing, len(speakers)),
    ).start()

    log.info(
        "agent=%s step=done run_id=%s picks=%d roster_partial=%s degradations=%d",
        AGENT, run_id, len(briefing.picks), briefing.roster_partial, len(degradations),
    )
    return briefing


def _load_env() -> None:
    """Give the lane modules their API keys however the server was launched.

    An MCP client spawns this process with its own environment, which may not have
    had the fleet's `.env` sourced. Never overrides a variable already set, and
    logs only the path -- never a value.
    """
    here = Path(__file__).resolve()
    for candidate in (here.parents[3] / ".env", here.parents[2] / ".env"):
        if candidate.is_file():
            load_dotenv(candidate, override=False)
            log.info("agent=%s step=env loaded=%s", AGENT, candidate)
            return
    log.warning(
        "agent=%s step=env no .env found; lane API keys must already be in the environment",
        AGENT,
    )


def main() -> None:
    _load_env()
    log.info("agent=%s step=boot transport=stdio tool=prep_conference", AGENT)
    mcp.run()


if __name__ == "__main__":
    main()
