#!/usr/bin/env bash
# Submission readiness gate. Run this before you hit submit.
#
#   ./scripts/preflight.sh
#
# Checks the things that would actually sink the submission: work not pushed,
# a secret committed, a claim in the README that no longer holds.
set -uo pipefail
cd "$(dirname "$0")/.."

PASS=0; FAIL=0; WARN=0
ok()   { echo "  PASS  $1"; PASS=$((PASS+1)); }
bad()  { echo "  FAIL  $1"; FAIL=$((FAIL+1)); }
warn() { echo "  WARN  $1"; WARN=$((WARN+1)); }

PY=backend/venv/bin/python
[[ -x $PY ]] || PY=python3

echo "Razo_AI preflight"
echo

echo "[1/6] Git state"
[[ -z "$(git status --porcelain)" ]] && ok "working tree is clean" \
                                     || bad "uncommitted changes — commit them or a judge won't see them"
BR=$(git rev-parse --abbrev-ref HEAD)
[[ "$BR" == "main" ]] && ok "on main" || warn "on '$BR', not main — judges clone main"
git fetch -q origin 2>/dev/null
AHEAD=$(git rev-list --count origin/main..HEAD 2>/dev/null || echo "?")
[[ "$AHEAD" == "0" ]] && ok "main is pushed to origin" \
                      || bad "$AHEAD commit(s) not pushed — run: git push origin main"

echo "[2/6] No secrets committed"
git ls-files | grep -qE '(^|/)\.env$' && bad "a .env file is tracked in git" \
                                      || ok ".env files are untracked"
LEAK=$(git grep -InE '(rzp_(test|live)_[A-Za-z0-9]{10,}|AIza[A-Za-z0-9_-]{30,}|gsk_[A-Za-z0-9]{20,}|mongodb\+srv://[^<>"'"'"' ]*:[^<>@"'"'"' ]+@)' -- \
        ':!*.lock' ':!*package-lock.json' 2>/dev/null | head -5)
[[ -z "$LEAK" ]] && ok "no API keys or connection strings in tracked files" \
                 || { bad "possible secret committed:"; echo "$LEAK" | sed 's/^/        /'; }

echo "[3/6] Backend tests"
if OFFLINE_MODE=True $PY -m pytest backend/tests -q >/tmp/razo_tests.log 2>&1 \
   || (cd backend && OFFLINE_MODE=True ../$PY -m pytest tests -q >/tmp/razo_tests.log 2>&1); then
  ok "$(grep -oE '[0-9]+ passed' /tmp/razo_tests.log | tail -1) (offline, no keys)"
else
  bad "tests failing — see /tmp/razo_tests.log"; tail -5 /tmp/razo_tests.log | sed 's/^/        /'
fi

echo "[4/6] Eval harness (the numbers in reports/metrics.md)"
if (cd backend && OFFLINE_MODE=True ../$PY -m scripts.run_eval >/tmp/razo_eval.log 2>&1); then
  ok "24-persona run passed both hard gates"
  # The report restamps its timestamp, git SHA and latency jitter on every run.
  # If that is *all* that moved, put the file back rather than leave the repo
  # dirty — but if a real number changed, keep it so it gets committed.
  VOLATILE_ONLY=1
  while IFS= read -r line; do
    case "$line" in
      [+-][+-]*) continue ;;
      [+-]*"Run at"*|[+-]*"Git SHA"*|[+-]*"Duration"*|[+-]*"latency"*) continue ;;
      [+-]*) VOLATILE_ONLY=0 ;;
    esac
  done < <(git diff -U0 -- reports/metrics.md)
  if [[ -n "$(git status --porcelain reports/metrics.md)" ]]; then
    if [[ $VOLATILE_ONLY -eq 1 ]]; then
      git checkout -- reports/metrics.md
    else
      warn "eval numbers changed — commit the refreshed reports/metrics.md"
    fi
  fi
else
  bad "eval harness failed — the README's numbers no longer hold"
fi

echo "[5/6] Client builds"
if [[ -d client/node_modules ]]; then
  (cd client && npm run build >/tmp/razo_build.log 2>&1) && ok "client builds" \
    || { bad "client build failed — see /tmp/razo_build.log"; tail -5 /tmp/razo_build.log | sed 's/^/        /'; }
else
  warn "client/node_modules missing — run: cd client && npm install"
fi

echo "[6/6] Submission artefacts"
for f in README.md docs/HLD.md docs/LLD.md docs/ARCHITECTURE.md docs/DEPLOY.md reports/metrics.md render.yaml .github/workflows/ci.yml backend/Dockerfile; do
  [[ -f "$f" ]] && ok "$f" || bad "$f is missing"
done

echo
echo "passed: $PASS   warnings: $WARN   failed: $FAIL"
echo
if [[ $FAIL -eq 0 ]]; then
  cat <<'NOTE'
Code side is ready. Still on you:
  - 5-minute pitch video recorded and linked in the README  (script: docs/PITCH.md)
  - deployed URLs linked in the README                      (guide:  docs/DEPLOY.md)
  - submitted before 5 September 2026
NOTE
else
  echo "Fix the failures above first."
  exit 1
fi
