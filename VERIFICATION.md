# VERIFICATION.md — V1 full re-check against reality

**Verifier: wave6-V1. Every number below was reproduced by me, in this session, on this machine.**
Nothing here is quoted from another lane's log. Where I could not reproduce a claim, it is in
§4 CLAIMED BUT UNPROVEN, which is the section that matters most.

Method: cold-start stdio MCP client (real JSON-RPC `initialize` → `tools/list` → `tools/call`),
not a Python import. Script kept at
`<scratchpad>/mcpclient.py`.

---

## 0. HEADLINE — read this before anything else

**The MCP server registered in Claude right now is running STALE CODE, and it is what a
presenter would hit if they do not relaunch Claude Code.** This is not a hypothetical
restatement of D-014. I reproduced it:

| | stale server (pid 82268, booted 12:47:29) | fresh server (booted at call time) |
|---|---|---|
| `prep_conference("AI Engineer Europe 2026", …)` | **176.7s**, **158 picks**, 0 session titles, `event_description: "Schedule"` | **34.1s**, **24 picks**, 24 session titles, correct description |
| `prep_conference("AI Engineer World's Fair 2026", …)` | **158.2s**, **0 picks**, `RankError` (roster returned 352 page-furniture rows) | **28.0s**, **19 picks**, 19 session titles, confidence 0.967 |
| `prep_conference("AI Engineer NY", …)` | **12 picks** (run `1561dffd`) — the excluded "past speakers" list | **0 picks, 0.0s** — correct |

Proof of which process served the calls: I snapshotted CPU time of both server PIDs around a
tool call. PID 82268 went `0:00.93 → 0:01.08`; the fresh PID 38916 stayed at `0:00.33`.
The stale process was launched at 12:47:29, before R2 (roster cache, ~13:41) and R3
(generalised extractor, 13:40) landed, so it has neither.

**Consequence for the demo:** on a stale server, `"AI Engineer NY"` returns **12 named people**.
That is the exact list D-011 says must be excluded, and it detonates the closing line of the
pitch ("it returns nobody, instead of inventing five people to fill the slide"). The single
highest-value pre-flight action is DEMO.md §0 step 1. It is already written down. It is not
optional, and it is currently not satisfied.

Note also: `git status` shows a concurrent session was still writing runs and re-capturing
`iridium_user_profile.json` (live, 21:01:28Z) while I verified. Freeze other lanes before you
rehearse or the ground keeps moving.

---

## 1. WHAT IS DEMO-SAFE (personally reproduced)

**MCP registration and tool surface.** `claude mcp list` → `conference-prep: … ✔ Connected`.
Cold-start `initialize` + `tools/list` over real stdio JSON-RPC: **0.37–0.38s**, serverInfo
`conference-prep-fleet 0.1.0`, `TOOLS: ['prep_conference']`. No non-JSON ever appeared on
stdout across 6 cold-start runs — **D-005 holds**, and a source grep for `print(` under
`event-fleet/src/fleet/` returns nothing.

**The three demo events, on a fresh server, using the exact DEMO.md strings.** All three cache
slugs resolve exactly (`ai-engineer-europe-2026`, `ai-engineer-world-s-fair-2026`,
`ai-engineer-ny`); cache TTL is `_DEFAULT_TTL_HOURS = 168.0`, captures are ~0.2h old, so no
live crawl fires. Europe: 24 picks, 24/24 session titles, real people (David Soria Parra —
"The Future of MCP"; Ido Salomon — "AgentCraft"). World's Fair: 19 picks, 19/19 session titles,
confidence 0.967, `facts_grounded: true`. NY: **0 picks in 0.0s**, honestly reported.

**Roster cache is genuinely keyless.** With `TAVILY_API_KEY`, `NEBIUS_API_KEY` and
`IRIDIUM_API_KEY` all removed from the environment: Europe 24 speakers in **1.56ms**, World's
Fair 19 in **0.29ms**, NY 0 in **0.18ms** — same names as live. R2's claim holds exactly.

**The judge discriminates.** `pytest tests/test_judge.py -v` → **4 passed in 16.96s**, real
`openai/gpt-oss-120b` calls, no mocks, including
`test_ungrounded_ranking_fails_and_caps_confidence`. On live runs I observed both verdicts on
real data: `facts_grounded: false` @ 0.4 with named ungrounded claims, and
`facts_grounded: true` @ 0.967/1.0. It is not a rubber stamp.

**Nothing fails silently — for the enrichment lane.** With `IRIDIUM_API_KEY=deliberately-invalid`
(one process only; the real `.env` untouched), the run still returned 24 picks at confidence 1.0
and `degradations` carried the real cause verbatim:
`Iridium could not resolve 'Omar Sanseviero' (IridiumError: … login failed with 401 …); their 3
fact(s) came from public web search via Tavily (ai.engineer, datakami.com), not from LinkedIn.`
Spec section 6 satisfied on this path.

