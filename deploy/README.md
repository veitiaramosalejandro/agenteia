# Despliegue Kubernetes

La configuración se divide en:

- `k8s/base`: recursos comunes del sistema.
- `k8s/overlays/production`: personalización de producción.
- `k8s/migrations`: imagen que ejecuta las migraciones SQL versionadas.

## Preparación

1. Construir y publicar las imágenes de `agent-service`, `agent-control-center` y
   `db-migrations` en un registry accesible por el clúster.
2. Crear el secreto `agenteia-secrets` en el namespace `agenteia-prod` sin
   guardar credenciales en Git.
3. Cambiar los dominios de `k8s/base/ingress.yaml` o sobrescribirlos desde el
   overlay de producción.
4. Confirmar que existe un `StorageClass` compatible con PVC `ReadWriteOnce`.
5. Confirmar que el nodo GPU tiene instalado el NVIDIA device plugin.

## Construcción de imágenes

Desde la raíz del repositorio:

```bash
docker build -t registry.example.com/agenteia/agent-service:0.0.2 ./agent-service
docker build -t registry.example.com/agenteia/agent-control-center:0.5.0 ./agent-control-center
docker build -t registry.example.com/agenteia/db-migrations:0.0.1 -f deploy/k8s/migrations/Dockerfile .
```

## Validación y aplicación

Con `kubectl` y `kustomize` instalados:

```bash
kubectl kustomize deploy/k8s/overlays/production
kubectl apply -k deploy/k8s/overlays/production
kubectl -n agenteia-prod get pods,pvc,jobs
```

El manifiesto no contiene secretos reales y no debe aplicarse hasta sustituir
los nombres de registry, dominios, `ClusterIssuer` y valores operativos.

