# DEPLOY.md — the public URL (H2)

**This is a stage-recovery doc, not documentation.** Every command is copy-pasteable and
every timing below was measured through the real tunnel, not estimated.

> **The tunnel is an UPGRADE, not a dependency.** The demo is the stdio MCP path inside
> Claude (`prep_conference`). If everything in this file is on fire, the demo still runs.
> See "If the tunnel dies mid-demo" at the bottom — the answer is *do nothing and keep talking*.

---

## LIVE PUBLIC URL

```
https://reliability-olympics-minimize-acne.trycloudflare.com
```

**This hostname is ephemeral.** `trycloudflare.com` mints a new random hostname on every
`cloudflared` restart. If you restart the tunnel, the URL above is dead and you must re-read
the new one out of the log (see COLD START step 4). Do not print it on a slide.

| Route | Method | Works through the tunnel? |
|---|---|---|
| `/health` | GET | yes — cheap, no model calls, safe to poll |
| `/prep` | POST JSON | yes — verified, 31.7s, 15055 bytes, real briefing |
| `/prep/stream` | GET/POST SSE | yes, **but only through the padding shim** — see the critical finding |

---

## COLD START — everything from nothing, under a minute

Three processes: **app (8787)** -> **padding shim (8788)** -> **cloudflared**.
Open three terminals, or background all three as shown. Every terminal does the `source` first.

```bash
# 0. one-time install (already done on this machine; ~15s)
brew install cloudflared          # installed: cloudflared 2026.8.2

# 1. env, every single shell (zsh: bare `. .env` does a $PATH lookup and fails)
source /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/.env

# 2. the app  (skip if `curl -s 127.0.0.1:8787/health` already answers)
uv run --directory /Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1/event-fleet \
  python -m fleet.http_app &

# 3. the SSE padding shim — REQUIRED for streaming, see the critical finding below
python3 $FLEET_ROOT/scripts/sse_pad.py &        # write this file first, source is at the bottom of this doc

# 4. the tunnel, and read the public hostname back out
cloudflared tunnel --url http://127.0.0.1:8788 --no-autoupdate > /tmp/tunnel.log 2>&1 &
sleep 10 && grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' /tmp/tunnel.log | head -1
```

**Verify in one line** (substitute the hostname step 4 printed):

```bash
U=https://reliability-olympics-minimize-acne.trycloudflare.com
curl -s $U/health
curl -sN "$U/prep/stream?event_name=AI%20Engineer%20World's%20Fair%202026&intent=meet%20agent%20eval%20people"
```

`/health` answering `{"status":"ok",...}` through the public hostname means all three hops are up.

**Teardown:** `pkill -f "cloudflared tunnel"; pkill -f sse_pad.py` (leave the app running — the
stdio MCP demo does not use it, but restarting it costs nothing and gains nothing).

No Cloudflare account, no login, no domain, no credential of any kind is involved. A quick
tunnel is anonymous by design. **There is no tunnel secret, so there is nothing in this file
to leak** — which is why this file is safe in a public repo.

---

## CRITICAL FINDING — the quick tunnel buffers SSE, and the shim is why it doesn't

**A bare quick tunnel does NOT stream.** Proven, not suspected:

> Origin emitted 30 events, one per second. Through `trycloudflare.com` the client received
> **all 30 at once at t=30.9s** — i.e. at end-of-stream. Identical client, same server,
> straight to `127.0.0.1`: events arrived at 0.01s, 1.02s, 2.02s, 3.02s, 4.03s. The client
> and the app are both innocent; the edge is the buffer.

Root cause: quick tunnels are served through a Cloudflare **Worker** — the origin sees
`Cf-Worker: trycloudflare.com` — and that Worker holds the response body until a block fills.

It is a **byte threshold, not a timeout**, which is what makes it fixable:

| Padding per event | Result through the tunnel |
|---|---|
| none (real app, ~200B events) | fully buffered, everything lands at end-of-stream |
| 4 KB | still fully buffered (41 KB total never crossed the threshold) |
| 32 KB | **streams**, ~1–3 event lag |
| 64 KB | streams, ~1–2 event lag |

Two things that do **not** work, so nobody re-derives them:
- `x-accel-buffering: no` — the app already sends it. Cloudflare consumes it and does not
  forward it to the client. It does not defeat the Worker.
- A single large priming block at stream open. 128 KB up front changed time-to-first-event by
  nothing (8.1s primed vs 7.1s unprimed). The buffer refills; padding must be **continuous**.

**The shim** (`$FLEET_ROOT/scripts/sse_pad.py`, source at the bottom) sits between cloudflared and the app and
appends a 32 KB SSE **comment** after an event, at most once per 0.75s. Comment lines beginning
with `:` are ignored by every spec-compliant SSE client including browser `EventSource`, so
Persona needs no change. The time gate matters: padding *every* event cost 4.8 MB and ~24s of
accumulated lag; padding at most once per 0.75s costs ~1 MB and under 2s.

