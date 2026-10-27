# Shared settings for the scripts. Override with env vars:
#   KUBECONFIG (kubeconfig for dark-ai), KUBE_CONTEXT (default dark-ai), TAG (image tag)
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
NS=claude-trading
CTX=${KUBE_CONTEXT:-dark-ai}
TAG=${TAG:-0.1.6}
REGISTRY=${REGISTRY:-docker.io/darkwunan}
k() { kubectl --context "$CTX" "$@"; }
kn() { kubectl --context "$CTX" -n "$NS" "$@"; }
