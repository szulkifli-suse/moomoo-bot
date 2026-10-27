#!/usr/bin/env bash
# Store your moomoo login for OpenD's automatic login. Asks on this Mac with hidden input and
# stores only the account and the MD5 hash of the password (OpenD's login_pwd_md5) in the Secret.
# The plain password is never stored or printed. The pod restarts afterwards.
source "$(dirname "$0")/common.sh"
read -r -p "moomoo account (email or phone with +country code): " acct
read -rs -p "moomoo password (hidden): " pw; echo
read -rs -p "Same password again: " pw2; echo
[ -n "$acct" ] && [ -n "$pw" ] || { echo "Account and password are required." >&2; exit 1; }
[ "$pw" = "$pw2" ] || { echo "Passwords don't match." >&2; exit 1; }
md5=$(printf '%s' "$pw" | md5 2>/dev/null || printf '%s' "$pw" | md5sum | cut -d' ' -f1)
unset pw pw2
python3 -c 'import json,sys; print(json.dumps({"stringData": {"MOOMOO_LOGIN_ACCOUNT": sys.argv[1], "MOOMOO_LOGIN_PWD_MD5": sys.stdin.read().strip()}}))' "$acct" <<<"$md5" \
  | kn patch secret claude-secrets --type merge --patch-file /dev/stdin
unset md5
kn rollout restart deploy/claude-trader
echo "Saved. OpenD will log in by itself in about a minute."
echo "If moomoo asks for an SMS code: scripts/opend-login.sh, then type: input_phone_verify_code -code=<code>"
