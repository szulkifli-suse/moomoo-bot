#!/usr/bin/env bash
# Kill switch ON: no new positions, open buy orders cancelled, stops keep running.
source "$(dirname "$0")/common.sh"
kn exec deploy/claude-trader -c bot -- sh -c 'date -u > /app/data/STOP && echo "halted: STOP file created"'
