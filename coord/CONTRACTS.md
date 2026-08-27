# Interface Contracts (Agent A owns this file — everyone else reads it)

All shapes live in `event-fleet/src/fleet/models.py`. Import them. Never redefine.

## Boundaries — build against these signatures from minute zero
```python
# Agent B  -- event-fleet/src/fleet/roster.py
def fetch_roster(event_name: str) -> tuple[list[Speaker], str, bool]:
    """Returns (speakers, event_description, is_partial).
    Real Tavily crawl of the public AI Engineer NY page.
    MUST tolerate a short/empty wave-published roster without raising."""

# Agent D  -- event-fleet/src/fleet/enrich.py
def enrich_user(hint: str | None = None) -> UserProfile: ...
def enrich_speakers(speakers: list[Speaker], limit: int = 5) -> list[EnrichedSpeaker]: ...
    """Iridium Door 2 first; Tavily fallback. A fallback is a logged degradation."""

# Agent C  -- event-fleet/src/fleet/rank.py
def rank(user: UserProfile, speakers: list[EnrichedSpeaker], intent: str) -> list[RankedPick]: ...

# Agent C  -- event-fleet/src/fleet/judge.py
def judge(user: UserProfile, picks: list[RankedPick], intent: str) -> EvalResult: ...
    """Mechanism A. Rubric: (a) pick matches stated goal, (b) EVERY cited fact is
    present in the enrichment output (grounding check -- this is the main job),
    (c) >= 3 valid picks. Scoring only; kick-back retry is stretch, hard cap 2."""

# Agent A  -- event-fleet/src/fleet/server.py
prep_conference(event_name: str, intent: str) -> ConferenceBriefing
submit_eval(feedback: RunFeedback, run_id: str | None = None) -> EvalAck
```

## Degradation contract
Every function above returns a usable value or raises. It never returns silently
empty. If it degrades to a fallback it appends a human-readable string to the
`degradations` list the server threads through to `ConferenceBriefing`.

## Change process
Need a signature change? Post to `coord.sh log <X> contract-request "<proposal>"`
and DM Agent A on the board. Agent A edits this file and `models.py`. You do not
edit either. This is the one hard serialization point in the fleet.
