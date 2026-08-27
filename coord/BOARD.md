# Conference Prep Fleet — BOARD
**Writer: Agent A only.** Everyone else reads. Propose changes via `coord.sh log <X> board-request`.

T0 = when Agent A stamps it here:  T0 = __________   HARD STOP = T0 + 2h30m (last 30m = rehearse)

## Demo floor (if everything else burns, this is what we show)
`prep_conference("AI Engineer NY", "<intent>")` in Claude returns a briefing built
from the **real** AI Engineer NY speaker roster. That alone is a demo. Get here first.


---
## Wiring into Claude  (S1 — VERIFIED: `claude mcp list` reports ✔ Connected)

One paste, from anywhere:

```bash
claude mcp add conference-prep -- uv run --directory /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/event-fleet python -m fleet.server
```

Confirm with `claude mcp list` -> `conference-prep: ... - ✔ Connected`. The tool then shows up
in Claude as `prep_conference(event_name, intent)`. Reload Claude Code to pick it up.

- Scope defaults to **local** (this project only). Add `-s user` to get it in every project.
- Undo / re-add: `claude mcp remove conference-prep -s local`
- The server loads `$FLEET_ROOT/.env` itself at boot, so lane keys are present even if the
  terminal that launched Claude never sourced it. No key is ever logged, only the file path.
- All server logging goes to **stderr** — stdout is the JSON-RPC channel and a stray `print()`
  in any lane module will corrupt the stream and drop the connection. Lanes: use `logging`.

**SDK GOTCHA (cost a round trip, do not re-derive):** `mcp` resolves to **2.1.1**, not 1.x.
FastMCP is gone. The v2 API is `from mcp.server import MCPServer`, `@mcp.tool()`, `mcp.run()`.
Wire-protocol fields are **snake_case** — `input_schema`, `output_schema`, `structured_content`,
`is_error`. The v1 camelCase attribute names raise `AttributeError`.

---
## PRE-VERIFIED FACTS (real calls made at bootstrap — do NOT re-derive, do NOT doubt)

**Secrets** — all three keys are live and loaded. Every terminal does:
`source /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/.env`
That file is gitignored and 600. Never read it into context, never echo a key, never commit it.

**Nebius (Agent C)** — key authenticates; 30 models on the live catalog.
The exact slug is **`openai/gpt-oss-120b`** (namespaced, not bare). C1 is closed.
`base_url="https://api.studio.nebius.com/v1/"`, OpenAI-compatible client.

**Tavily (Agent B)** — key authenticates; real search returned results.
Event entry point: **https://ai.engineer/nyc/2026** ("AI Engineer New York 2026: October 12-14").
Start your crawl there. If speakers are client-rendered, drive a real browser and read the DOM.

**Iridium (Agent D)** — Door 2 login **works and returns a real JWT + refresh token**.
GOTCHA, this cost a round trip: the key is used **RAW, with NO `irid_` prefix**.
Sending `irid_<key>` returns `401 {"detail":"Invalid API key"}`. The spec's `irid_...`
notation is illustrative, not literal. Use `$IRIDIUM_API_KEY` exactly as-is:
`POST https://api.iridiumhqmcp.com/auth/login  {"api_key": "<raw key>"}` -> 200,
`access_token` (len ~209) + `refresh_token`. D1's remaining work is `/mcp/tools/list`.

**Still unverified — these are real work, not freebies:** whether the NY speaker roster is
published yet (it may be an empty wave), which Iridium tool resolves a person, and whether
Cotal installs cleanly. Verify each yourself before claiming it.

---
## Lanes

### Agent A — Spine & Contracts  (model: opus)
Owns: `models.py`, `server.py`, run persistence, `BOARD.md`, `CONTRACTS.md`, integration.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| A1 | `models.py` committed + pushed to main | P0 | DONE (scaffolded) | B,C,D |
| A2 | MCP server boots; `prep_conference` registered; visible in Claude | P0 | DONE (S1, verified end-to-end) | demo |
| A3 | Straight-line wiring: roster -> briefing, real data, no ranking yet | P0 | TODO | H1 floor |
| A4 | Run persistence -> `run_id` on every briefing (SQLite or Supabase) | P1 | TODO | submit_eval |
| A5 | Wire C's rank + judge into the flow; thread `degradations` | P1 | TODO | H2 |
| A6 | Wire D's enrichment; Tavily-only fallback path proven | P1 | TODO | H2 |
| A7 | Render `ConferenceBriefing` -> the section-5 output format | P1 | TODO | demo |
| A8 | `submit_eval` + label prompt in tool description | P3 STRETCH | TODO | — |

