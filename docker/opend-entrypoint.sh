#!/bin/bash
# Downloads moomoo OpenD (command-line build) once onto the volume, then runs it.
# First login is interactive: kubectl attach -it into this container and type your
# moomoo account, password and SMS code. OpenD remembers the login on the volume after that.
set -euo pipefail

V="${OPEND_VERSION:?OPEND_VERSION not set}"
ROOT=/opend
BASE="moomoo_OpenD_${V}_Ubuntu18.04"
BIN_DIR="$ROOT/bin/$V"
export HOME="$ROOT/home"
mkdir -p "$HOME" "$ROOT/logs" "$ROOT/bin"

if [ ! -x "$BIN_DIR/OpenD" ]; then
  echo "Downloading moomoo OpenD $V from softwaredownload.futustatic.com (about 470 MB)..."
  tmp="$ROOT/download"
  rm -rf "$tmp" && mkdir -p "$tmp"
  curl -fSL --retry 3 -o "$tmp/opend.tar.gz" "https://softwaredownload.futustatic.com/${BASE}.tar.gz"
  tar -xzf "$tmp/opend.tar.gz" -C "$tmp" "$BASE/$BASE"
  rm -rf "$BIN_DIR" && mv "$tmp/$BASE/$BASE" "$BIN_DIR"
  rm -rf "$tmp"
  echo "OpenD $V installed."
fi

# OpenD keeps its login cache in /tmp/F3CNN. Keep it on the volume so restarts stay logged in.
mkdir -p "$ROOT/state/F3CNN" && chmod 700 "$ROOT/state"
if [ ! -L /tmp/F3CNN ]; then rm -rf /tmp/F3CNN; ln -s "$ROOT/state/F3CNN" /tmp/F3CNN; fi

cd "$BIN_DIR"
args=(-lang=en -api_ip=127.0.0.1 -api_port=11111 -log_level=info -no_monitor=1 -console=1 -log_path="$ROOT/logs")
# 1) Automatic login: MOOMOO_LOGIN_ACCOUNT + MOOMOO_LOGIN_PWD_MD5 from the Secret
#    (set with scripts/set-moomoo-login.sh). If moomoo asks for an SMS code, run
#    scripts/opend-login.sh and type: input_phone_verify_code -code=123456
if [ -n "${MOOMOO_LOGIN_ACCOUNT:-}" ] && [ -n "${MOOMOO_LOGIN_PWD_MD5:-}" ]; then
  echo "Logging in automatically as the account in claude-secrets."
  exec ./OpenD "${args[@]}" -login_account="$MOOMOO_LOGIN_ACCOUNT" -login_pwd_md5="$MOOMOO_LOGIN_PWD_MD5"
fi

# Reuse the remembered login: MOOMOO_LOGIN_ACCOUNT if set, else the account name you logged in
# with (email or phone before the numeric moomoo ID) from the login cache. If a remembered login
# failed and nobody has logged in since, start the normal interactive login instead of looping.
STATE="$ROOT/state"
account="${MOOMOO_LOGIN_ACCOUNT:-}"
if [ -z "$account" ]; then
  names=$(ls "$STATE/F3CNN" 2>/dev/null | sed -n 's/^\(.*\)_UserLogin_MM$/\1/p')
  account=$(printf '%s\n' "$names" | grep -v '^[0-9]*$' | head -1)
  [ -z "$account" ] && account=$(printf '%s\n' "$names" | head -1)
fi
cache=$(ls -t "$STATE"/F3CNN/*_UserLogin_MM 2>/dev/null | head -1)
use_remember=0
if [ -n "$account" ] && [ -n "$cache" ]; then
  if [ ! -f "$STATE/remember_failed" ] || [ "$cache" -nt "$STATE/remember_failed" ]; then
    use_remember=1
  fi
fi
if [ "$use_remember" = 1 ]; then
  echo "Logging in with the remembered login."
  args+=(-login_account="$account" -login_by_remember=1)
  started=$(date +%s)
  set +e
  ./OpenD "${args[@]}"
  code=$?
  set -e
  if [ $(( $(date +%s) - started )) -lt 180 ]; then
    echo "Remembered login failed; next start will wait for a manual login (scripts/opend-login.sh)."
    touch "$STATE/remember_failed"
  fi
  exit "$code"
fi
echo "Starting OpenD. If this is the first run, attach and log in:"
echo "  kubectl -n claude-trading attach -it deploy/claude-trader -c opend"
exec ./OpenD "${args[@]}"
