#!/usr/bin/env bash
# Follow logs. Usage: scripts/logs.sh [bot|opend|dashboard]
source "$(dirname "$0")/common.sh"
kn logs -f deploy/claude-trader -c "${1:-bot}" --tail=200