### Agent B — Roster  (model: sonnet)  [Tavily prize]
Owns: `roster.py`, `data/roster_*.json`.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| B1 | Find the real AI Engineer NY speaker page — **entry point pre-verified below**, still confirm the speaker list renders | P0 | IN PROGRESS | everything |
| B2 | `fetch_roster()` via real Tavily crawl/extract -> `list[Speaker]` | P0 | TODO | A3 |
| B3 | Handle partial/wave roster + empty list without crashing; set `is_partial` | P0 | TODO | A3 |
| B4 | Pre-cache the real response to `data/` with source URL + timestamp (demo fallback) | P0 | TODO | de-risk |
| B5 | Pull event facts: dates, venue, 2–4 word description | P1 | TODO | A7 |
| B6 | Session titles per speaker where published | P2 | TODO | C |

### Agent C — Priorities & Judge  (model: opus)  [Nebius prize]
Owns: `rank.py`, `judge.py`, prompts.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| C1 | ~~Confirm gpt-oss-120b slug~~ **RESOLVED, see PRE-VERIFIED below** | P0 | DONE | — |
| C2 | Real Nebius call succeeds; paste real response in log | P0 | TODO | A5 |
| C3 | `rank()` — speakers vs intent, structured `RankedPick` out | P0 | TODO | A5 |
| C4 | `judge()` — Mechanism A, 3-check rubric, grounding check is the priority | P1 | TODO | A5 |
| C5 | Verify judge actually FAILS a deliberately ungrounded ranking | P1 | TODO | credibility |
| C6 | Kick-back retry, hard cap 2 | P3 STRETCH | TODO | — |

### Agent D — Enrichment & Mesh  (model: opus)  [Iridium + Cotal prizes]
Owns: `enrich.py`, `.cotal/agents/*.md`, mesh wiring.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| D1 | Iridium Door 2 — **login pre-verified below**, still enumerate `/mcp/tools/list` and pick the read tool | P0 | IN PROGRESS | D2 |
| D2 | `enrich_user()` — ONE real Iridium call, pre-warmed + cached for demo | P0 | TODO | A6 |
| D3 | Tavily-only fallback path, proven by forcing an Iridium failure | P0 | TODO | de-risk |
| D4 | Install Cotal; `cotal up` authed loopback; 4 agent files with default-deny ACLs | P1 | TODO | H2 theme |
| D5 | Fleet coordination visible on cotal web dashboard during a real run | P1 | TODO | demo |
| D6 | Every fleet error posts to the mesh (spec section 6) | P1 | TODO | demo |
| D7 | `enrich_speakers()` multi-speaker | P3 STRETCH | TODO | — |

---
## HELP-WANTED  (idle agents pick these up; claim by logging first)
- (empty — Agent A fills as bottlenecks appear)

## Cut list — do NOT build these
Flights agent · Mechanism B / `submit_eval` / Runtype · judge kick-back retry ·
multi-speaker enrichment · any login/user-management UI · hotels · anything GPU.

## Decisions log (Agent A appends)
- D-001: coord/ read+written at the absolute main-checkout path, not per-worktree. Zero sync latency.
- D-017: DEPLOY BEFORE STRETCH (human decision). Order is: spine works -> HTTP/SSE endpoint (H1)
  -> Cloudflare tunnel (H2) -> then Persona (X1) and/or Mitosis (X2). H1 is the shared prerequisite:
  the tunnel needs an HTTP route and Persona needs an SSE route, so one piece of work unlocks both.
  The stdio MCP transport must keep working unchanged -- it is the proven demo path.
- D-018: RESEARCH VERIFIED. Mitosis is real: openapi.json + /.well-known/mcp/server-card.json both
  200, bearerAuth, sandbox at dev.mitosislabs.ai, endpoints /api/v1/{search,jobs,batch,skills,me}.
  NO API KEY YET -- gating item. Persona is real: @runtypelabs/persona v4.20.0, MIT. cloudflared is
  NOT installed.
- D-019: DISPUTED SCOPE — the brief says Mitosis should REPLACE the judge's grounding check. Do NOT
  do that. The judge's grounding check is verified working and has caught real invented facts on
  live data; it is the strongest demo moment we have. Correct composition: Mitosis becomes the
  cited EVIDENCE STORE, and the judge keeps checking claims against it. The judge stays; only its
  evidence source changes. Replacing a working eval with an unproven integration hours before a
  demo is the wrong trade.
