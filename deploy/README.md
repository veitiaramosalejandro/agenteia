# Despliegue Kubernetes

La configuración se divide en:

- `k8s/base`: recursos comunes del sistema.
- `k8s/overlays/production`: personalización de producción.
- `k8s/migrations`: imagen que ejecuta las migraciones SQL versionadas.
- `scripts/preflight.sh`: comprobaciones previas sin modificar el clúster.

El manifiesto opcional `k8s/optional` añade `solidset-data-api` y requiere
configurar previamente sus valores SQL Server. No se incluye en los overlays
base de staging/producción porque el acceso a SQL Server depende de la red y
las credenciales de cada instalación.

## Staging

El overlay de staging reduce las réplicas y usa un modelo de chat menor:

```bash
kubectl kustomize deploy/k8s/overlays/staging
```

Para renderizar la variante con Data API:

```bash
kubectl kustomize deploy/k8s/optional
```

## Preparación

1. Construir y publicar las imágenes de `agent-service`, `agent-control-center` y
   `db-migrations` en un registry accesible por el clúster.
2. Crear el secreto `agenteia-secrets` en el namespace `agenteia-prod` sin
   guardar credenciales en Git.
3. Cambiar los dominios de `k8s/base/ingress.yaml` o sobrescribirlos desde el
   overlay de producción.
4. Confirmar que el `IngressClass` utilizado es el instalado en el clúster.
   K3s usa `traefik` por defecto; el manifiesto base ya está preparado para
   esa clase.
5. Confirmar que existe un `StorageClass` compatible con PVC `ReadWriteOnce`.
6. Confirmar que el nodo GPU tiene instalado el NVIDIA device plugin.

En K3s, la configuración del runtime y el Device Plugin requieren privilegios
de administrador. El script preparado para este nodo es:

```bash
sudo sh deploy/scripts/configure-k3s-nvidia.sh
```

El script hace una copia de seguridad de la configuración de containerd,
configura el runtime NVIDIA para K3s, reinicia K3s y aplica el Device Plugin
NVIDIA fijado a `v0.17.1`. Después debe aparecer `nvidia.com/gpu` en los recursos
asignables del nodo.

Los PVC `agent-data` y `agent-audio` son `ReadWriteOnce` y se comparten entre
los Pods de API y workers. Esto es válido para el despliegue inicial en un
único nodo. En un clúster multinodo se debe usar almacenamiento `ReadWriteMany`
o separar los volúmenes por servicio antes de habilitar anti-affinity o
escalado entre nodos.

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

Antes de aplicar producción:

```bash
KUBE_CONTEXT=mi-contexto \
  NAMESPACE=agenteia-prod \
  sh deploy/scripts/preflight.sh
```

Para crear el Secret sin escribir credenciales en Git, exportar primero las
variables en la sesión actual y ejecutar:

```bash
NAMESPACE=agenteia-staging \
  sh deploy/scripts/create-secret.sh
```

El script requiere `POSTGRES_USER`, `POSTGRES_PASSWORD` y `DB_URL`. Las claves
de proveedores LLM y la contraseña inicial del Control Center son opcionales.

El manifiesto no contiene secretos reales y no debe aplicarse hasta sustituir
los nombres de registry, dominios, `ClusterIssuer` y valores operativos.

