# Conference Prep Fleet — for anyone who wants to run it

You give it a conference and one sentence about why you're going. A fleet of agents pulls the
event's **real published speaker roster**, ranks who is worth your time against your goal,
grades its own ranking, and hands back a briefing with the reasons attached.

It never invents a speaker. If a lineup isn't published, it says so and returns nobody.

---

## Try it with no install and no key

There is a live public endpoint. It is anonymous — no account, no token, nothing to sign up for.
The hostname is an ephemeral Cloudflare quick tunnel and changes whenever the tunnel restarts;
the current one is at the top of [DEPLOY.md](DEPLOY.md).

```bash
U=https://reliability-olympics-minimize-acne.trycloudflare.com

curl -s $U/health

curl -s -X POST $U/prep -H 'content-type: application/json' \
  -d '{"event_name":"AI Engineer World'\''s Fair 2026","intent":"meet people working on agent evals"}'
```

That is a real run: **28.1s, 19 ranked picks**, measured just now through the public hostname.
`GET|POST $U/prep/stream` is the same thing as Server-Sent Events, if you'd rather watch the
lanes report in as they finish than wait for one JSON blob.

> These are plain HTTP routes (`/health`, `/prep`, `/prep/stream`) — **not** an MCP endpoint.
> `claude mcp add --transport http` against this hostname will not connect. To use it as a tool
> inside Claude, run it locally over stdio — next section.

## Add it to Claude as an MCP tool (local, stdio)

```bash
git clone https://github.com/nikhilkulkarni1755/agent-natives-builders-hackathon.git
cd agent-natives-builders-hackathon

claude mcp add conference-prep -- uv run --directory "$PWD/event-fleet" python -m fleet.server
```

Then just ask Claude to prep you for an event. The tool is `prep_conference(event_name, intent)`.

A local run uses **your own** keys, in your shell env or a `.env` at the repo root (or in
`event-fleet/`):

| Variable | Needed for | Required? |
|---|---|---|
| `TAVILY_API_KEY` | finding and reading the event's pages | yes |
| `NEBIUS_API_KEY` | roster extraction, ranking, the self-eval judge | yes |
| `IRIDIUM_API_KEY` | working out who *you* are | **no — see below** |

Nothing is committed to this repo that you have to fill in. There are no keys, tokens or
credentials anywhere in it, including in the deploy doc — a quick tunnel is anonymous by design.

---

## Iridium is optional. Really.

**Without an Iridium account** you get a full briefing, ranked on the intent sentence you typed
and nothing else. The briefing tells you so in plain words rather than pretending:

> No Iridium account is connected, so this briefing has no attendee profile: the picks are
> ranked against your stated goal only. Connect Iridium to have the fleet work out who you are
> and rank against your background too.

**With one**, the fleet resolves your professional background and ranks the roster against that
*as well as* your stated goal — so "meet people working on agent evals" gets weighted by what
you've actually built.

**This was a real bug and it is fixed.** An earlier build served the *owner's* cached LinkedIn
profile to every caller, which would have ranked a stranger against the wrong person's interests.
The cached profile is now gated on the caller presenting their own `IRIDIUM_API_KEY`. Verified
again on the live public endpoint while writing this page — the run above came back with:

```json
"user": {"summary": "", "interests": [], "source": "none"}
```

Empty, honest, and not somebody else's. That is what every keyless caller gets.

---

## What works, and what doesn't

### Works — a conference with a public speakers or schedule page

There is no allowlist and no per-site code. Discovery, extraction and validation are the same
code path for every event. Proven live on three unrelated domains:

| Event | Source domain | One measured run |
|---|---|---|
| AI Engineer World's Fair 2026 | `ai.engineer` | 19 speakers, all with session titles |
| PyCon US 2026 | `us.pycon.org` | 14 speakers, 9 with sessions |
| KubeCon + CloudNativeCon Europe 2026 | `kccnceu2026.sched.com` | 11 speakers, 10 with sessions |

The domains are the point; the counts move between runs as organisers publish more people.

**An unpublished lineup is a correct answer, not a failure.** `AI Engineer NY` returns **zero**
speakers, because as of today its lineup is genuinely not out. It says that instead of guessing.

### Does not work — Luma, Meetup, Partiful

**These are not supported and will not be.** Their pages are client-rendered invite/RSVP pages,
not published speaker listings, so there is no roster on them to read. Asking for one returns an
empty roster with an explanation, immediately.

Measured against real public events on 2026-08-27, *before* the guard existed:

| Platform | What actually happened |
|---|---|
| `lu.ma` | 16.3s → hard error. Another event: 9.1s → **10 "speakers" that were the public RSVP guest list** — names with no role at all ("Xenofon", "Sushrut A"). Wrong data, which is worse than no data. |
| `meetup.com` | 35.9s → zero speakers. |
| `partiful.com` | 51.2s → zero speakers. Another event: 40.4s → hard error. |

With the guard, all three now answer in **0.0s** with the empty-roster shape and this reason:

> Not supported: Luma. Those event pages are client-rendered invites, so a public fetch returns
> nothing, an unrelated page, or the RSVP guest list — never a speaker line-up. No roster was
> fetched and no speaker was invented. What does work: any conference with a public speakers or
> schedule page.

The same check also runs on whatever discovery *finds*, not just on what you typed — so a Luma
page that surfaces while searching for an event by name is dropped before it can be mistaken for
a lineup.

---

## Honest limits

- **Type the event name exactly.** Dropping the year is not a small thing: `AI Engineer World's
  Fair 2026` returns 19 real speakers; `AI Engineer World's Fair` degrades to a 16.8s crawl and
  **2 junk results**. Copy the name as the organiser writes it, year included.
- **Partial rosters are normal.** Big conferences publish speakers in waves. A briefing marked
  partial is built from the wave that is out, and will look different next month.
- **A run takes 28–70 seconds.** 28–34s for an event whose roster is already captured; a cold
  event adds a 17–44s live crawl on top. There is no progress bar on `/prep` — use `/prep/stream`
  if you want to see the lanes land one at a time.
- **The public endpoint is rate limited**, because it runs on one laptop: concurrency 1, one run
  per 60s per caller, 20 runs per hour overall. Over the line you get a refusal, not a queue.
- **Rosters are cached for 7 days.** A capture is a real crawl result with its source URLs and
  timestamp recorded — never a hand-written speaker. Set `FLEET_ROSTER_LIVE=1` to force a fresh one.
- **The public hostname is ephemeral.** `trycloudflare.com` mints a new random one on every
  restart, so the URL above dies when the tunnel does. The local stdio path has no such problem.
- **The self-eval is not decoration.** The judge checks each claim against the evidence and will
  mark its own briefing low-confidence. A confidence of 0.3 means it doubts itself — believe it.
