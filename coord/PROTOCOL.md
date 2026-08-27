# Fleet Coordination Protocol

**Every agent reads this file at the top of every loop iteration. It is the law.**

`FLEET_ROOT=/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1`

## Why coord/ is not in your worktree
Code work happens in **your own worktree** (isolated, atomic commits, no collisions).
Coordination happens at the **absolute path** `$FLEET_ROOT/coord` — the main checkout,
shared by all agents, zero sync latency. Never `git pull` to see another agent's status;
just read the file. Never edit another agent's status file.

## THIS REPO IS PUBLIC
`agent-natives-builders-hackathon` is a **public** GitHub repo. Before every push:
- Never commit `.env`, `token.txt`, any `irid_...` / `sk-...` / bot token, or a JWT.
- `token.txt` at repo root is an Immersive Commons token. It is gitignored. Leave it alone.
- Secrets live in your shell env only. Never paste one into a source file, a log line,
  a coord status, or a Cotal agent file.
- Run `git diff --cached --name-only` before committing. If you see a secret, stop.

## Ownership map (violating this causes merge conflicts — don't)
| Path | Writer | Readers |
|---|---|---|
| `coord/BOARD.md` | **Agent A only** | all |
| `coord/status/<X>.md` | Agent X only | all |
| `coord/log/<X>.jsonl` | Agent X only (append) | all |
| `coord/hitl/pending/*.md` | any agent (own files) | human |
| `coord/hitl/answered/*.md` | HITL poller | all |
| `coord/CONTRACTS.md` | **Agent A only** | all |

## The three commands you use
```bash
$FLEET_ROOT/scripts/coord.sh status <A|B|C|D> "<one-line state>" "<what's next>"
$FLEET_ROOT/scripts/coord.sh log    <A|B|C|D> <event> "<detail>"
$FLEET_ROOT/scripts/coord.sh read                       # whole board + all statuses
```

## HITL rule
A decision is **HITL** only if it is genuinely the human's to make: a secret you
don't have, a paid/irreversible action, a product-scope call, or an account login.
Everything else you decide yourself and log.

```bash
$FLEET_ROOT/scripts/hitl.py ask <A|B|C|D> "<question>" "<option1>" "<option2>" ...
$FLEET_ROOT/scripts/hitl.py check <A|B|C|D>   # -> prints answer or NO_ANSWER_YET
```

`ask` sends to Telegram and returns **immediately**. You do **not** block.
Log the question, move to the next unblocked task in your lane, and `check` on
each subsequent heartbeat. If still unanswered after 20 minutes, take the option
you recommended, log `assumed_default`, and keep shipping.

## Ralph loop (every agent, every heartbeat — verification is ALWAYS last)
1. **Read** — `coord.sh read`; check HITL answers; re-read your lane in BOARD.md.
2. **Select** — highest-priority *unblocked* task in your lane. If your lane is
   empty, go to IDLE (below). Never take another agent's lane without posting first.
3. **Research** — before writing code for a new integration, get ground truth:
   real docs, real endpoints, real payloads (WebFetch/WebSearch/Tavily/CDP/browser).
   Never guess an API shape. Never invent a model slug, URL, or field name.
4. **Implement** — the *smallest deployable slice*. One feature that works end to
   end beats four that are half-wired.
5. **Commit** — atomic, in your worktree, on your branch, then push.
6. **VERIFY — LAST STEP, NON-NEGOTIABLE.** Run the thing for real against the real
   service and paste real output into your log. A task is not done until verified.
7. **Report** — `coord.sh status`; `coord.sh log`.

## NO STUB DATA
No mock speakers, no `return {"name": "John Doe"}`, no hardcoded rankings, no
fake confidence scores, no `TODO: call the real API`. If the real call is not
working yet, the feature is **not done** — say so in your status and keep working
on it. The one permitted cache is a **real response captured from a real call**,
saved to `event-fleet/data/` with the timestamp and source URL recorded, used as
a demo fallback. That is a cache, not a stub.

## Errors are never silent (spec section 6)
Every error: structured log -> `coord.sh log <X> error "<agent/step/why>"` -> and
once the mesh is up, post to Cotal. No bare `except: pass`. A fallback that fires
is a **degradation** and gets logged as loudly as a failure.

## IDLE state — you stay alive
When your lane is clear you do **not** exit. On each heartbeat:
1. `coord.sh read` — did another agent just unblock work for you?
2. Check `coord/hitl/answered/` for answers to your questions.
3. Pick up any task in BOARD.md marked `HELP-WANTED`.
4. Otherwise: harden. Add a real-call verification, tighten an error path,
   pre-warm a demo fallback, shave latency. Log what you hardened.
5. Post `IDLE` status with what you are watching for. Then wait for the next tick.

## Deploy discipline
Smallest deployable item, feature by feature. Never build a chain of four things
that only works when all four land. Every commit should leave `main` demoable.
