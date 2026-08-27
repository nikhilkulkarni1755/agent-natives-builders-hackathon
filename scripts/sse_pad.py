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
