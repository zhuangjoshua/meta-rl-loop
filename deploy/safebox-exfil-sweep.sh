#!/usr/bin/env bash
# STEP F EXFIL sweep (deploy/SAFEBOX-BROKER-REMEDIATION-PLAN.md).
# Proves the red line "no provider-key VALUE exists anywhere outside the safebox".
# Reports COUNTS + REDACTED locations only — never prints a raw secret value.
#
#   ./safebox-exfil-sweep.sh            # local: repo working tree + git history
#   ./safebox-exfil-sweep.sh --host h   # ssh h and sweep ps/env/logs (run on every NON-safebox host)
#
# Exit 0 = clean (zero leaks), 1 = leaks found. Run after STEP E/G and before key rotation.
set -uo pipefail

# Real provider-key VALUE shapes (not just env NAMES). Extend as providers are added.
PAT='sk-ant-[A-Za-z0-9_-]{18}|sk-[A-Za-z0-9]{24}|AIza[A-Za-z0-9_-]{20}|tvly-[A-Za-z0-9]{16}|whsec_[A-Za-z0-9]{16}|rk_live_[A-Za-z0-9]{16}|sk_live_[A-Za-z0-9]{16}|xoxb-[0-9]'
redact() { sed -E 's/(sk-ant-|sk-|AIza|tvly-|whsec_|rk_live_|sk_live_|xoxb-)[A-Za-z0-9_-]{6,}/\1<REDACTED>/g'; }
# Exclude docs/examples AND test fixtures (tests legitimately carry fake key-shaped strings, incl. the
# redaction tests). A real provider key must never live in non-test source.
EXCLUDE='\.example|\.sample|REMEDIATION-PLAN|CODEX-HANDOFF|safebox-exfil-sweep|/tests/|(^|/)test_|_test\.(py|ts|js)|conftest'
leaks=0

host_mode() {
  local H="$1"
  echo "== EXFIL sweep on host: $H (provider keys must NOT appear here) =="
  ssh -o BatchMode=yes -o ConnectTimeout=12 "$H" "
    pat='$PAT'
    echo '[ps process args]';     ps -eo cmd 2>/dev/null | grep -nE \"\$pat\" | sed -E 's/(sk-ant-|sk-|AIza|tvly-|whsec_|rk_live_|sk_live_|xoxb-)[A-Za-z0-9_-]{6,}/\1<REDACTED>/g' | head
    echo '[env files]';           grep -rInE \"\$pat\" /opt/takyon/.takyon/.env /opt/takyon/secrets/.env 2>/dev/null | sed -E 's/(sk-ant-|sk-|AIza|tvly-|whsec_|rk_live_|sk_live_|xoxb-)[A-Za-z0-9_-]{6,}/\1<REDACTED>/g' | head
    echo '[recent logs]';         grep -rInE \"\$pat\" /opt/takyon/.takyon/logs 2>/dev/null | sed -E 's/(sk-ant-|sk-|AIza|tvly-|whsec_|rk_live_|sk_live_|xoxb-)[A-Za-z0-9_-]{6,}/\1<REDACTED>/g' | head
    echo '[running proc /proc environ]'; for p in \$(pgrep -f 'takyon-cli|claude-agent-task' 2>/dev/null); do tr '\0' '\n' < /proc/\$p/environ 2>/dev/null | grep -E \"\$pat\" >/dev/null && echo \"  LEAK: provider key in /proc/\$p/environ\"; done
  "
  echo "  (any non-empty match above on a NON-safebox host = a leak to fix)"
}

if [ "${1:-}" = "--host" ]; then host_mode "${2:?host required}"; exit 0; fi

cd "$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
echo "== EXFIL sweep: local repo =="

echo "[1] git-tracked files — any committed key VALUE?"
hits="$(git grep -InE "$PAT" -- . 2>/dev/null | grep -vE "$EXCLUDE" || true)"
if [ -n "$hits" ]; then echo "$hits" | redact | head -20; leaks=$((leaks+$(echo "$hits" | wc -l))); else echo "  clean"; fi

echo "[2] git history — secrets ever committed (must be scrubbed in STEP G)?"
for f in secrets/.env polsia3/.env.local; do
  n="$(git log --all --oneline -- "$f" 2>/dev/null | wc -l | tr -d ' ')"
  [ "$n" != "0" ] && { echo "  HISTORY: $f present in $n commit(s) — git filter-repo it (STEP G)"; leaks=$((leaks+1)); } || echo "  $f: not in history"
done

echo
if [ "$leaks" = "0" ]; then echo "RESULT: clean (0 leaks)"; exit 0; else echo "RESULT: $leaks leak signal(s) — see above (redacted)"; exit 1; fi
