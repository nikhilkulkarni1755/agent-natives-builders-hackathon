# FINAL_SUBMISSION.md — Conference Prep Fleet

Paste-ready values for `ic_hack_submit` (`eid: anb-hack-01`). Every claim below traces to
something on disk; see **Evidence** at the bottom. Submission is idempotent per team and
**overwrites the whole record**, so send every field each time.

---

## `title`
```
Conference Prep Fleet
```

## `repo_url`
```
https://github.com/nikhilkulkarni1755/agent-natives-builders-hackathon
```

## `demo_url`
```
https://events.iridiumhqmcp.com
```
Live, judge-reachable, on our own domain. `GET /health` answers with no auth.
**This field is the "it runs" gate** — an empty one leans the whole gate on the stage demo.

## `blurb`
```
Point it at a conference, say what you want out of it, and it returns a ranked briefing of
who to meet and why. It pulls the real published speaker roster, resolves who you are, ranks
people against your stated goal, and audits its own ranking for invented facts before it
answers. It is event-agnostic: the same code path returns real speakers from ai.engineer,
us.pycon.org and kccnceu2026.sched.com with no per-site handling. When a lineup is not
published yet it returns nobody, rather than inventing names to fill the page -- and a second
model independently scores every claim against the evidence that produced it. Public endpoint,
no API key required to try it.
```

## `agent_surface`  ← the field the rubric scores
```
MCP server, reachable two ways. `prep_conference(event_name, intent) -> ConferenceBriefing` is registered over stdio (`claude mcp add`, health-checked Connected), and the same pipeline is exposed to remote agents over HTTP and SSE at https://events.iridiumhqmcp.com -- a named Cloudflare tunnel on our own zone. Routes: /health, /prep (JSON), /prep/stream (SSE). The stream republishes the lanes' own structured logs as per-stage progress -- 55 events across a 30s run, first at 1.9s -- so a calling agent renders work in flight instead of blocking. Persona (Runtype's open-source agent UI) consumes that stream unchanged. No key needed; per-caller limits use CF-Connecting-IP.

Machine-to-machine identity enforced by a broker, not by application code. The fleet coordinates over a Cotal NATS mesh in authenticated mode; each agent holds its own minted least-privilege credential with an explicit default-deny subject ACL. An agent publishing outside its grant is refused by nats-server itself -- reproduced twice, captured verbatim: `Permissions Violation for Publish to "...fleet.judge-only"`. Every run writes its per-lane outcome and every error to a durable replayable audit log, verified by forcing a real 401 and watching the error count move. Caller identity is request-scoped: a remote agent is never answered with the operator's identity.

Grounding as a surface, not a promise. Extraction is discover -> extract -> validate, and validation requires every returned name to appear verbatim in the source, making a fabricated speaker structurally impossible. A second model audits the ranking against the evidence that produced it, scoring 0-1 with per-check pass/fail; it has caught our own ranker inventing facts on live data. 24 events across 12 domains verified by real fetch. Unsupported sources fail closed in 0.0s -- Luma, Meetup and Partiful are refused by name, because a Luma page returns its RSVP guest list and an extractor will mistake it for a line-up.
```

---

## Sponsor use — what each one actually does here

| Sponsor | How it is used | Evidence |
|---|---|---|
| **Tavily** | Roster discovery + extraction, and the public-data enrichment plane. Real crawl/extract against conference sites; also the fallback when identity resolution declines to guess. | 23 cached real rosters in `event-fleet/data/roster/` |
| **Nebius** | Token Factory serverless inference, `openai/gpt-oss-120b`, for both ranking and the grounding judge. No GPU is rented anywhere. | `rank.py`, `judge.py`; 51 persisted runs |
| **Cotal** | The coordination plane. Authenticated NATS mesh, per-agent minted identities, default-deny ACLs enforced by the broker, one durable ordered audit log, live authed dashboard. | `Permissions Violation` in `.cotal/nats.log`; `fleet.errors` 3→4 under forced failure |
| **Iridium** | Attendee identity resolution via Door 2, so picks are ranked against who the user actually is. Optional for public callers, and never shared across callers. | `iridium.py`, `enrich.py`; request-scoped identity |
| **Runtype (Persona)** | The front end. Persona 4.20.0 consumes our SSE stream directly — stage rail, live fleet log, streamed briefing. | `web/index.html`, verified in a real browser |
| **Mitosis** | Cited evidence client (`cortex_ask` / `cortex_remember`), returning facts carrying a `[mitosis:<universal_id>]` receipt in the exact shape the judge audits. Composed with the judge, not replacing it. | `mitosis.py`; public tools verified with zero auth |

## Anticipated judge questions
- **"Does it work for conferences you didn't hard-code?"** Yes — verified live on `us.pycon.org`
  and `kccnceu2026.sched.com`, neither related to the demo event, same code path, no domain checks.
- **"What happens when it doesn't know?"** It returns nobody and says so. AI Engineer NY's 2026
  lineup is not published; we return zero speakers rather than the "past speakers" list on the page.
- **"How do you know it isn't making things up?"** Two independent mechanisms: names must appear
  verbatim in the source, and a second model audits every cited claim and caps confidence when it fails.
- **"Can I try it?"** `curl https://events.iridiumhqmcp.com/health`, then POST `/prep`. No key.

## Evidence
`VERIFICATION.md` (independent audit, incl. what was found broken), `DEMO.md` (script + evidence
index), `DEPLOY.md` (tunnel + SSE-through-proxy findings), `PUBLIC.md` (stranger onboarding),
`EVENTS.md` (24 events verified by real fetch), `coord/BOARD.md` (32 recorded decisions),
`coord/log/*.jsonl` (per-task evidence). 60 commits.

## Known limits, stated plainly
Luma / Meetup / Partiful are unsupported by design. Event names must be given as the organiser
writes them, year included. Rosters publish in waves, so a partial list is labelled partial.
A cold event costs a 17-44s crawl; a captured one is near-instant.
