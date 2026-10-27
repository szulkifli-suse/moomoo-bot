#!/usr/bin/env bash
source "$(dirname "$0")/common.sh"
kn get secret claude-secrets -o jsonpath='{.data.DASHBOARD_PASSWORD}' | base64 -d; echo
