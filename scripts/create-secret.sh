#!/usr/bin/env bash
# Create or update the claude-secrets Secret from YOUR shell environment (never from a file in the repo).
#   ANTHROPIC_API_KEY      required for the Analyst
#   DASHBOARD_PASSWORD     optional; a random one is generated if unset (printed once)
#   TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID   optional; recaps are only logged without them
#   MOOMOO_LOGIN_ACCOUNT   optional; your moomoo ID, lets OpenD reuse the remembered login on restarts
#   MOOMOO_TRADE_PIN       needed only for live trading
source "$(dirname "$0")/common.sh"
: "${ANTHROPIC_API_KEY:?export ANTHROPIC_API_KEY first}"
if [ -z "${DASHBOARD_PASSWORD:-}" ]; then
  existing=$(kn get secret claude-secrets -o jsonpath='{.data.DASHBOARD_PASSWORD}' 2>/dev/null | base64 -d || true)
  if [ -n "$existing" ]; then DASHBOARD_PASSWORD=$existing; else
    DASHBOARD_PASSWORD=$(openssl rand -base64 18 | tr -d '/+=')
    echo "Generated dashboard password: $DASHBOARD_PASSWORD"
  fi
fi
args=(--from-literal=ANTHROPIC_API_KEY="$ANTHROPIC_API_KEY" --from-literal=DASHBOARD_PASSWORD="$DASHBOARD_PASSWORD")
for v in TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID MOOMOO_LOGIN_ACCOUNT MOOMOO_TRADE_PIN; do
  [ -n "${!v:-}" ] && args+=(--from-literal="$v=${!v}")
done
k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
kn create secret generic claude-secrets "${args[@]}" --dry-run=client -o yaml | kn apply -f -
kn rollout restart deploy/claude-trader 2>/dev/null || true
echo "Secret updated."
