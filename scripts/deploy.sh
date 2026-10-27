#!/usr/bin/env bash
# Apply everything to dark-ai. Keeps the current LIVE_TRADING value (new installs start in shadow mode).
source "$(dirname "$0")/common.sh"
M="$ROOT/deploy/k8s"

k apply -f "$M/namespace.yaml"
live=$(kn get configmap claude-config -o jsonpath='{.data.LIVE_TRADING}' 2>/dev/null || true)
live=${live:-false}
kn create configmap claude-config --from-file=config.yaml="$ROOT/config/config.yaml" \
  --from-literal=LIVE_TRADING="$live" --dry-run=client -o yaml | kn apply -f -
sha=$( (cat "$ROOT/config/config.yaml"; echo "$live") | shasum -a 256 | cut -c1-16)

k apply -f "$M/pvc.yaml" -f "$M/service.yaml" -f "$M/ingress.yaml" -f "$M/networkpolicy.yaml"
sed -e "s|__TAG__|$TAG|g" -e "s|__CONFIG_SHA__|$sha|" "$M/deployment.yaml" | k apply -f -
echo "LIVE_TRADING=$live. Waiting for the pod..."
kn rollout status deploy/claude-trader --timeout=240s || true
kn get pods -o wide
