# Conference Prep Fleet

A fleet of coordinating agents that preps you for a conference you're about to attend.
Give it an event name and why you're going; it pulls the real speaker roster, works out
who you are, ranks who's worth meeting against your goal, evaluates its own ranking,
and hands back a briefing.

- `event-fleet/` — the Python MCP server and agent modules
- `coord/` — the fleet coordination bus (PROTOCOL, BOARD, CONTRACTS, per-agent status)
- `scripts/` — `coord.sh` (fleet bus), `hitl.py` (Telegram human-in-the-loop bridge)
- `META_PROMPTS.md` — launch prompts, one per agent terminal

Start here: `coord/PROTOCOL.md`, then your lane in `coord/BOARD.md`.
