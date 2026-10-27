#!/usr/bin/env bash
# Run the backtest inside the bot container (needs OpenD logged in). Report: dashboard > Backtest.
# Usage: scripts/backtest.sh [--years 0.25]   (default: backtest.years in config.yaml, 3)
source "$(dirname "$0")/common.sh"
kn exec deploy/claude-trader -c bot -- python -m claude_trader.backtest "$@"
