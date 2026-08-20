#!/usr/bin/env bash
# Merge a users.json backup back into the live store, without losing either side.
#
# 2026-08-20: /users showed 2 accounts; runeclaw-BEFORE-20260802/data/users.json
# holds 18. The live 2 are not junk — they carry whatever role, tier and wallet
# state has been set since — so this MERGES rather than overwriting: every user
# from the backup that the live store has never heard of is added, and every
# live record is kept exactly as it is.
#
# It never writes in place. It produces a candidate file and prints the counts,
# and YOU move it into place after the bot is stopped.
set -euo pipefail

LIVE="${LIVE:-$HOME/runeclaw-persist/data/users.json}"
BACKUP="${BACKUP:-$HOME/runeclaw-BEFORE-20260802/data/users.json}"
OUT="${OUT:-$HOME/users.json.merged}"

for f in "$LIVE" "$BACKUP"; do
  [ -f "$f" ] || { echo "missing: $f" >&2; exit 1; }
done

if pgrep -f 'bot\.main' >/dev/null; then
  echo "REFUSING: the bot is running (pid $(pgrep -f 'bot\.main' | tr '\n' ' '))." >&2
  echo "It saves users.json on the message path, so it would overwrite the" >&2
  echo "restore with its current two-user map. Stop it, then re-run." >&2
  exit 1
fi

python3 - "$LIVE" "$BACKUP" "$OUT" <<'PY'
import json, sys, shutil, time
live_p, back_p, out_p = sys.argv[1:4]
live = json.load(open(live_p))
back = json.load(open(back_p))

# Live wins on every key it holds: it is newer, and its records carry role,
# tier and wallet state set since the backup. The backup only contributes
# people the live store has never heard of.
merged = dict(back)
merged.update(live)

added = sorted(set(merged) - set(live))
print(f"live:    {len(live):>3} users")
print(f"backup:  {len(back):>3} users")
print(f"merged:  {len(merged):>3} users  (+{len(added)} recovered)")
if added:
    print("recovered ids:", ", ".join(added))
kept = sorted(set(live) & set(back))
if kept:
    print(f"{len(kept)} id(s) in both — LIVE record kept, backup ignored")

json.dump(merged, open(out_p, "w"), indent=2, default=str)
# Re-read what was written rather than trusting the dict in memory.
check = json.load(open(out_p))
assert len(check) == len(merged), "written file does not match"
print(f"\nwrote {out_p} ({len(check)} users, verified by re-reading)")
PY

cat <<EOF

Next, with the bot still stopped:

  cp "$LIVE" "$LIVE.pre-merge-\$(date +%Y%m%d%H%M%S)"   # keep the current one
  cp "$OUT" "$LIVE"
  python3 -c "import json;print(len(json.load(open('$LIVE'))),'users in place')"

Then start the bot and run /users. If it prints the "started empty" warning,
stop and re-check the symlink before letting it write.
EOF
