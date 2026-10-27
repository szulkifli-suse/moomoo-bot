#!/usr/bin/env bash
# Run the backtest inside the bot container (needs OpenD logged in). Report: dashboard > Backtest.
source "$(dirname "$0")/common.sh"
kn exec deploy/claude-trader -c bot -- python -m claude_trader.backtest
