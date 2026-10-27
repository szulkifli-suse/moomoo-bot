#!/usr/bin/env bash
# Kill switch OFF.
source "$(dirname "$0")/common.sh"
kn exec deploy/claude-trader -c bot -- sh -c 'rm -f /app/data/STOP && echo "resumed: STOP file removed"'
