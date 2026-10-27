#!/usr/bin/env bash
# Switch to LIVE trading. Only after the shadow run and the go-live gate, and after you decided to.
source "$(dirname "$0")/common.sh"
read -r -p 'Type "go live" to place real orders with real money: ' answer
[ "$answer" = "go live" ] || { echo "Cancelled."; exit 1; }
if kn get configmap claude-config -o jsonpath='{.data.config\.yaml}' | grep -q '^  allow_fractional: true'; then
  echo "Fractional shares are on. moomoo's API support for them is not confirmed for this account."
  read -r -p 'Did one small REAL fractional order succeed in moomoo through the API? Type "tested": ' t
  [ "$t" = "tested" ] || { echo "Cancelled. Test a fractional order first, or set allow_fractional: false."; exit 1; }
fi
kn get secret claude-secrets -o jsonpath='{.data.MOOMOO_TRADE_PIN}' | grep -q . \
  || { echo "MOOMOO_TRADE_PIN is not in claude-secrets; add it with create-secret.sh first." >&2; exit 1; }
kn patch configmap claude-config --type merge -p '{"data":{"LIVE_TRADING":"true"}}'
kn rollout restart deploy/claude-trader
echo "LIVE_TRADING=true. The pod is restarting."
