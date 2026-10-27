#!/usr/bin/env bash
# Build all three images on dark-ai with rootless BuildKit and push them to $REGISTRY.
# Usage: scripts/build.sh [bot|dashboard|opend ...]   (default: all)
source "$(dirname "$0")/common.sh"
targets=("$@"); [ ${#targets[@]} -eq 0 ] && targets=(bot dashboard opend)

k create namespace "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
if ! kn get secret registry-push >/dev/null 2>&1; then
  echo "Secret $NS/registry-push missing. Create it (Docker Hub token) or copy it, see README." >&2
  exit 1
fi

tmp=$(mktemp -d); trap 'rm -rf "$tmp"' EXIT
COPYFILE_DISABLE=1 tar -czf "$tmp/context.tgz" -C "$ROOT" --exclude '__pycache__' --exclude '.DS_Store' \
  claude_trader dashboard config docker requirements-bot.txt requirements-dashboard.txt
kn create configmap build-context --from-file=context.tgz="$tmp/context.tgz" --dry-run=client -o yaml | kn apply -f - >/dev/null

for t in "${targets[@]}"; do
  case "$t" in
    bot) image="$REGISTRY/claude-bot:$TAG"; df=docker/bot.Dockerfile ;;
    dashboard) image="$REGISTRY/claude-dashboard:$TAG"; df=docker/dashboard.Dockerfile ;;
    opend) image="$REGISTRY/claude-opend-runtime:$TAG"; df=docker/opend.Dockerfile ;;
    *) echo "unknown target $t" >&2; exit 1 ;;
  esac
  kn delete job "build-$t" --ignore-not-found --wait >/dev/null
  sed -e "s|__NAME__|$t|g" -e "s|__IMAGE__|$image|" -e "s|__DOCKERFILE__|$df|" "$ROOT/deploy/k8s/build-job.yaml" | kn apply -f - >/dev/null
  echo "Building $image ..."
done
for t in "${targets[@]}"; do
  if kn wait --for=condition=complete "job/build-$t" --timeout=900s >/dev/null 2>&1; then
    echo "built: $t"
  else
    echo "build FAILED: $t (kubectl -n $NS logs job/build-$t)" >&2
    kn logs "job/build-$t" --tail=40 >&2 || true
    exit 1
  fi
done