**Cotal mesh.** `cotal status` → `mode auth`, `connection ok`, `nats://127.0.0.1:4222`, web
running, and `channels fleet.errors(2), fleet.progress(8)`. Never `--open`. ACL denial is on
disk and reproducible: `grep "Publish Violation" .cotal/nats.log` → 2 hits, both
`Subject "cotal.main.chat.local.<pubkey>.fleet.judge-only"`. Dashboard returns **HTTP 401** on
both `cotal.localhost:7799` and `127.0.0.1:7799`. All four M1/M2 claims stand.

**R3 cross-domain — and it is stronger than DEMO.md currently says.** I ran live crawls myself:
`us.pycon.org/2026/about/keynote-speakers` → **14 real speakers** (Lin Qiao, Pablo Galindo
Salgado, Barry Warsaw, Amanda Casari…), 9 with sessions, 18.1s.
`kccnceu2026.sched.com/list/descriptions` → **11 real speakers** (Katherine Druckman, Kohei
Tokunaga, Bill Mulligan…), 10 with sessions, 20.6s. Two non-`ai.engineer` domains, real names,
same code path, no per-site casing. **See §5 — the softened claim can be upgraded.**

**Public-repo safety: clean.** `git ls-files` tracks no `.env`, no `*.creds`, no `*.db`, no
`nats.log`, no `web.session`. Grep of the whole tracked tree for `tvly-`, `irid_`, `mi_`, `eyJ`,
NATS seeds and PEM headers returns only *prose mentions* of the string `irid_` in BOARD.md,
PROTOCOL.md, `.env.example` and `iridium.py` — no key material. `git log --diff-filter=A` across
all refs shows none of those paths was **ever** committed. `.env` is `-rw-------`. `token.txt`
is untracked and gitignored. `.cotal/.gitignore` is deny-all with an allow-list of
`agents/*.md`, and those five files are clean. The tracked
`data/iridium_user_profile.json` contains no token material (checked for `access_token`,
`refresh_token`, `api_key`, `eyJ`, `Bearer`) — only the user's own public LinkedIn profile.
*Judgement call, not a leak:* that is personal data in a public repo. Intentional, presumably,
but worth a conscious decision.

---

## 2. WHAT IS BROKEN OR FRAGILE — ranked by likelihood of biting on stage

**1. Stale MCP server (near-certain if pre-flight is skipped; catastrophic).** See §0. Two
`fleet.server` processes were alive simultaneously during verification. A Claude window opened
before ~13:43 serves pre-R2/R3 code and will return 158 picks, 0 picks with a `RankError`, or
12 fabricated-context NY names. **Mitigation is already written as DEMO.md §0 step 1 — enforce
it, and close every other Claude window so there is only one server process.**

**2. The event string must be character-exact (high likelihood; severe).** The cache key is a
slug of the event name, so one word off is a silent cache miss into a live crawl. Measured on
**current** code:

| Typed string | Result |
|---|---|
| `AI Engineer World's Fair 2026` | cache hit, 0.29ms, **19 speakers** |
| `AI Engineer World's Fair` (no year) | cache MISS, **16.8s**, **2 speakers** |
| `AI Engineer Europe 2026` | cache hit, 1.56ms, **24 speakers** |
| `AI Engineer Europe` (no year) | cache MISS, **43.7s**, 25 speakers (different set) |

A concurrent session already hit this for real: run `b2fd935f`, `"AI Engineer World's Fair"`,
returned **1 pick named "Optimizing Modern AI Systems"** — a session title rendered as a person.
Type Beat 1 from the file, do not retype it from memory.

**3. Judge latency and variance (high likelihood; moderate).** The judge is the dominant stage
cost — **34.16s** of a 50.5s run — and it is the pipeline's variance driver. Across 25 stored
runs on only three events, confidence on identical inputs ranged
**0.183 / 0.275 / 0.342 / 0.383 / 0.4 / 0.933 / 0.967 / 1.0**. DEMO.md handling both a high and
a low branch is the right design; just do not promise a specific number. The `JudgeError`
truncation flake is real and I saw it live in concurrent run `1561dffd`
(`finish_reason=length`) — Fallback G is correctly armed.

**4. DEMO.md Fallback A shows a moving target (moderate likelihood; embarrassing).** It selects
`order by created_at desc limit 1`. During verification that "latest run" was, at different
minutes: a good Europe run, then the **NY run with 12 picks and a JudgeError**, then a good
World's Fair run. Any concurrent session changes what your safety net displays.
**Fix: pin an explicit `run_id` in the Fallback A snippet** (`6a554a6f` or `21bc51bc` are good
rows) instead of taking the newest.

