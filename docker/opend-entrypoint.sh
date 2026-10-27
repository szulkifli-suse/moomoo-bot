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

cd "$BIN_DIR"
args=(-lang=en -api_ip=127.0.0.1 -api_port=11111 -log_level=info -no_monitor=1 -console=1 -log_path="$ROOT/logs")
if [ -n "${MOOMOO_LOGIN_ACCOUNT:-}" ]; then
  args+=(-login_account="$MOOMOO_LOGIN_ACCOUNT" -login_by_remember=1)
fi
echo "Starting OpenD. If this is the first run, attach and log in:"
echo "  kubectl -n claude-trading attach -it deploy/claude-trader -c opend"
exec ./OpenD "${args[@]}"
