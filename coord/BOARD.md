# Conference Prep Fleet — BOARD
**Writer: Agent A only.** Everyone else reads. Propose changes via `coord.sh log <X> board-request`.

T0 = when Agent A stamps it here:  T0 = __________   HARD STOP = T0 + 2h30m (last 30m = rehearse)

## Demo floor (if everything else burns, this is what we show)
`prep_conference("AI Engineer NY", "<intent>")` in Claude returns a briefing built
from the **real** AI Engineer NY speaker roster. That alone is a demo. Get here first.

---
## Lanes

### Agent A — Spine & Contracts  (model: opus)
Owns: `models.py`, `server.py`, run persistence, `BOARD.md`, `CONTRACTS.md`, integration.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| A1 | `models.py` committed + pushed to main | P0 | DONE (scaffolded) | B,C,D |
| A2 | MCP server boots; `prep_conference` registered; visible in Claude | P0 | TODO | demo |
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
| B1 | Deep research: find the real AI Engineer NY (Oct 12–14 2026) speaker page | P0 | TODO | everything |
| B2 | `fetch_roster()` via real Tavily crawl/extract -> `list[Speaker]` | P0 | TODO | A3 |
| B3 | Handle partial/wave roster + empty list without crashing; set `is_partial` | P0 | TODO | A3 |
| B4 | Pre-cache the real response to `data/` with source URL + timestamp (demo fallback) | P0 | TODO | de-risk |
| B5 | Pull event facts: dates, venue, 2–4 word description | P1 | TODO | A7 |
| B6 | Session titles per speaker where published | P2 | TODO | C |

### Agent C — Priorities & Judge  (model: opus)  [Nebius prize]
Owns: `rank.py`, `judge.py`, prompts.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| C1 | Deep research: confirm the exact gpt-oss-120b slug on the live Token Factory catalog | P0 | TODO | C2 |
| C2 | Real Nebius call succeeds; paste real response in log | P0 | TODO | A5 |
| C3 | `rank()` — speakers vs intent, structured `RankedPick` out | P0 | TODO | A5 |
| C4 | `judge()` — Mechanism A, 3-check rubric, grounding check is the priority | P1 | TODO | A5 |
| C5 | Verify judge actually FAILS a deliberately ungrounded ranking | P1 | TODO | credibility |
| C6 | Kick-back retry, hard cap 2 | P3 STRETCH | TODO | — |

### Agent D — Enrichment & Mesh  (model: opus)  [Iridium + Cotal prizes]
Owns: `enrich.py`, `.cotal/agents/*.md`, mesh wiring.
| # | Task | Prio | Status | Blocks |
|---|---|---|---|---|
| D1 | Deep research: Iridium Door 2 live (`/auth/login` -> JWT -> `/mcp/tools/list`) | P0 | TODO | D2 |
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
- D-002: Lanes are vertical slices, not pipeline stages, so A/B/C/D parallelise without chaining.
