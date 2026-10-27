#!/usr/bin/env bash
# Switch to LIVE trading. Only after the shadow run and the go-live gate, and after you decided to.
source "$(dirname "$0")/common.sh"
read -r -p 'Type "go live" to place real orders with real money: ' answer
[ "$answer" = "go live" ] || { echo "Cancelled."; exit 1; }
kn get secret claude-secrets -o jsonpath='{.data.MOOMOO_TRADE_PIN}' | grep -q . \
  || { echo "MOOMOO_TRADE_PIN is not in claude-secrets; add it with create-secret.sh first." >&2; exit 1; }
kn patch configmap claude-config --type merge -p '{"data":{"LIVE_TRADING":"true"}}'
kn rollout restart deploy/claude-trader
echo "LIVE_TRADING=true. The pod is restarting."