**5. `render.py` is not on the demo path (moderate; only matters in Q&A).** It is imported
**only** by `http_app.py`; `server.py` never calls it. DEMO.md §9 admits this, but §6's Q&A
answer still promises an explicit *"nobody has been invented to fill this section"* — that
string lives in `render.py:35` and **never reaches the MCP output**. NY returns an empty
`picks: []` with `notes: "No picks were produced, so there was nothing to judge."` — honest,
but not the sentence the script promises. Say "it returns zero picks", not "it says so".

**6. D-015 is not what the board implies (low stage risk; real account risk).** `enrich_user()`
is cached (0 live calls) — that part is true. But `enrich_speakers(limit=3)` fires **3 real
`get_linkedin_profile` calls every single run**. Counted directly in server stderr:
`grep -c "call_tool tool=get_linkedin_profile"` → **3**. So a normal run is **3 live LinkedIn
searches, not zero.** Warm-up run + stage run + each rehearsal = 3 each. I spent ~9 across
verification. Budget the remainder deliberately; this is the throttling risk D-015 exists to
prevent.

---

## 3. THE REAL LATENCY TABLE (all measured by me)

Cold-start MCP boot + `initialize`: **0.37–0.38s** (negligible).

**End-to-end through a real MCP `tools/call`, fresh server, warm roster cache:**

| Event | Total | Picks | Sessions | Confidence | Degradations |
|---|---|---|---|---|---|
| AI Engineer Europe 2026 | **34.1s** | 24 | 24 | 0.4 (`facts_grounded: false`) | 3 |
| AI Engineer World's Fair 2026 | **28.0s** | 19 | 19 | 0.967 (`facts_grounded: true`) | 3 |
| AI Engineer NY | **0.0s** | 0 | 0 | 0.0 | 0 |
| Europe, Iridium forced to 401 | **50.5s** | 24 | 24 | 1.0 | 3 |

**Per-stage** (Europe, from the server's own timestamped stderr):

| Stage | Time |
|---|---|
| boot + initialize | 0.37s |
| `fetch_roster` (cache hit) | 0.27s |
| `enrich_user` (cache) | ~0ms |
| `enrich_speakers` (24 spk, 3 LinkedIn + 12 public) | 2.4s on Iridium fast-fail; ~14s when Iridium answers |
| `rank` (`gpt-oss-120b`, 24 speakers) | **13.69s** |
| `judge` (audits top 8 of 24) | **34.16s** ← dominant |
| `store` | 0.01s |

**Cold roster (live crawl, current code, roster stage only):** PyCon US 18.1s · KubeCon EU 20.6s ·
`"AI Engineer World's Fair"` 16.8s · `"AI Engineer Europe"` 43.7s.
**Estimated cold end-to-end** = warm total + crawl ≈ **45–78s**, worst observed path ~90s.

**Stale-server reality, for contrast:** Europe **176.7s**, World's Fair **158.2s**.

**Verdict against DEMO.md's "pipeline takes roughly 40–70s" and an ~85s script:** warm and fresh,
**28–34s — comfortably inside the claim, better than advertised.** Fire it in the first five
seconds and you will land with time to spare. Stale or on a cache miss you are at 90–177s and
the script has dead air. The 40–70s figure is honest *only* under the pre-flight conditions.

---

## 4. CLAIMED BUT UNPROVEN — the section to act on

**a) "Every non-silent fleet error posts to the mesh" (TASKS.json M3 = DONE; DEMO.md Beat 3 and
§8 evidence index). FALSE for the pipeline.** `mesh.py` is **dead code**. I grepped every module
under `src/fleet/` — `server.py`, `roster.py`, `enrich.py`, `rank.py`, `judge.py`, `store.py`,
`render.py`, `http_app.py` — **not one of them imports or calls `mesh`.** Proof beyond the grep:
I bracketed the forced-failure run (which produced 3 real Iridium 401 degradations) with
`cotal status`. Before: `fleet.errors(2), fleet.progress(8)`. After: `fleet.errors(2),
fleet.progress(8)`. **Unchanged.** The counts on the dashboard came from manual CLI `cotal send`
by the mesh lane, not from the pipeline.
→ DEMO.md §0 step 3 ("you want them to *grow* on stage") **will not happen.** Beat 3's "Every
lane's progress, every error" is not earned. **Say instead:** "a real authenticated audit log
with durable, replayable channels, and a broker-level denial" — all of which *is* true and
proven. Do not imply the run you just fired wrote to it.

**b) DEMO.md §6: unpublished roster produces "an explicit 'nobody has been invented to fill this
section'".** Not on the MCP path — that string is in unwired `render.py`. See §2.5.

