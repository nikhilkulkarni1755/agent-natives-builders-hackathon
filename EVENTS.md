# Events that work

**Paste the event string EXACTLY as written below** — character for character, year included.
The cache key is the slugged event name, so "AI Engineer Europe 2026" and "AIE Europe" are two
different keys: the wrong one misses the cache and triggers a slow live crawl that may return
worse data.

Every row below was verified on **2026-08-27** by a real `fetch_roster()` call that returned real,
named people. Nothing here is hand-written. Two honest caveats:

- **Counts move.** Conference rosters publish in waves, so a "~19 speakers" event may return 30
  next month. `partial: yes` means the site itself signalled the lineup is still incomplete.
- **Zero speakers is a correct answer.** An event whose lineup is not yet published returns
  *nobody* rather than a guess. That is the system refusing to invent people, not a failure.
  There is a row below that demonstrates exactly this.

Because each of these was fetched for real, every one is now cached and comes back near-instantly
for the next person who runs it.

## San Francisco / Bay Area

| Paste this exactly | Speakers returned | Partial? | Notes |
| --- | --- | --- | --- |
| `AI Engineer World's Fair 2026` | ~19 | yes | Jun 29 – Jul 2, 2026, San Francisco |
| `NVIDIA GTC 2026` | ~25 | no | San Jose |
| `GDC 2026` | ~25 | no | Mar 9–13, 2026, San Francisco |
| `Cloudflare Connect 2026` | ~25 | no | San Francisco |
| `The AI Conference 2026` | ~25 | yes | Sep 29 – Oct 1, 2026, San Francisco |
| `Snowflake Summit 2026` | ~23 | no | Jun 1–4, 2026, San Francisco |
| `RSA Conference 2026` | ~22 | no | Mar 23–26, 2026, Moscone Center, San Francisco |
| `Data + AI Summit 2026` | ~12 | yes | Jun 15–18, 2026, San Francisco (Databricks) |
| `Stripe Sessions 2026` | ~11 | yes | San Francisco |
| `GitHub Universe` | ~11 | no | Oct 28–29, Fort Mason Center, San Francisco. Use the name **without** the year — `GitHub Universe 2026` returns zero |
| `TechCrunch Disrupt 2026` | ~10 | no | Oct 13–15, 2026, San Francisco |
| `AI DevSummit 2026` | ~8 | no | May 27–28, 2026, South San Francisco |
| `ODSC AI West 2026` | ~6 | no | ODSC West, virtual + Bay Area |
| `Ray Summit 2026` | ~6 | no | San Francisco |
| `GenAI Summit SF 2026` | ~4 | no | Jul 18–19, 2026, San Francisco |

Thinner but real (a small published lineup, not a broken fetch):

| Paste this exactly | Speakers returned | Partial? | Notes |
| --- | --- | --- | --- |
| `Dreamforce 2026` | ~2 | no | Sep 15–17, 2026, San Francisco. Only the headliners are announced so far |
| `AI Dev DeepLearning.AI 2026` | ~1 | no | Apr 28–29, 2026, San Francisco |

## New York

| Paste this exactly | Speakers returned | Partial? | Notes |
| --- | --- | --- | --- |
| `React Summit US 2026` | ~25 | no | Nov 17–20, 2026, New York |
| `The AI Summit New York 2026` | ~20 | no | Dec 9–10, 2026, Javits Center |
| `Vercel Ship 2026` | ~25 | yes | Jun 30, 2026, New York / London / Berlin. Rich count, but the site lists many speakers by first name only, so some rows come back sparse |
| `FinovateFall 2026` | ~9 | yes | Sep 9–11, 2026, Marriott Marquis Times Square |
| `ProductCon New York 2026` | ~9 | yes | May 20, 2026, New York |
| `AWS Summit New York 2026` | ~1 | no | Only the keynote speaker is published so far |
| `MCP Dev Summit NYC 2026` | ~1 | no | Apr 2–3, 2026, New York City |

### The honest edge case — try this one in front of a skeptic

| Paste this exactly | Speakers returned | Partial? | Notes |
| --- | --- | --- | --- |
| `AI Engineer NY` | **0** | yes | The lineup is not published yet. The system returns **nobody** and says the roster is incomplete, instead of filling the gap with plausible-sounding names. This is the behaviour to show off, not to hide |

## Also verified (outside SF and NY)

These are not Bay Area or New York events, but they were verified the same way and are cached, so
they are safe to demo if you want to show the same code path working on a different domain.

| Paste this exactly | Speakers returned | Partial? | Notes |
| --- | --- | --- | --- |
| `AI Engineer Europe 2026` | ~24 | yes | Apr 8–10, 2026, London |
| `PyCon US 2026` | ~14 | no | us.pycon.org |
| `KubeCon + CloudNativeCon Europe 2026` | ~11 | yes | Sched-hosted schedule |

## What did not work, and why

Tested on 2026-08-27 and deliberately left off the list:

| Event string tested | What happened |
| --- | --- |
| `GitHub Universe 2026` | 0 speakers. The year-suffixed page is a teaser; `GitHub Universe` (no year) works |
| `JSNation US 2026` | 0 speakers. Lineup not published yet — the same correct-empty behaviour as AI Engineer NY |
| `PyGotham 2026` | Error: no readable document found — every candidate source described a different event |
| `InfoQ Dev Summit New York 2026` | Error: no readable document found |
| `SmashingConf New York 2026` | Error: no readable document found |

Known **unsupported platforms** — these publish invites and RSVP pages, not rosters, so there is
nothing to read. They are refused in about 2 seconds rather than crawled:

- Luma (`lu.ma`, `luma.com`)
- Meetup (`meetup.com`)
- Partiful (`partiful.com`)

Also skip umbrella "tech week" style listings (SF Tech Week, NY Tech Week): they are a thousand
separately hosted parties with no central speaker page, so there is no single roster to fetch.
