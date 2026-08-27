# SUBMISSION_FORMAT.md — `ic_hack_submit`

Structure of the submission filed for **anb-hack-01** on 2026-08-27, kept for reuse.

## Call

MCP server: `https://www.immersivecommons.com/api/mcp`
Tool: `ic_hack_submit`
Scope: `hack:submit` — **plus you must be on a team**. No team, no submission.

Preconditions, in order (each gates the next):

1. `ic_hack_register` → seat on the roster
2. `ic_hack_team_create` → team (submission is keyed by `team_id`, not by person)
3. `ic_hack_submit`

## Schema

| Field | Type | Max | Notes |
|---|---|---|---|
| `eid` | string | 64 | Event id, e.g. `anb-hack-01` |
| `title` | string | 140 | |
| `blurb` | string | 2000 | One paragraph: what it is |
| `repo_url` | string | 2048 | |
| `demo_url` | string | 2048 | Omit rather than pass a localhost URL |
| `agent_surface` | string | 2000 | **The field the rubric scores** |
| `folder_id` | string | 64 | Vault folder (`d_...`) with slides/video |

No field is required by the schema — a partial submission is legal, which is what makes the
early-stub-then-overwrite pattern work. There is **no `track` field**; track is decided
outside this call.

## Behaviour

- **Idempotent per team.** One submission per team; calling again **overwrites the whole
  record**. Nothing is appended, there is no history. Send every field each time — a field
  omitted on a later call is not preserved from the earlier one.
- **Locks at 15:00 PDT day two.** After the organizer locks, the record freezes and further
  calls return `locked`. Response carries `locked: false` while still open.
- Returns the full stored `submission` plus `submitted_at` / `updated_at`.

## Payload sent

```json
{
  "eid": "anb-hack-01",
  "title": "Conference Prep Fleet",
  "blurb": "Point it at a conference, say what you want out of it, and it returns a ranked briefing of who to meet and why. It pulls the real published roster, resolves who you are, ranks people against your goal, and audits its own ranking for invented facts before it answers. Event-agnostic; every verified run to date is on ai.engineer properties. When a lineup isn't published yet it returns nobody rather than inventing names to fill the slide.",
  "repo_url": "https://github.com/nikhilkulkarni1755/agent-natives-builders-hackathon",
  "agent_surface": "MCP server. `prep_conference` is registered and callable ... (full text in the stored record)"
}
```

`demo_url` deliberately omitted — the dashboard was `http://cotal.localhost:7799/`, which a
judge cannot reach.

## Writing `agent_surface`

This is the 30-point band. Rule from the tool's own description: **name the surface, do not
re-describe the product.** Named surfaces that count — MCP server, `ai-agent.json`, A2A
endpoint, machine-to-machine auth, agent payments.

The structure that worked was three paragraphs, one per surface:

1. **The callable surface** — MCP server, tool name, transports (stdio + HTTP/SSE), i.e. how a
   *remote* agent reaches it.
2. **Machine-to-machine auth** — per-agent least-privilege identity, default-deny subject ACLs
   enforced by the broker rather than by application code, authenticated audit log.
3. **Grounding as a surface** — verbatim-source check making fabrication structurally
   impossible; a second model scoring for ungrounded claims.

Every claim traced to something on disk (`DEMO.md` §8 evidence index). Claims that could not be
evidenced were cut, per `DEMO.md` §9.

## Gotchas

- **"It runs" is a gate, not just its 25 points.** A submission judges cannot trigger live
  cannot place at all, whatever the other 75 score. `demo_url` is the field that carries this —
  an empty one leans the whole gate on the live stage demo.
- Rankings use **mean** judge score, not sum.
- Submit a stub the moment a team exists. An unsubmitted finished project scores zero; an ugly
  stub is overwritable until 15:00.
- Business failures return **HTTP 200 with `ok: false`** in the body. Read the envelope, not the
  status code.
- Backticks and newlines in `agent_surface` survive fine, but build the JSON with a real
  serializer rather than shell interpolation — write the payload to a file, then send the file.
