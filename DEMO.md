# DEMO.md — Conference Prep Fleet

**Rule for this file: every line on stage traces to something captured on disk.**
The evidence index at the bottom maps each claim to the file that proves it. If a claim
isn't in that index, it isn't in the script.

---

## 0. PRE-FLIGHT — run this 10 minutes before you go on

```bash
source /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/.env
```

1. **RECONNECT THE MCP SERVER.** Quit and relaunch Claude Code. The server is a long-lived
   stdio subprocess that loads the lane modules **once at boot** — a fix on disk is not live
   until it reconnects (D-014). Run a73a8a9e died on stage-adjacent because of exactly this.
2. `claude mcp list` → must print `conference-prep: ... - ✔ Connected`. Ignore the
   `plugin:cotal:cotal ✘ Failed to connect` line; it is dead by design (D-016) and is not
   part of the demo.
3. `cotal status` **from `event-fleet/`** → `mode auth`, `connection ok`, `web running`,
   and a `channels fleet.errors(N), fleet.progress(N)` line. Note the two numbers; you want
   them to *grow* on stage.
4. **Log into the dashboard now, not on stage.** `http://cotal.localhost:7799/` returns
   `401` without a session. If the browser tab is logged out, re-run
   `cotal web --detach --no-open` from `event-fleet/` and click the printed one-time link
   **before** you walk up.
5. `echo "FLEET_ENRICH_LIVE=[$FLEET_ENRICH_LIVE]"` → must be **empty**. Setting it to `1`
   burns live LinkedIn reads and an over-searching account gets throttled — that would kill
   the single best-working part of the demo (D-015). The cached real profile is served by
   default and that is **policy, not a degradation**.

Then do exactly one warm-up run of the script end to end. Do not do a second one.

---

## 1. SCREEN LAYOUT

| Screen / window | What's on it | State before you start |
|---|---|---|
| **Front — Claude Code** | the prompt already typed, **not** sent | cursor in the box |
| **Terminal** | `cd event-fleet`, `.env` sourced, cleared | blank prompt |
| **Browser tab** | `http://cotal.localhost:7799/` | **already logged in**, channels view |

Front screen is Claude. You will switch to terminal once, browser once, and back to Claude
to land. Three moves, no more.

---

## 2. THE SCRIPT (≈85 seconds)

The pipeline takes roughly 40–70s. **You fire it in the first five seconds and talk over it.**
Never stand in silence waiting for a model.

> **[0:00 — Claude, prompt already typed. Hit enter as you say the first line.]**
>
> "I showed up to this hackathon knowing nobody. So I built the thing that fixes that.
>
> That's an MCP server I just called from inside Claude. Point it at any conference, tell it
> what you actually want out of it, and it tells you who to meet and why."

> **[0:12 — switch to terminal. Run the ACL command.]**
>
> "Five agents built this in parallel, coordinating over a real authenticated message mesh —
> each one holding its own least-privilege identity. Here's the roster agent trying to publish
> to a channel it was never granted."
>
> *(the denial appears)*
>
> "That's the broker refusing it. Default-deny, enforced by NATS itself at the subject level —
> not by a check in my code."

> **[0:35 — switch to browser.]**
>
> "And that's the live audit log. Every lane's progress, every error, durable and replayable.
> Authenticated — it 401s without a session."

> **[0:45 — back to Claude. The result is in.]**
>
> "There it is. Real named speakers, real session titles, and a reason for each one grounded in
> what the conference page actually says. It knows who *I* am because it resolved my own
> LinkedIn through Iridium — which is my product, running inside my own fleet."

> **[1:05 — the closer. This is the line that wins it. Slow down.]**
>
> "And the best part is this one. A second model audits the ranking for invented facts. It just
> flagged one of my *own* picks as ungrounded and dropped confidence to 0.4. When I point this
> at AI Engineer New York — whose lineup isn't published yet — it returns nobody, instead of
> inventing five people to fill the slide.
>
> It refuses to make things up. Most demos up here can't say that."

**If confidence comes back high instead of low, the line is:**
> "…and the judge signed off at 1.0 — every claim traceable to the page. It has teeth: I'll
> show you it failing a deliberately fabricated ranking in ten seconds."
> *(then run the pytest command from §4, Beat 5)*

Either verdict gives you a line. There is no bad roll.

---

## 3. THE HONEST VERSION (use if roster extraction regresses)

R3 landed and the roster is returning real people — verified live in runs `6a554a6f` and
`42a889db` (25 real speakers, real session titles, `degradations: []`). But if on the day the
extractor comes back with page furniture, **do not improvise around it — say it:**

