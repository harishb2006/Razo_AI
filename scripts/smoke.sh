#!/usr/bin/env bash
# End-to-end smoke test. Works against local, container or deployed.
#
#   ./scripts/smoke.sh                          # localhost:8000
#   ./scripts/smoke.sh https://your.onrender.com dev-local-key
#
# Exits non-zero the moment a guardrail behaves differently than promised,
# so CI and a pre-demo check are the same command.
set -uo pipefail

BASE="${1:-http://127.0.0.1:8000}"
KEY="${2:-${API_KEY:-dev-local-key}}"
API="$BASE/api/v1"
PASS=0; FAIL=0

c()  { curl -s -m 20 -H "X-API-Key: $KEY" "$@"; }
cj() { curl -s -m 20 -H "X-API-Key: $KEY" -H 'Content-Type: application/json' "$@"; }
jget() { python3 -c "import json,sys
try: print(json.load(sys.stdin).get('$1',''))
except Exception: print('')"; }

check() { # check <label> <expected-substring> <actual>
  if [[ "$3" == *"$2"* ]]; then echo "  PASS  $1"; PASS=$((PASS+1))
  else echo "  FAIL  $1"; echo "        expected to contain: $2"; echo "        got: ${3:0:200}"; FAIL=$((FAIL+1)); fi
}

newsession() { cj -X POST "$API/chat/sessions" -d '{}' | jget session_id; }

echo "Razo_AI smoke test -> $BASE"
echo

echo "[1/7] Service is up"
check "/health returns ok"            '"status":"ok"'   "$(c "$BASE/health")"
check "/health/ready knows about db"  '"database"'      "$(c "$BASE/health/ready")"

echo "[2/7] Machine-readable to an AI buyer"
check "agent manifest is served"      '"endpoints"'     "$(c "$BASE/.well-known/agent-catalog.json")"

echo "[3/7] Catalog is real"
check "products come back"            '"sku"'           "$(c "$API/catalog/products?limit=3")"
check "categories come back"          '"category"'      "$(c "$API/catalog/categories")"

echo "[4/7] A normal purchase clears"
S=$(newsession)
cj -X POST "$API/cart/$S/items" -d '{"sku":"RZ-FOOT-108","qty":1}' >/dev/null
check "cheap cart creates a payment link" '"paid_link_created"' "$(cj -X POST "$API/checkout/$S" -d '{}')"

echo "[5/7] Spend above the threshold is escalated, not paid"
S=$(newsession)
cj -X POST "$API/cart/$S/items" -d '{"sku":"RZ-APPA-116","qty":1}' >/dev/null
R=$(cj -X POST "$API/checkout/$S" -d '{}')
check "goes to the merchant for approval" '"approval_required"' "$R"
[[ "$R" == *payment_link_url* ]] && { echo "  FAIL  a link WAS created above the threshold"; FAIL=$((FAIL+1)); } \
                                 || { echo "  PASS  no payment link above the threshold"; PASS=$((PASS+1)); }

echo "[6/7] Spend above the hard cap is refused outright"
S=$(newsession)
cj -X POST "$API/cart/$S/items" -d '{"sku":"RZ-APPA-116","qty":3}' >/dev/null
R=$(cj -X POST "$API/checkout/$S" -d '{}')
check "denied"                 '"denied"' "$R"
check "cites the cap rule"     '"R1"'     "$R"

echo "[7/7] The log cannot have been edited"
check "audit chain intact"     '"ok":true' "$(c "$API/audit/verify")"

echo
echo "passed: $PASS   failed: $FAIL"
[[ $FAIL -eq 0 ]] || exit 1
echo "All good."
