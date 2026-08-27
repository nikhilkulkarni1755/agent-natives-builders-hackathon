#!/usr/bin/env python3
"""Dynamic fleet scheduler.

Agent count is NOT declared. It is the width of the ready frontier: every task whose
dependencies are all DONE and whose owned files are not being written by an in-flight
task. The fleet widens when work fans out and narrows at integration points, on its own.
"""

import json
import pathlib
import sys

ROOT = pathlib.Path("/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1")
TASKS = ROOT / "coord" / "TASKS.json"
MAX_CONCURRENT = 6


def load():
    return json.loads(TASKS.read_text())


def save(t):
    TASKS.write_text(json.dumps(t, indent=2))


def frontier(t):
    """Tasks that could start right now."""
    busy_files = {f for x in t.values() if x["status"] == "RUNNING" for f in x["files"]}
    out = []
    for x in t.values():
        if x["status"] != "TODO":
            continue
        if any(t[d]["status"] != "DONE" for d in x["deps"]):
            continue
        if set(x["files"]) & busy_files:
            continue  # file-level mutual exclusion: no two agents write the same file
        out.append(x)
    return sorted(out, key=lambda x: (x["role"] != "spine", x["id"]))


def running(t):
    return [x for x in t.values() if x["status"] == "RUNNING"]


def cmd_ready():
    t = load()
    inflight = running(t)
    slots = MAX_CONCURRENT - len(inflight)
    f = frontier(t)[:max(0, slots)]
    print(f"IN FLIGHT: {len(inflight)}  {[x['id'] for x in inflight]}")
    print(f"SPAWN NOW: {len(f)}  (fleet width this wave = {len(inflight) + len(f)})")
    for x in f:
        print(f"\n  {x['id']}  [{x['role']}]  {x['title']}")
        print(f"      owns:   {', '.join(x['files']) or '(no files)'}")
        print(f"      verify: {x['verify']}")
    blocked = [x for x in t.values() if x["status"] == "TODO" and x not in f and x not in inflight]
    if blocked:
        print(f"\nWAITING: {len(blocked)} tasks still have unmet deps")


def cmd_claim(tid, agent):
    t = load()
    t[tid]["status"], t[tid]["agent"] = "RUNNING", agent
    save(t)
    print(f"{tid} -> RUNNING ({agent})")


def cmd_done(tid, evidence):
    t = load()
    t[tid]["status"], t[tid]["evidence"] = "DONE", evidence
    save(t)
    unlocked = [x["id"] for x in frontier(t) if tid in x["deps"]]
    print(f"{tid} -> DONE. unlocked: {unlocked or '(none)'}")


def cmd_block(tid, why):
    t = load()
    t[tid]["status"], t[tid]["evidence"] = "BLOCKED", why
    save(t)
    print(f"{tid} -> BLOCKED: {why}")


def cmd_status():
    t = load()
    order = {"DONE": 0, "RUNNING": 1, "TODO": 2, "BLOCKED": 3}
    mark = {"DONE": "[x]", "RUNNING": "[~]", "TODO": "[ ]", "BLOCKED": "[!]"}
    for x in sorted(t.values(), key=lambda x: (order[x["status"]], x["id"])):
        dep = f" <- {','.join(x['deps'])}" if x["deps"] else ""
        print(f"  {mark[x['status']]} {x['id']:3} [{x['role']:10}] {x['title'][:56]}{dep}")
    n = {k: sum(1 for x in t.values() if x["status"] == k) for k in order}
    print(f"\n  done {n['DONE']}/{len(t)}   running {n['RUNNING']}   "
          f"ready {len(frontier(t))}   blocked {n['BLOCKED']}")


if __name__ == "__main__":
    c = sys.argv[1] if len(sys.argv) > 1 else "status"
    if c == "ready":
        cmd_ready()
    elif c == "claim":
        cmd_claim(sys.argv[2], sys.argv[3])
    elif c == "done":
        cmd_done(sys.argv[2], sys.argv[3])
    elif c == "block":
        cmd_block(sys.argv[2], sys.argv[3])
    else:
        cmd_status()
