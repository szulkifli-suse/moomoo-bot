#!/usr/bin/env bash
# Log in to OpenD: account (email), password, remember = Y, then the SMS code as
#   input_phone_verify_code -code=123456
# Leave with Ctrl-P then Ctrl-Q. Ctrl-C would stop OpenD.
source "$(dirname "$0")/common.sh"
if pgrep -f "kubectl.*attach.*claude-trader.*opend" >/dev/null; then
  echo "Another OpenD login session is already open on this Mac. Leave it first (Ctrl-P Ctrl-Q)." >&2
  exit 1
fi
echo "Attaching to OpenD. Press Enter if the screen is blank."
echo "Leave with Ctrl-P then Ctrl-Q (not Ctrl-C)."
kn attach -it deploy/claude-trader -c opend
