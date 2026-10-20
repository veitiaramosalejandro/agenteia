#!/bin/sh
set -eu

KUBE_CONTEXT=${KUBE_CONTEXT:-}
NAMESPACE=${NAMESPACE:-agenteia-staging}
SECRET_NAME=${SECRET_NAME:-agenteia-secrets}

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

: "${POSTGRES_USER:?falta POSTGRES_USER}"
: "${POSTGRES_PASSWORD:?falta POSTGRES_PASSWORD}"
: "${DB_URL:?falta DB_URL}"

set -- \
    --from-literal=POSTGRES_USER="$POSTGRES_USER" \
    --from-literal=POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
    --from-literal=DB_URL="$DB_URL"

# Variables opcionales; se añaden solo cuando están exportadas.
for optional_name in \
    OPENAI_API_KEY \
    LLM_CREDENTIAL_ENCRYPTION_KEY \
    CONTROL_CENTER_BOOTSTRAP_PASSWORD; do
    optional_value=$(eval "printf '%s' \"\${$optional_name-}\"")
    if [ -n "$optional_value" ]; then
        set -- "$@" "--from-literal=$optional_name=$optional_value"
    fi
done

kubectl create namespace "$NAMESPACE" --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null

kubectl -n "$NAMESPACE" create secret generic "$SECRET_NAME" \
    "$@" \
    --dry-run=client -o yaml \
    | kubectl apply -f - >/dev/null

echo "Secret $SECRET_NAME preparado en namespace $NAMESPACE"

