#!/bin/sh
set -eu

[ "$(id -u)" -eq 0 ] || {
    echo "ERROR: ejecutar este script con sudo o como root" >&2
    exit 1
}

command -v nvidia-container-runtime >/dev/null 2>&1 || {
    echo "ERROR: nvidia-container-runtime no está instalado" >&2
    exit 1
}

command -v k3s >/dev/null 2>&1 || {
    echo "ERROR: k3s no está instalado" >&2
    exit 1
}

K3S_CONTAINERD_DIR=${K3S_CONTAINERD_DIR:-/var/lib/rancher/k3s/agent/etc/containerd}
K3S_CONTAINERD_TEMPLATE=${K3S_CONTAINERD_TEMPLATE:-$K3S_CONTAINERD_DIR/config-v3.toml.tmpl}
DEVICE_PLUGIN_VERSION=${DEVICE_PLUGIN_VERSION:-v0.20.1}
DEVICE_PLUGIN_URL="https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"

# K3s detecta automáticamente nvidia-container-runtime cuando está instalado.
# La plantilla que se generó en ejecuciones anteriores hacía que el containerd
# embebido terminara con exit status 1. Se conserva como respaldo recuperable,
# pero se retira del directorio activo para que K3s use su configuración nativa.
RESTART_K3S=false
if [ -f "$K3S_CONTAINERD_TEMPLATE" ]; then
    BACKUP_TEMPLATE="${K3S_CONTAINERD_TEMPLATE}.disabled.$(date +%Y%m%d%H%M%S)"
    mv "$K3S_CONTAINERD_TEMPLATE" "$BACKUP_TEMPLATE"
    echo "Plantilla NVIDIA desactivada: $BACKUP_TEMPLATE"
    RESTART_K3S=true
fi

mkdir -p "$K3S_CONTAINERD_DIR"
if [ "$RESTART_K3S" = true ]; then
    systemctl restart k3s
fi
systemctl is-active --quiet k3s

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl apply -f "$DEVICE_PLUGIN_URL"
kubectl patch daemonset/nvidia-device-plugin-daemonset \
    --namespace=kube-system \
    --type=merge \
    --patch='{"spec":{"template":{"spec":{"runtimeClassName":"nvidia"}}}}'
kubectl rollout restart daemonset/nvidia-device-plugin-daemonset --namespace=kube-system
kubectl rollout status daemonset/nvidia-device-plugin-daemonset \
    --namespace=kube-system --timeout=180s

GPU_ALLOCATABLE=""
for attempt in 1 2 3 4 5 6 7 8 9 10 11 12; do
    GPU_ALLOCATABLE=$(kubectl get nodes -o \
        jsonpath='{range .items[*]}{.metadata.name}{" => "}{.status.allocatable.nvidia\\.com/gpu}{"\n"}{end}')
    case "$GPU_ALLOCATABLE" in
        *" => "*"1"*)
            printf '%s\n' "$GPU_ALLOCATABLE"
            echo "GPU Kubernetes configurada"
            exit 0
            ;;
    esac
    sleep 5
done

printf '%s\n' "$GPU_ALLOCATABLE"
echo "ERROR: el nodo sigue sin anunciar nvidia.com/gpu después de 60 segundos" >&2
exit 1

