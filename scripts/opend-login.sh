#!/usr/bin/env bash
# Log in to OpenD (account, password, SMS code). OpenD runs without a TTY, so leaving with
# Ctrl-C or closing this terminal does not stop it.
# SMS code: type   input_phone_verify_code -code=123456
source "$(dirname "$0")/common.sh"
if pgrep -f "kubectl.*attach.*claude-trader.*opend" >/dev/null; then
  echo "Another OpenD login session is already open on this Mac. Close it first (Ctrl-C in that tab)." >&2
  exit 1
fi
cat <<'MSG'
Attached to OpenD. Wait for each prompt, then type and press Enter:
  account -> your moomoo email     password -> your password     remember -> Y
  SMS code -> input_phone_verify_code -code=123456
When it says "Login successful", press Ctrl-C (OpenD keeps running).
If no prompt appears, press Enter once and wait.
MSG
# OpenD pads its output with long runs of spaces; squeeze them so the screen stays readable.
kn attach -i deploy/claude-trader -c opend 2>/dev/null | python3 -u -c '
import re, sys
while True:
    chunk = sys.stdin.buffer.read1(4096)
    if not chunk:
        break
    sys.stdout.buffer.write(re.sub(rb" {2,}", b" ", chunk))
    sys.stdout.buffer.flush()
'
