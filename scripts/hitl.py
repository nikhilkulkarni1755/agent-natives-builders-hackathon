#!/usr/bin/env python3
"""HITL bridge: fleet <-> Telegram.

Agents never block on the human. `ask` fires and returns; `check` is polled on
each heartbeat; `poll` runs once in its own terminal and routes replies back.
"""

import json
import os
import pathlib
import random
import string
import sys
import time
import urllib.parse
import urllib.request

ROOT = pathlib.Path("/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1")
HITL = ROOT / "coord" / "hitl"
PENDING, ANSWERED = HITL / "pending", HITL / "answered"
OFFSET, CHATFILE = HITL / ".offset", HITL / ".chat_id"
ASSUME_AFTER_S = 20 * 60


def token() -> str:
    t = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not t and (ROOT / "token.txt").exists():
        t = (ROOT / "token.txt").read_text().strip()
    if not t:
        sys.exit("no TELEGRAM_BOT_TOKEN and no token.txt at repo root")
    return t


def api(method: str, **params):
    url = f"https://api.telegram.org/bot{token()}/{method}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    with urllib.request.urlopen(url, timeout=70) as r:
        return json.load(r)


def chat_id() -> str | None:
    c = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if c:
        return c
    if CHATFILE.exists():
        return CHATFILE.read_text().strip() or None
    upd = api("getUpdates", limit=50).get("result", [])
    for u in reversed(upd):
        msg = u.get("message") or u.get("channel_post") or {}
        cid = str((msg.get("chat") or {}).get("id", ""))
        if cid:
            CHATFILE.write_text(cid)
            return cid
    return None


def send(text: str):
    cid = chat_id()
    if not cid:
        print("HITL: no chat_id yet -- message the bot once, then rerun `hitl.py discover`",
              file=sys.stderr)
        return None
    return api("sendMessage", chat_id=cid, text=text).get("result", {}).get("message_id")


def qid() -> str:
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=4))


def cmd_ask(agent: str, question: str, *options: str):
    q = qid()
    opts = "\n".join(f"  {i+1}) {o}" for i, o in enumerate(options))
    body = f"[{q}] Agent {agent} needs a call:\n\n{question}"
    if opts:
        body += f"\n\n{opts}\n\nReply `{q} <number>` or just reply to this message."
    else:
        body += f"\n\nReply `{q} <answer>` or just reply to this message."
    mid = send(body)
    (PENDING / f"{q}.md").write_text(json.dumps({
        "qid": q, "agent": agent, "question": question, "options": list(options),
        "asked_at": time.time(), "message_id": mid,
        "recommended": options[0] if options else None,
    }, indent=2))
    print(q)


def cmd_check(agent: str):
    out = []
    for f in sorted(ANSWERED.glob("*.md")):
        d = json.loads(f.read_text())
        if d.get("agent") == agent and not d.get("seen_by_agent"):
            out.append(f"[{d['qid']}] Q: {d['question']}\n    ANSWER: {d['answer']}")
            d["seen_by_agent"] = True
            f.write_text(json.dumps(d, indent=2))
    # auto-default anything the human has sat on too long
    for f in sorted(PENDING.glob("*.md")):
        d = json.loads(f.read_text())
        if d.get("agent") != agent:
            continue
        waited = time.time() - d["asked_at"]
        if waited > ASSUME_AFTER_S and d.get("recommended"):
            d["answer"] = d["recommended"]
            d["assumed_default"] = True
            (ANSWERED / f.name).write_text(json.dumps(d, indent=2))
            f.unlink()
            out.append(f"[{d['qid']}] TIMED OUT after {int(waited/60)}m -> "
                       f"ASSUMED DEFAULT: {d['recommended']}  (log this and proceed)")
        else:
            out.append(f"[{d['qid']}] STILL WAITING ({int(waited/60)}m): {d['question']}")
    print("\n".join(out) if out else "NO_ANSWER_YET")


def resolve(text: str, reply_to: int | None):
    """Match an incoming Telegram message to a pending question."""
    pend = {f.stem: json.loads(f.read_text()) for f in PENDING.glob("*.md")}
    if not pend:
        return None, text
    if reply_to:
        for k, d in pend.items():
            if d.get("message_id") == reply_to:
                return k, text
    head, _, rest = text.partition(" ")
    if head.upper() in pend:
        return head.upper(), rest.strip() or text
    if len(pend) == 1:
        return next(iter(pend)), text
    return None, text


def cmd_poll():
    cid = chat_id()
    print(f"HITL poller live. chat_id={cid or 'UNKNOWN — message your bot once'}")
    off = int(OFFSET.read_text()) if OFFSET.exists() else 0
    while True:
        try:
            res = api("getUpdates", offset=off, timeout=50).get("result", [])
        except Exception as e:  # never die silently
            print(f"HITL poll error: {e}", file=sys.stderr)
            time.sleep(3)
            continue
        for u in res:
            off = u["update_id"] + 1
            OFFSET.write_text(str(off))
            msg = u.get("message") or {}
            text = (msg.get("text") or "").strip()
            if not text:
                continue
            if not CHATFILE.exists():
                CHATFILE.write_text(str(msg["chat"]["id"]))
            key, ans = resolve(text, (msg.get("reply_to_message") or {}).get("message_id"))
            if not key:
                print(f"  (unmatched) {text}")
                continue
            d = json.loads((PENDING / f"{key}.md").read_text())
            if ans.isdigit() and d["options"]:
                i = int(ans) - 1
                if 0 <= i < len(d["options"]):
                    ans = d["options"][i]
            d["answer"], d["answered_at"] = ans, time.time()
            (ANSWERED / f"{key}.md").write_text(json.dumps(d, indent=2))
            (PENDING / f"{key}.md").unlink()
            print(f"  [{key}] -> {ans}")
            send(f"[{key}] recorded for Agent {d['agent']}: {ans}")


if __name__ == "__main__":
    PENDING.mkdir(parents=True, exist_ok=True)
    ANSWERED.mkdir(parents=True, exist_ok=True)
    c = sys.argv[1] if len(sys.argv) > 1 else "help"
    if c == "ask":
        cmd_ask(sys.argv[2], sys.argv[3], *sys.argv[4:])
    elif c == "check":
        cmd_check(sys.argv[2])
    elif c == "poll":
        cmd_poll()
    elif c == "notify":
        send(sys.argv[2])
    elif c == "discover":
        print(chat_id() or "NO_CHAT_ID -- send your bot any message, then rerun")
    else:
        print(__doc__)
        print("usage: hitl.py {ask <agent> <q> [opts...]|check <agent>|poll|notify <msg>|discover}")
