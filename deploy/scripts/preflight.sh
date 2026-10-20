#!/bin/sh
set -eu

KUBE_CONTEXT=${KUBE_CONTEXT:-}
NAMESPACE=${NAMESPACE:-agenteia-prod}
KUSTOMIZE_DIR=${KUSTOMIZE_DIR:-deploy/k8s/overlays/production}
REQUIRE_GPU=${REQUIRE_GPU:-true}

if [ -z "${KUBECONFIG:-}" ] && [ -f "$HOME/.kube/config" ]; then
    export KUBECONFIG="$HOME/.kube/config"
fi

fail() {
    echo "ERROR: $*" >&2
    exit 1
}

command -v kubectl >/dev/null 2>&1 || fail "kubectl no está instalado"

if [ -n "$KUBE_CONTEXT" ]; then
    kubectl config use-context "$KUBE_CONTEXT" >/dev/null \
        || fail "no se pudo seleccionar el contexto $KUBE_CONTEXT"
fi

CURRENT_CONTEXT=$(kubectl config current-context 2>/dev/null || true)
[ -n "$CURRENT_CONTEXT" ] || fail "no hay contexto Kubernetes seleccionado"
echo "contexto: $CURRENT_CONTEXT"

kubectl cluster-info >/dev/null 2>&1 \
    || fail "el clúster no responde"
kubectl get nodes >/dev/null \
    || fail "no se pudieron consultar los nodos"

kubectl kustomize "$KUSTOMIZE_DIR" >/dev/null \
    || fail "Kustomize no puede construir $KUSTOMIZE_DIR"
echo "manifiestos: OK ($KUSTOMIZE_DIR)"

kubectl get storageclass >/dev/null \
    || fail "no hay StorageClass consultable"
echo "storage: OK"

if [ "$REQUIRE_GPU" = "true" ]; then
    GPU_COUNT=$(kubectl get nodes -o json | python3 -c '
import json, sys
payload = json.load(sys.stdin)
total = 0
for node in payload.get("items", []):
    total += int(node.get("status", {}).get("allocatable", {}).get("nvidia.com/gpu", "0"))
print(total)
')
    [ "$GPU_COUNT" -ge 1 ] \
        || fail "no hay GPU nvidia.com/gpu disponible"
    echo "GPU: OK ($GPU_COUNT disponible(s))"
fi

if kubectl get namespace "$NAMESPACE" >/dev/null 2>&1; then
    kubectl get secret agenteia-secrets -n "$NAMESPACE" >/dev/null 2>&1 \
        || fail "falta el Secret agenteia-secrets en $NAMESPACE"
    echo "secretos: OK"
else
    echo "namespace $NAMESPACE aún no existe; se validará al aplicar"
fi

echo "preflight completado correctamente"

