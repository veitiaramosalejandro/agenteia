#!/bin/sh
set -eu

IMAGE_PREFIX=${IMAGE_PREFIX:-agenteia}
IMAGE_TAG=${IMAGE_TAG:-staging}

command -v docker >/dev/null 2>&1 || {
    echo "ERROR: docker no está instalado" >&2
    exit 1
}
command -v sudo >/dev/null 2>&1 || {
    echo "ERROR: sudo no está instalado" >&2
    exit 1
}

docker image inspect \
    "${IMAGE_PREFIX}/agent-service:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/agent-control-center:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/db-migrations:${IMAGE_TAG}" \
    >/dev/null || {
        echo "ERROR: falta construir alguna imagen de staging" >&2
        echo "Ejecuta deploy/scripts/build-import-k3s-images.sh" >&2
        exit 1
    }

docker save \
    "${IMAGE_PREFIX}/agent-service:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/agent-control-center:${IMAGE_TAG}" \
    "${IMAGE_PREFIX}/db-migrations:${IMAGE_TAG}" \
    | sudo k3s ctr images import -

sudo k3s ctr images list | grep -E \
    "${IMAGE_PREFIX}/(agent-service|agent-control-center|db-migrations):${IMAGE_TAG}"

