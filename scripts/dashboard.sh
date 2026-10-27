#!/usr/bin/env bash
# Open the dashboard through a local tunnel at http://localhost:8080 (no public DNS needed).
source "$(dirname "$0")/common.sh"
echo "Dashboard: http://localhost:8080 (Ctrl-C to stop)"
COOKIE_NOTE="If login loops on http, use the HTTPS hostname instead; the session cookie is Secure."
echo "$COOKIE_NOTE"
kn port-forward svc/claude-dashboard 8080:80