**c) DEMO.md §3 / §8: "25 real speakers … `degradations: []`" (runs `6a554a6f`, `42a889db`).**
Those run_ids **do** exist in `runs.db` with 25 picks and 0 degradations — the citation is
honest. But it is **stale**: today's cache holds **24** Europe speakers and every current run
carries **3** degradations (the Iridium-disambiguation → Tavily fallback). Nothing is wrong;
the numbers on the slide are just no longer the numbers on the screen. Update to
"24 speakers, 3 honest degradations" or you will be contradicted by your own live run.

**d) TASKS.json is stale in the *optimistic* direction too.** `I1` is marked `RUNNING` and
`I2`/`I3` `TODO`, but rank, judge, enrichment and degradation-threading are all demonstrably
wired and working in `server.py`. The board understates what is built. `A3`–`A7` on BOARD.md are
likewise still `TODO` against shipped code. Harmless on stage, but do not read the board aloud.

**e) R3's evidence counts do not reproduce exactly.** TASKS.json claims
`us.pycon.org (10) + kccnceu2026.sched.com (25)`. I got **14** and **11** on live re-runs. The
*claim* (two non-ai.engineer domains, real names) is solid; the *counts* are not stable, because
the extractor is a model. Quote the domains, not the numbers.

**f) DEMO.md §7 "in the last run the top picks resolved via Tavily public data, not Iridium."**
Broadly right and worth keeping, but the precise histogram today is Europe `{tavily: 12,
none: 12}`, World's Fair `{tavily: 12, none: 7}` — **`iridium: 0`** on both, because all three
LinkedIn resolutions hit the disambiguation guard and fell back. Iridium resolved **you**
(`user.source: iridium-cache`), and that is the accurate sentence. It currently resolves zero
speakers.

**g) Not reproduced, and I did not attempt it:** the live `cotal send` ACL re-fire (DEMO.md §4
Beat 2 primary variant). DEMO.md §9 already flags this. The `grep` variant on `nats.log` is on
disk and I confirmed it works — **use the grep, not the live send.**

---

## 5. ONE CLAIM YOU SHOULD MAKE *STRONGER*

DEMO.md §9 softened "works for any conference" to "event-agnostic" because cross-domain proof
did not exist when it was written. **It exists now, and I reproduced it live in this session:**
`us.pycon.org` → 14 real speakers, and `kccnceu2026.sched.com` → 11 real speakers, both through
the same `fetch_roster` with no domain checks, both recording their real source URLs
(`data/roster/pycon-us-2026.json`, `data/roster/kubecon-cloudnativecon-europe-2026.json`).

**The domain-agnostic claim is earned.** You may say: *"Three different domains — ai.engineer,
us.pycon.org, and a Sched-hosted KubeCon site — same code path, no per-site special cases."*
Drop the §6 caveat "I'd want a run against a non-ai.engineer domain before claiming that on
stage." You have two. (Those two cache files are currently **untracked** — commit them as
evidence, with explicit paths per D-008.)

---

## 6. GO / NO-GO ON THE THREE DEMO EVENTS

All three are **GO**, and all three are **conditional on the same two gates**:
**(i) relaunch Claude Code so the MCP server is fresh, and confirm only one `fleet.server`
process is alive; (ii) type the event string character-exact from DEMO.md §4.**

| Event | Verdict | Evidence | Fails if |
|---|---|---|---|
| **AI Engineer Europe 2026** (Beat 1) | **GO** | 34.1s, 24 picks, 24/24 sessions, judge caught real ungrounded claims → conf 0.4, the closer's low-confidence branch works on live data | stale server → 158 picks of page furniture, 177s |
| **AI Engineer World's Fair 2026** | **GO** | 28.0s, 19 picks, 19/19 sessions, conf 0.967 `facts_grounded: true`, the high-confidence branch | stale server → **0 picks + RankError**; drop the year → 2 picks |
| **AI Engineer NY** (D-011 edge case) | **GO** | 0 picks in 0.0s, cache is keyless and serves 0 speakers with no API keys at all | **stale server → 12 named "past speakers" and the closer dies** |

**Blunt summary.** The product is better than the board says and the pitch is mostly honest —
the judge really does have teeth, the mesh really is authed with a real broker denial, the
extractor really is domain-agnostic, and warm latency (28–34s) beats what DEMO.md promises.
Three things to fix before you walk up, in order: **relaunch Claude Code and kill the stale
server**; **stop claiming pipeline errors reach the mesh (they do not — `mesh.py` is never
called)**; and **pin a `run_id` in Fallback A.** Then upgrade the domain-agnostic claim, because
that one you have actually earned.