**The shim does not touch `event-fleet/src/`.** It is a read-only reverse proxy. The stdio MCP
transport and the app itself are byte-for-byte untouched.

> If someone who owns `http_app.py` wants to delete the shim: emit the same 32 KB SSE comment
> from the heartbeat inside the app and point cloudflared straight at 8787. That is the tidier
> fix. It was not done here because that file belongs to another lane mid-demo.

---

## MEASURED SSE TIMINGS THROUGH THE TUNNEL

Real run, real API spend, shipped config (32 KB pad / 0.75s gate), public hostname,
round-tripping the public internet — `run_id 035cdfa5-5125-4eed-8588-36bda253df2d`.
Client elapsed on the left, the server's own `elapsed_s` on the right:

```
[ 0.336s]  HTTP 200  (content-type: text/event-stream; server: cloudflare)
[ 8.648s]  stage start     server elapsed_s 0.0
[ 8.648s]  stage roster    server elapsed_s 0.0
[ 8.648s]  stage enrich    server elapsed_s 0.0
[24.108s]  stage rank      server elapsed_s 16.25
[24.108s]  stage judge     server elapsed_s 16.25
[26.417s]  stage store     server elapsed_s 23.62
[26.424s]  event: done     server elapsed_s 25.62   picks=19  degradations=3
-----------------------------------------------------------------------
first byte 8.648s | 63 events | client total 26.425s vs server total 25.62s
```

Read those numbers honestly:

- **It genuinely streams.** 63 events arrive spread across the run, not in one dump.
- **Steady-state lag is under 2s.** Server finished at 25.62s, client saw `done` at 26.42s.
  Mid-run beats track within ~0.8–2.3s. Good enough for a live progress UI.
- **Cold start is ~7–9s.** Response *headers* arrive at 0.34s, but the first body flush is
  held ~8s. Measured at 7.1s, 8.1s and 8.6s across three runs; padding size does not move it.
  **Persona will look dead for the first ~8 seconds, then catch up and run live.** Render a
  spinner on HTTP 200, not on first event, or the UI will look broken during the best part.
- **A 40–70s run survives.** Longest observed: 76.8s wall clock, no cut, no idle timeout.
  Heartbeats every ~2s keep it alive; Cloudflare's ~100s idle limit is never approached.

Second full run for consistency, `run_id 8f98516a`: 67 events, server 39.98s, client 42.04s.

---

## `CF-Connecting-IP` — verified arriving, cooldown is per-caller not global

This mattered: if the header did not survive, every caller would look like one IP and the
60s cooldown would become a global lock on the whole demo. **It survives.** Full chain proven:

1. **Cloudflare sets it.** Headers as they actually reach the origin through a quick tunnel:
   ```
   "Cf-Connecting-Ip": "2600:1010:b35b:...",   <- real client IP, not localhost
   "X-Forwarded-For":  "2600:1010:b35b:...",
   "Cf-Ipcountry": "US",  "Cf-Ray": "a31dfbee4ea77834-SJC",
   "Cf-Worker": "trycloudflare.com"
   ```
2. **The shim forwards it.** `curl -H 'CF-Connecting-IP: 203.0.113.77'` through the shim
   arrives at the app as `"Cf-Connecting-Ip": "203.0.113.77"`. Only hop-by-hop headers are dropped.
3. **The app acts on it.** A second request inside the window, through the public URL:
   ```
   event: error
   data: {"message": "Rate limited: one briefing per 60s per caller. Retry in 22s.", "retryable": true}
   ```

Concurrency also holds through the tunnel — a second concurrent request got
`"A briefing is already running (limit 1 at a time)..."` as a clean SSE `error` event, not a hang.

Live protections, all confirmed working end to end: **concurrency 1 · 60s per-caller cooldown ·
20 briefings/hour**. Note `CF-Connecting-IP` is a **cost control, not a security boundary** —
it is trustworthy only because the tunnel is the sole route in. Never bind the app to `0.0.0.0`
while the tunnel is up; that opens a second, unheadered path and the cooldown becomes bypassable.

---

## If the tunnel dies mid-demo

**Do nothing. Keep talking.** The tunnel is not on the critical path.

| What breaks | Impact | What you do |
|---|---|---|
| cloudflared exits / laptop changes network | public URL 502s or resolves to nothing | **Nothing.** The stdio MCP demo in Claude is unaffected — it never touches HTTP. |
| The shim (8788) dies | `/prep/stream` reverts to buffered (still *returns*, just all at the end); `/health` and `/prep` fine | `python3 $FLEET_ROOT/scripts/sse_pad.py &`. Or point cloudflared at 8787 and accept JSON-only. |
| The app (8787) dies | shim returns `502 upstream unreachable`; stdio MCP still fine | restart step 2 |
| Persona's live view stalls | it is showing the buffered stream | it will still deliver the full briefing at the end — say "that's the stream landing" and move on |