> "Before R3, this returned 'Tuesday' and 'Supporting Partners' as people to meet — a regex
> can't tell a person from a calendar row. So extraction moved to a model with a hard
> verbatim-source check: a name that doesn't appear literally in the fetched document is
> dropped. That makes inventing a speaker structurally impossible rather than just discouraged.
> Here's the run where it works."

Then replay the stored run (§5, Fallback A). Owning a caught bug and showing the fix reads as
engineering. Pretending it never happened reads as a demo that will break in production.

---

## 4. EXACT COMMANDS, IN ORDER

**Beat 1 — Claude (front screen).** Type this beforehand; hit enter on your first sentence:

```
Use prep_conference for "AI Engineer Europe 2026" — I want to meet people building
MCP and agent infrastructure.
```

**Beat 2 — terminal, the ACL denial.** From `event-fleet/`:

```bash
cotal send --creds .cotal/auth/creds/roster_agent.creds msg fleet.judge-only "demo probe"
```
Expected: non-zero exit, `NATS permission denied ... cannot publish
cotal.main.chat.local.<pubkey>.fleet.judge-only`.

*Safer variant — show the broker's own log instead of firing a live send. This is already on
disk and cannot fail:*
```bash
grep "Publish Violation" .cotal/nats.log | tail -2
```

**Beat 3 — browser.** Switch to the already-logged-in dashboard, channels view. Point at
`fleet.progress` / `fleet.errors` and their durable counts. Do not click around.

**Beat 4 — Claude.** Scroll to the picks and the `evaluation` block. Read one pick's `reason`
out loud and the `confidence` number.

**Beat 5 — the judge, only if you're running long or need the high-confidence branch:**
```bash
uv run --directory /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/event-fleet \
  --with pytest python -m pytest tests/test_judge.py -v -k "grounded"
```
14–25s, real Nebius calls, no mocks. The two test names that print on screen are the pitch:
`test_grounded_ranking_passes_with_enough_coverage PASSED` and
`test_ungrounded_ranking_fails_and_caps_confidence PASSED`.

---

## 5. FALLBACKS — assume the wifi is bad and one API is down

Every fallback below reads from disk. None needs a network.

### Fallback A — the live call hangs, errors, or Tavily/Nebius is down
**Say:** "Network's fighting me — here's the same run from ninety minutes ago, out of the run
store. Every run gets a `run_id` and is persisted."
```bash
python3 -c "
import sqlite3, json
c = sqlite3.connect('/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/event-fleet/data/runs.db')
rid, br = next(c.execute('select run_id, briefing from runs order by created_at desc limit 1'))
d = json.loads(br)
print(f\"run {rid} | {d['event_name']} | confidence {d['evaluation']['confidence']} | degradations {len(d['degradations'])}\")
print(d['event_description'], '\n')
for p in d['picks'][:5]:
    s = p['speaker']['speaker']
    print(f\"{p['rank']}. {s['name']} -- {s.get('title') or ''} {('@ '+s['company']) if s.get('company') else ''}\")
    print(f\"   session: {s.get('session_title')}\")
    print(f\"   why: {p['reason']}\n\")
print('judge:', d['evaluation'].get('notes'))
"
```
This is not a mock — it is the real stored output of a real run, and the `run_id` is the proof.
**Persistence stops being a boring feature the moment it saves the demo. Say that out loud.**

### Fallback B — the call returns but with zero picks
**Do not treat this as a failure — it is the best story you have.** Say:
> "That's the empty-roster case, and it's deliberate. AI Engineer New York's lineup isn't out
> until Wave 2. It checked, found no confirmed speakers, and returned none. The twelve names on
> that page are labelled past speakers from other events, and it excluded them. It would rather
> hand you nothing than hand you people who aren't going."

Then pivot straight to Fallback A to show a populated run.

### Fallback C — Iridium / LinkedIn is down or slow
Nothing to do. `enrich_user()` serves `data/iridium_user_profile.json` by default and makes
zero live calls. If asked: "the profile is cached by policy, not because anything failed —
LinkedIn throttles accounts that search too often, and the cache is a real capture, timestamped
in the file." **Never set `FLEET_ENRICH_LIVE=1` to prove it live.** Show the file instead:
```bash
python3 -c "
import json; d=json.load(open('/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/event-fleet/data/iridium_user_profile.json'))
print('captured_at:', d['captured_at']); print('endpoint:', d['endpoint'])
p=d['profile']; print(p['name'], '|', p['current_role']['title'], '@', p['current_role']['company'])
print(p['headline'])"
```

