#!/bin/sh
set -eu

[ "$(id -u)" -eq 0 ] || {
    echo "ERROR: ejecutar este script con sudo o como root" >&2
    exit 1
}

command -v nvidia-ctk >/dev/null 2>&1 || {
    echo "ERROR: nvidia-container-toolkit no está instalado" >&2
    exit 1
}

command -v k3s >/dev/null 2>&1 || {
    echo "ERROR: k3s no está instalado" >&2
    exit 1
}

K3S_CONTAINERD_CONFIG=${K3S_CONTAINERD_CONFIG:-/var/lib/rancher/k3s/agent/etc/containerd/config.toml}
DEVICE_PLUGIN_VERSION=${DEVICE_PLUGIN_VERSION:-v0.17.1}
DEVICE_PLUGIN_URL="https://raw.githubusercontent.com/NVIDIA/k8s-device-plugin/${DEVICE_PLUGIN_VERSION}/deployments/static/nvidia-device-plugin.yml"

mkdir -p "$(dirname "$K3S_CONTAINERD_CONFIG")"
if [ -f "$K3S_CONTAINERD_CONFIG" ]; then
    cp -p "$K3S_CONTAINERD_CONFIG" \
        "${K3S_CONTAINERD_CONFIG}.before-nvidia.$(date +%Y%m%d%H%M%S)"
fi

nvidia-ctk runtime configure \
    --runtime=containerd \
    --config="$K3S_CONTAINERD_CONFIG" \
    --nvidia-set-as-default \
    --cdi.enabled

systemctl restart k3s
systemctl is-active --quiet k3s

export KUBECONFIG=/etc/rancher/k3s/k3s.yaml
kubectl apply -f "$DEVICE_PLUGIN_URL"
kubectl rollout status daemonset/nvidia-device-plugin-daemonset \
    --namespace=kube-system --timeout=180s

echo "GPU Kubernetes configurada"
kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{" => "}{.status.allocatable.nvidia\\.com/gpu}{"\n"}{end}'

