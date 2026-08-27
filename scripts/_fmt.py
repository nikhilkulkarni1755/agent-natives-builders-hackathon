#!/usr/bin/env python3
"""Formatting helpers for coord.sh monitor. Kept out of the shell to avoid quote mangling."""
import json
import sys

mode = sys.argv[1]

if mode == "log":
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            d = json.loads(line)
        except Exception:
            continue
        print(f"  {d['ts'][11:19]}  {d['agent']}  {d['event']}: {d['detail'][:70]}")

elif mode == "question":
    d = json.load(open(sys.argv[2]))
    print(f"  [{d['qid']}] Agent {d['agent']}: {d['question']}")
    for i, o in enumerate(d["options"]):
        print(f"       {i+1}) {o}")