### Fallback D — Nebius is down (no ranking, no judge)
The briefing still returns with the real roster, and `degradations` names the exact failure.
**Say:** "That list is the honest degradation report. Every lane that fails says so, in the
output the user reads — nothing is silently swallowed." A system that reports its own failure
in the user-facing payload is a stronger claim than one that happened to succeed.

### Fallback E — the cotal dashboard 401s or the browser session died
Skip the browser entirely; the terminal has the same evidence:
```bash
cotal status | grep -A1 channels     # durable channel counts
grep "Publish Violation" .cotal/nats.log | tail -2
```
**Say:** "It 401s because it's authenticated — the mesh was never brought up with `--open`.
Here's the same audit state from the CLI."

### Fallback F — Claude Code itself won't reconnect the MCP server
Run the pipeline over stdio from the terminal and narrate it as the same code path — because
it is: the MCP tool is a thin wrapper over `prep_conference`. Then use Fallback A for output.

### Fallback G — the judge test flakes
`gpt-oss-120b` occasionally truncates (`finish_reason=length`); P3 saw this in 1 of 5 full-suite
runs. If it raises `JudgeError`: **"that's a real model truncation, and note that it raised
rather than silently scoring 1.0 — a judge that fails open is worse than no judge."** Then
re-run once. Prefer `-k "grounded"` (2 tests, ~14–25s) over the full suite on stage.

---

## 6. ANTICIPATED JUDGE QUESTIONS — honest answers only

**"Does it work for other conferences, or just this one?"**
Yes — and it's the same code path, no per-site special cases, no domain checks. It's been run
live against three different events: AI Engineer Europe 2026 (25 real speakers with sessions),
AI Engineer World's Fair, and AI Engineer New York. *Honest caveat, volunteer it:* they're all
`ai.engineer` properties, so I've proven it's event-agnostic, not yet that it's
**domain**-agnostic across arbitrary conference site layouts. Discovery goes through Tavily
search and the `llms.txt` convention, so structurally there's nothing site-specific — but I'd
want a run against a non-`ai.engineer` domain before claiming that on stage.

**"What happens when it doesn't know something?"**
It says so, in three separate places. Unpublished roster → zero speakers and an explicit
"nobody has been invented to fill this section." Lane failure → a plain-English entry in
`degradations` in the payload the user reads. Unverifiable claim in a ranking → the judge fails
grounding and caps confidence. The design rule is that absence is reported, never filled in.

**"How do you know the model isn't making the speakers up?"**
Two independent mechanisms. Extraction validates every name against the source: it must survive
a shape check and appear **verbatim** in the fetched document, so a hallucinated name is dropped
structurally rather than talked out of. And the judge re-reads every ranking looking for claims
not supported by the evidence. It caught a fabricated NASA Mars-rover role and a fabricated CUDA
kernel spec by name in the regression suite, and it flagged one of my own real picks live today.

**"Is the multi-agent mesh real, or is it a diagram?"**
Real and running. Authenticated NATS (`mode auth`, never `--open`), three minted least-privilege
identities, a broker-level publish denial in `nats.log` reproduced twice, a cross-identity round
trip including role anycast, and a live authenticated web dashboard whose durable message counts
grow during a run. *Honest caveat:* the Cotal Claude **plugin** doesn't work — it needs env vars
only a `cotal spawn`-launched session gets, so it can never work from a normally-launched Claude.
Diagnosed, not a mystery. I drive the mesh from the fleet code and the CLI, both of which you
just saw.

**"Why not just ask Claude who to meet?"**
Because Claude will confidently name speakers for a lineup that hasn't been published. The whole
system is the machinery that stops that: real fetch, verbatim validation, and an independent
grounding audit on top.

**"Is the judge just rubber-stamping?"**
No, and there's a committed regression test that fails if it ever starts. Grounded ranking →
confidence 0.9, `facts_grounded: true`. Deliberately fabricated ranking → 0.25, `facts_grounded:
false`, and it names both invented claims in its notes. Real model calls, no mocks. I can run it
in 15 seconds.

**"What did you cut?"**
The judge kick-back retry loop (scoring only — a deliberate scope decision, not a failure),
`submit_eval`, and multi-speaker enrichment beyond a budget of 3. I'd rather show four things
working than eight things half-wired.

**"How long did this take / how many agents?"**
Roughly two and a half hours, five lanes in parallel on a dependency graph. Every decision that
cost a round trip is in `coord/BOARD.md` as a numbered decision — D-001 through D-016.

---

## 7. DO NOT SAY THESE

