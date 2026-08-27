#!/usr/bin/env bash
# Fleet coordination bus. Writes to the shared main checkout, never to a worktree.
set -euo pipefail
ROOT="/Users/nikhilkulkarni/immersive-commons-hackathon/hackathon-p1"
C="$ROOT/coord"
cmd="${1:-read}"

ts() { date -u +%Y-%m-%dT%H:%M:%SZ; }

case "$cmd" in
  status)
    a="$2"; now="$3"; next="${4:-}"; blocked="${5:-—}"
    printf '# Agent %s\nSTATE: %s\nUPDATED: %s\nNOW: %s\nNEXT: %s\nBLOCKED_ON: %s\n' \
      "$a" "$(echo "$now" | grep -qi '^idle' && echo IDLE || echo WORKING)" \
      "$(ts)" "$now" "$next" "$blocked" > "$C/status/$a.md"
    echo "posted status for $a"
    ;;
  log)
    a="$2"; ev="$3"; detail="${4:-}"
    python3 -c 'import json,sys,datetime
print(json.dumps({"ts":datetime.datetime.now(datetime.UTC).isoformat(),"agent":sys.argv[1],"event":sys.argv[2],"detail":sys.argv[3]}))' \
      "$a" "$ev" "$detail" >> "$C/log/$a.jsonl"
    echo "logged $a/$ev"
    ;;
  read)
    echo "===== BOARD ====="; cat "$C/BOARD.md"
    echo; echo "===== CONTRACTS ====="; cat "$C/CONTRACTS.md"
    echo; echo "===== STATUS ====="
    for f in "$C"/status/*.md; do echo "--- $(basename "$f") ---"; cat "$f"; echo; done
    echo "===== RECENT LOG (last 15) ====="
    cat "$C"/log/*.jsonl 2>/dev/null | sort | tail -15
    echo; echo "===== OPEN HITL ====="
    ls -1 "$C/hitl/pending" 2>/dev/null | grep -v '^$' || echo "(none)"
    ;;
  brief)
    for f in "$C"/status/*.md; do
      printf '%-9s %s | %s\n' "$(basename "$f" .md)" \
        "$(grep '^STATE:' "$f" | cut -d' ' -f2-)" "$(grep '^NOW:' "$f" | cut -d' ' -f2-)"
    done
    ;;
  monitor)
    # live fleet dashboard for the human. Ctrl-C to exit.
    while true; do
      clear
      echo "FLEET  $(ts)"
      echo "================================================================"
      for f in "$C"/status/*.md; do
        n=$(basename "$f" .md)
        printf '%-3s %-12s %s\n' "$n" "$(grep '^STATE:' "$f" | cut -d' ' -f2-)" "$(grep '^NOW:' "$f" | cut -d' ' -f2-)"
        b=$(grep '^BLOCKED_ON:' "$f" | cut -d" " -f2-)
        [ "$b" != "—" ] && [ -n "$b" ] && printf '    BLOCKED: %s\n' "$b"
      done
      echo "---------------- open HITL questions ----------------"
      if ls -A "$C/hitl/pending" >/dev/null 2>&1 && [ -n "$(ls -A "$C/hitl/pending" 2>/dev/null)" ]; then
        for q in "$C"/hitl/pending/*.md; do
          "$ROOT/scripts/_fmt.py" question "$q"
        done
        echo "  -> answer:  scripts/coord.sh answer <QID> <choice-number-or-text>"
      else
        echo "  (none)"
      fi
      echo "---------------- last 12 events ----------------"
      cat "$C"/log/*.jsonl 2>/dev/null | sort | tail -12 | "$ROOT/scripts/_fmt.py" log
      sleep 10
    done
    ;;
  answer)
    # answer a HITL question from the terminal, no Telegram needed
    q="$2"; ans="$3"
    f="$C/hitl/pending/$q.md"
    [ -f "$f" ] || { echo "no pending question $q"; exit 1; }
    python3 -c 'import json,sys,time
f,ans=sys.argv[1],sys.argv[2]
d=json.load(open(f))
if ans.isdigit() and d["options"]:
    i=int(ans)-1
    if 0<=i<len(d["options"]): ans=d["options"][i]
d["answer"],d["answered_at"]=ans,time.time()
json.dump(d,open(sys.argv[3],"w"),indent=2)
print("answered "+d["qid"]+" -> "+ans)' "$f" "$ans" "$C/hitl/answered/$q.md"
    rm -f "$f"
    ;;
  sync)
    # commit the coordination trail; safe to run from anywhere, retries on race
    cd "$ROOT"
    git add coord/ >/dev/null 2>&1 || true
    git diff --cached --quiet && { echo "coord: nothing to sync"; exit 0; }
    git commit -q -m "coord: ${2:-fleet status update}" || true
    for i in 1 2 3; do
      git pull --rebase -q origin main 2>/dev/null || true
      git push -q origin main 2>/dev/null && { echo "coord: synced"; exit 0; }
      sleep 2
    done
    echo "coord: committed locally, push failed (non-fatal)"
    ;;
  *) echo "usage: coord.sh {status|log|read|brief|sync} ..."; exit 1 ;;
esac
