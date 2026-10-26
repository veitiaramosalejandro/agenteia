#!/bin/sh
set -eu

IMAGE_PREFIX=${IMAGE_PREFIX:-agenteia}
IMAGE_TAG=${IMAGE_TAG:-staging}
MIGRATION_TAG=${MIGRATION_TAG:-staging}

command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker no está instalado" >&2
    exit 1
}

command -v sudo >/dev/null 2>&1 || {
    echo "ERROR: sudo no está instalado" >&2
    exit 1
}

echo "Construyendo agent-service:${IMAGE_TAG}"
docker build \
    -t "${IMAGE_PREFIX}/agent-service:${IMAGE_TAG}" \
    ./agent-service

echo "Construyendo agent-control-center:${IMAGE_TAG}"
docker build \
    -t "${IMAGE_PREFIX}/agent-control-center:${IMAGE_TAG}" \
    ./agent-control-center

echo "Construyendo db-migrations:${MIGRATION_TAG}"
docker build \
    -t "${IMAGE_PREFIX}/db-migrations:${MIGRATION_TAG}" \
    -f deploy/k8s/migrations/Dockerfile .

echo "Importando imágenes en el containerd de K3s"
docker save \
    "${IMAGE_PREFIX}/agent-service:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/agent-control-center:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/db-migrations:${MIGRATION_TAG}" \
    | sudo k3s ctr images import -

echo "Imágenes importadas"
sudo k3s ctr images list | grep -E \
    "${IMAGE_PREFIX}/(agent-service|agent-control-center|db-migrations):"