- ❌ "We drive the mesh from inside Claude." The Cotal plugin does not work (D-016). Say
  *fleet code, CLI, and dashboard* — all three are real.
- ❌ "The judge fixes bad rankings." It **scores**; the kick-back retry was never built.
- ❌ "It enriches every speaker off LinkedIn." Budget is 3, and in the last run the top picks
  resolved via **Tavily public data**, not Iridium. Iridium resolved **you**; Tavily corroborates
  **them**. That's the accurate sentence.
- ❌ "It works on any conference website." Say **event-agnostic** and volunteer the
  domain caveat (§6).
- ❌ Anything about `submit_eval` or eval submission. Cut.
- ❌ Do not run enrichment twice to "prove it's live" (D-015).

---

## 8. EVIDENCE INDEX — claim → proof on disk

| Claim in the script | Where it's proven |
|---|---|
| MCP server live in Claude, `prep_conference` registered | `claude mcp list` → ✔ Connected; `coord/log/S1.jsonl` |
| Real speakers, real sessions, real reasons | `event-fleet/data/runs.db` runs `6a554a6f`, `42a889db` (25 picks, `degradations: []`) |
| Iridium resolved the real user off the live API | `coord/log/E2.jsonl`; BOARD D-013; `data/iridium_user_profile.json` (role, company, Iridium MCP authorship, vLLM/SGLang PRs, 12 interests) |
| Cached profile is policy, not failure | BOARD D-015; `enrich.py` `FLEET_ENRICH_LIVE` gate; run `42a889db` `user.source: iridium-cache` with `degradations: []` |
| Nebius `openai/gpt-oss-120b`, ~3.5s at 5 speakers | `coord/log/P1.jsonl` (3.47s @5, 7.66s @40) |
| Judge discriminates 0.9 vs 0.25, names both fabrications | `coord/log/P3.jsonl`; `event-fleet/tests/test_judge.py` (fake NASA Mars-rover role, fake CUDA kernel) |
| Judge flagged a real pick live and capped confidence at 0.4 | runs.db `42a889db` → `"UNGROUNDED CLAIMS DETECTED -- Amy Boyd..."`, `facts_grounded: false` |
| Broker-level ACL denial, default-deny | `event-fleet/.cotal/nats.log` `Publish Violation ... fleet.judge-only`; `coord/log/M2.jsonl` (reproduced 2x) |
| Authed mesh, not `--open` | `cotal status` → `mode auth`; `coord/log/M1.jsonl` |
| Cross-identity round trip incl. anycast | `coord/log/M2.jsonl` `roundtrip` |
| Live authed dashboard, durable counts grow | `coord/log/M2.jsonl` `dashboard` (SSE verified); `http://cotal.localhost:7799/` → 401 unauthenticated |
| Errors post to the durable audit log | `coord/log/M2.jsonl` `M3-verify` (`fleet.errors` 1→2) |
| Every run persisted with a `run_id` | `event-fleet/data/runs.db`, 4+ distinct runs; `coord/log/S2.jsonl` |
| Empty roster returns nobody, past speakers excluded | `coord/log/R1.jsonl`; BOARD D-011; runs `cee6a58b`, `b84dcd3d` (0 picks) |
| Extraction can't invent a name | `roster.py` module docstring: verbatim-source check + shape check |
| Failures surface in the user-facing payload | run `a73a8a9e` `degradations` (judge truncation, named in full) |

---

## 9. CLAIMS SOFTENED OR CUT (read this before Q&A)

- **"Works for any conference"** → softened to **event-agnostic**. All three live runs were
  `ai.engineer` properties. R3's own criterion (two conferences on different domains) is not yet
  evidenced on disk.
- **"Every speaker enriched from LinkedIn"** → cut. Budget is 3 and the last run's top picks
  resolved `source: tavily`, not `iridium`.
- **"Judge corrects bad rankings"** → cut. Scoring only.
- **"Drive the mesh from Claude"** → cut entirely (D-016).
- **"Anycast visible on the dashboard"** → cut. Anycast is point-to-point to a role queue and
  does not appear on the console; the dashboard honestly shows presence and channel traffic only.
- **Live ACL re-fire not personally re-verified while writing this** — the send was blocked by a
  local permission classifier. The denial is on disk verbatim in `nats.log` and was reproduced
  twice by M2, which is why §4 Beat 2 offers the `grep` on the broker log as the safer variant.
- **Rendered briefing format** — `render.py` exists but is not wired into `server.py` yet, so
  Claude currently shows the structured briefing rather than the prose format. Do not promise a
  formatted briefing; show the structured output, which reads fine.