**The fallback is the primary.** `prep_conference(event_name, intent)` over stdio MCP inside
Claude is the proven demo path (S1, `claude mcp list` -> `✔ Connected`) and is completely
independent of this file. Per **D-017** the tunnel was always the upgrade, and the stdio
transport was to keep working unchanged. It does.

**Never restart the tunnel on stage to "fix" it** — you get a *new random hostname*, so anything
pointed at the old one (Persona config, a browser tab, a QR code) breaks harder than it was.

---

## Honest read: stage-safe, with one caveat

- **Safe to show.** No account, no credential, no domain, nothing to leak. Rate limits verified
  live through the public path. Worst realistic case is a 502, which costs nothing because the
  real demo is inside Claude.
- **The caveat is the ~8s cold start**, not reliability. If Persona is on screen and renders
  nothing until the first event, it will look broken for 8 seconds during the demo's opening.
  Render on HTTP 200. That is a front-end decision, and it is the one thing worth fixing.
- **Do not build a load-bearing stage moment on `/prep/stream`.** It works and it is measured,
  but it depends on a byte-threshold workaround against an undocumented edge behaviour that
  Cloudflare can change without telling anyone. A named tunnel on a real zone would bypass the
  Worker entirely — that needs the human's Cloudflare account and a domain, so it is filed as
  a HITL question (`H2`, `coord/hitl/pending/`) rather than assumed. **It is unverified that a
  named tunnel fixes the buffering** — likely, but not proven, and not worth demo time to find out.

---

## Appendix — `$FLEET_ROOT/scripts/sse_pad.py`

Paste this whole block to recreate the shim from cold:

```bash
cat > $FLEET_ROOT/scripts/sse_pad.py <<'PYEOF'
"""Pad SSE events so they cross the trycloudflare edge Worker's block buffer.

The quick tunnel releases the response body only once a block has filled, so a
stream of small events arrives in one dump at the end. Appending an SSE comment
(ignored by EventSource, per spec) after each event fills the block and forces a
flush. Front door for the tunnel; forwards CF-Connecting-IP so per-caller
cooldown keeps working. Read-only shim: it never touches fleet code.
"""
import os, time, urllib.request, urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UP = os.getenv("FLEET_UPSTREAM", "http://127.0.0.1:8787")
PAD = int(os.getenv("FLEET_SSE_PAD", "32768"))
PAD_EVERY = float(os.getenv("FLEET_SSE_PAD_EVERY", "0.75"))
PORT = int(os.getenv("FLEET_PAD_PORT", "8788"))
HOP = {"host", "connection", "keep-alive", "transfer-encoding", "upgrade", "accept-encoding"}


class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _proxy(self, body=None):
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP}
        headers["accept-encoding"] = "identity"
        req = urllib.request.Request(UP + self.path, data=body, headers=headers, method=self.command)
        try:
            up = urllib.request.urlopen(req, timeout=300)
        except urllib.error.HTTPError as e:
            up = e
        except OSError as e:
            self.send_response(502)
            msg = f"upstream {UP} unreachable: {e}".encode()
            self.send_header("content-type", "text/plain")
            self.send_header("content-length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return

        ctype = up.headers.get("content-type", "")
        stream = ctype.startswith("text/event-stream")
        self.send_response(up.status)
        for k, v in up.headers.items():
            if k.lower() in HOP or k.lower() == "content-length":
                continue
            self.send_header(k, v)
        if stream:
            self.send_header("transfer-encoding", "chunked")
            self.end_headers()
            pad = b": " + (b"p" * PAD) + b"\n\n"
            buf = b""
            last = time.monotonic()
            try:
                while True:
                    b = up.read1(8192)
                    if not b:
                        break
                    buf += b
                    while b"\n\n" in buf:
                        event, buf = buf.split(b"\n\n", 1)
                        out = event + b"\n\n"
                        now = time.monotonic()
                        if now - last >= PAD_EVERY:
                            out += pad
                            last = now
                        self.wfile.write(b"%x\r\n%s\r\n" % (len(out), out))
                        self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
                self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        payload = up.read()
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self._proxy()

    def do_OPTIONS(self):
        self._proxy()

    def do_POST(self):
        n = int(self.headers.get("content-length") or 0)
        self._proxy(self.rfile.read(n) if n else b"")

    def log_message(self, *a):
        pass


ThreadingHTTPServer(("127.0.0.1", PORT), H).serve_forever()
PYEOF
```

Tunables via env, if the edge behaviour shifts on conference wifi: `FLEET_SSE_PAD` (default
32768 — raise to 65536 if the stream buffers again), `FLEET_SSE_PAD_EVERY` (default 0.75s —
lower for snappier, at the cost of bandwidth), `FLEET_PAD_PORT`, `FLEET_UPSTREAM`.
