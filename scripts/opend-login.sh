#!/usr/bin/env bash
# Log in to OpenD (account, password, SMS code). OpenD runs without a TTY, so leaving with
# Ctrl-C or closing this terminal does not stop it.
# SMS code: type   input_phone_verify_code -code=123456
source "$(dirname "$0")/common.sh"
echo "Attaching to OpenD. Type your answers and press Enter (the password is shown as you type)."
echo "SMS code: input_phone_verify_code -code=<code>. When it says Login successful, press Ctrl-C."
kn attach -i deploy/claude-trader -c opend
