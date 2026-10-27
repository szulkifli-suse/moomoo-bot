#!/usr/bin/env bash
# Attach to the OpenD console to log in (account, password, SMS code). Choose to remember the login.
# Detach with Ctrl-P then Ctrl-Q (Ctrl-C would stop OpenD; it restarts on its own).
source "$(dirname "$0")/common.sh"
echo "Attaching to OpenD. Press Enter if the screen is blank. Detach with Ctrl-P Ctrl-Q."
kn attach -it deploy/claude-trader -c opend
