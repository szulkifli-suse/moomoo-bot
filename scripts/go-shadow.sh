#!/usr/bin/env bash
# Back to shadow mode (no real orders).
source "$(dirname "$0")/common.sh"
kn patch configmap claude-config --type merge -p '{"data":{"LIVE_TRADING":"false"}}'
kn rollout restart deploy/claude-trader
echo "LIVE_TRADING=false."
