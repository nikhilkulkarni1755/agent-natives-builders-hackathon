# CLAUDE_PROMPT.md — final submission

Paste the block below into a **fresh Claude Code session** in this repo.

**Before you paste it, do these two things — nothing else matters as much:**
1. **Quit and relaunch Claude Code.** The registered MCP server loads lane modules once at
   boot; the running one predates the unsupported-platform guard and the mesh wiring. This is
   the single most likely way a working demo looks broken.
2. **Keep the laptop awake and online.** `https://events.iridiumhqmcp.com` is served by a
   `cloudflared` process on this machine. If it sleeps, `demo_url` dies — and that field is
   the "it runs" gate.

---

```
You are finishing a hackathon submission for the Conference Prep Fleet. Everything is built,
verified and pushed. Your job is to FILE THE SUBMISSION and post one bulletin-board message.
Do not build features. Do not refactor. There is very little time.

REPO: /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1
Load secrets in any shell you open:
  source /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/.env
(zsh note: a bare `. .env` does a $PATH lookup and fails — use the absolute path.)

## STEP 1 — read these three files, in this order
- FINAL_SUBMISSION.md   — the paste-ready field values. This is your source of truth.
- SUBMISSION_FORMAT.md  — the tool contract, preconditions and gotchas.
- VERIFICATION.md       — what was independently verified, and what was found broken and fixed.

## STEP 2 — pre-flight, and be honest about what you find
Verify, do not assume:
  curl -s https://events.iridiumhqmcp.com/health          # must be 200
  curl -s -o /dev/null -w '%{http_code}' https://events.iridiumhqmcp.com/   # must be 200, serves the UI
  claude mcp list | grep conference-prep                   # must say Connected
If the public URL is down, the tunnel process died. Restart it:
  cloudflared tunnel --config ~/.cloudflared/events-fleet.yml run events-fleet &
Then re-check. If it still fails, tell the human immediately — demo_url is the "it runs" gate,
and an unreachable one costs more than every other field combined.

Note: the operator's local DNS resolver has been stale. If curl fails locally but
`curl --resolve events.iridiumhqmcp.com:443:104.21.13.113 ...` succeeds, the site is UP for
everyone else and the local resolver is the only problem. Do not restart anything on that basis.

## STEP 3 — file the submission
The MCP server is https://www.immersivecommons.com/api/mcp, tool `ic_hack_submit`, eid
`anb-hack-01`. A test submission already exists, so the team and roster preconditions are met.

Send EVERY field from FINAL_SUBMISSION.md verbatim: title, blurb, repo_url, demo_url,
agent_surface. The call is idempotent per team and OVERWRITES THE WHOLE RECORD — a field you
omit is not preserved from the earlier submission, it is lost.

Build the payload with a real JSON serializer (write it to a file, then send the file). Do not
interpolate it in a shell string; agent_surface contains backticks, quotes and newlines.

Business failures return HTTP 200 with `ok: false` in the body. Read the envelope, not the
status code. Echo the stored record back and confirm each field actually landed.

## STEP 4 — post ONE bulletin-board message, 200 chars MAX
Minimal, useful to another agent, and it must carry the link. Use exactly this unless the human
says otherwise:

  Conference Prep Fleet — name a conference + why you're going, get a ranked briefing of who to
  meet. Refuses to invent people. No key: https://events.iridiumhqmcp.com

Count the characters before posting. If the endpoint rejects it, shorten by cutting the middle
sentence, never the URL.

## RULES
- NEVER invent a result, a URL, or a confirmation. If a call fails, say so plainly.
- The repo is PUBLIC and there is a live .env and Cotal credentials on disk. Never commit or
  echo a key. Stage explicit paths only: `git commit -m "msg" -- <path>` (-m BEFORE --).
  Never `git add -A`.
- Do not edit anything under event-fleet/src/. The system is verified working; a late edit is
  pure downside.
- If something is genuinely the human's decision, ask them directly and keep working meanwhile.

## WHAT THIS IS, so you can answer questions about it
An MCP server: `prep_conference(event_name, intent) -> ConferenceBriefing`. It pulls a real
published speaker roster, resolves who the caller is (optional, via Iridium), ranks speakers
against their stated goal on Nebius gpt-oss-120b, and has a second model audit the ranking for
invented facts before returning. Five agents coordinated over an authenticated Cotal NATS mesh
with default-deny broker-enforced ACLs. Tavily does discovery and public enrichment; Persona is
the front end; Mitosis is wired as a cited evidence client. 24 events verified across 12
domains. Luma, Meetup and Partiful are unsupported by design and fail closed in 0.0s.
The strongest claim, and it is evidenced: it refuses to make things up.
```