- D-016: COTAL PLUGIN IS DEAD BY DESIGN, NOT BROKEN — diagnosed, do not spend more time on it.
  `plugin:cotal:cotal` requires COTAL_NAME/COTAL_LINK/COTAL_AGENT_FILE in the process env, then a
  second gate (COTAL_CONTROL_SOCKET/TOKEN) that only `cotal spawn --agent claude` wires up. A
  normally-launched Claude session can never satisfy it. DO NOT claim "drive the mesh from inside
  Claude" on stage. DO claim: real mesh, minted least-privilege identities, a real broker-level ACL
  denial, a real cross-identity round trip, and the live authed web dashboard — all proven.
- D-015: HUMAN CONSTRAINT — LinkedIn throttles an account that searches too often, and losing
  enrichment would kill the best-working part of the demo. Iridium is DONE; treat it as working and
  do not refine it further. `enrich_user()` now serves the cached real profile by default (0 live
  calls); set FLEET_ENRICH_LIVE=1 only for a deliberate live capture. `enrich_speakers` budget cut
  5 -> 3. NO AGENT may run enrichment repeatedly to test. Test against the cache. Spend live
  LinkedIn calls only on the final pre-demo capture. Serving cache by policy is NOT a degradation
  and must not appear in the user-facing degradations list.
- D-012: BLOCKER FOUND BY LIVE END-TO-END RUN (dispatcher, run a73a8a9e). fetch_roster() against
  the World's Fair page returns PAGE FURNITURE, not speakers: "Supporting Partners" (with the whole
  sponsor list as its title), "Keynote", "Tuesday"/"Wednesday"/"Thursday", "PM", "New Engineer
  Orientation". The extractor's name+title-line card heuristic was only ever proven against a page
  with ZERO speakers, so its extraction path was never actually exercised. It does not invent people
  (no-stub holds), but it cannot tell a person from a calendar row. R3 is now the #1 critical path.
- D-013: Iridium enrichment VERIFIED WORKING on the live path — resolved the real user correctly
  (role, company, Iridium MCP authorship, vLLM/SGLang PRs, 12 real interests). source=iridium.
- D-014: The MCP server is a long-lived stdio subprocess. It loads lane modules ONCE at boot, so a
  fix on disk is NOT live until the server reconnects. Run a73a8a9e still hit the judge truncation
  bug that is already fixed on disk. Reconnect the server after any lane change, and ALWAYS before
  rehearsing the demo.
- D-010: HUMAN DECISION. Build and test against an event that HAS a published roster, but the
  product must work for ANY event with named people — fetch_roster stays genuinely event-agnostic
  and must NOT be ai.engineer-specific. New task R3 covers the generalization; V1 gates on it.
- D-011: The AI Engineer NY empty roster (0 confirmed speakers, Wave 2 lands Sep 1) is KEPT as a
  demonstrated EDGE CASE, not a failure. Refusing to invent speakers when a lineup is unpublished
  is a feature worth showing. Keep the cached NY artifact as proof we checked.
- D-008: HARD RULE — NEVER `git add -A` / `git add .` in the shared checkout. Stage explicit paths
  only. The dispatcher violated this and swept another lane's file into a coord commit; content was
  fine, but with live Cotal creds and a .env on disk in a PUBLIC repo, a broad add is how a secret
  eventually ships. Explicit paths, then `git diff --cached --name-only`, every time.
- D-009: Iridium uses Door 2, NOT Door 3. ChatRequest is `{"message": str}` with no tool allowlist
  or read-only mode — Door 3 would hand an LLM `delete_account` and `schedule_dm_to_person` against
  a real production LinkedIn account. Door 2 is two typed deterministic calls. Safety, not taste.
- D-005: HARD RULE, learned from S1 — stdout is the MCP JSON-RPC channel. NO `print()` in any
  module under event-fleet/src/fleet/. Use `logging` to stderr. A stray print drops the connection.
- D-006: `mcp` resolves to 2.1.1, NOT 1.x. FastMCP is gone. Use `from mcp.server import MCPServer`,
  `@mcp.tool()`, `mcp.run()`. Wire fields are snake_case (input_schema, structured_content, is_error).
- D-007: rank() returns ALL speakers ranked, not a top-N (no top_k in the contract). Render slices
  to top 3-5. Fact-less speakers stay IN the ranking (grounding-correct) but are filtered OUT of the
  rendered top-N — the judge still sees the full list. Dispatcher call, not a ranking bug.
- D-003: Keys verified at bootstrap by live call, not assumed. Findings in PRE-VERIFIED above.
- D-004: Iridium API key is used raw — the `irid_` prefix in the spec is notation, not literal.
- D-002: Lanes are vertical slices, not pipeline stages, so A/B/C/D parallelise without chaining.
