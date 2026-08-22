# SolidSET Data API

API independiente y de solo lectura para acceder a SQL Server desde el agente
de IA sin entregar al agente las credenciales ni conectividad directa con la
base de datos.

Esta guía explica el despliegue en desarrollo usando Docker Desktop y
PowerShell.

## 1. Arquitectura

```text
Agente IA
    │ HTTP + X-SolidSET-Data-Key
    ▼
SolidSET Data API :8081
    │ conexión local/privada
    ▼
SQL Server de SolidSET
```

El Compose de este directorio es autónomo. No necesita PostgreSQL, Redis,
Qdrant, Ollama ni los contenedores del agente.

Si SQL Server también se ejecuta en Docker mediante otro Compose, utiliza el
overlay `docker-compose.sql-container.yml`. Este conecta la Data API a la red
externa indicada por `SQL_SERVER_DOCKER_NETWORK` y evita colisiones con puertos
SQL publicados en el host.

## 2. Requisitos

- Docker Desktop con contenedores Linux.
- Docker Compose v2.
- SQL Server accesible desde Docker.
- Una cuenta SQL Server exclusivamente de lectura.
- El puerto TCP de la instancia SQL Server habilitado.

```powershell
docker version
docker compose version
```

## 3. Preparar SQL Server

La cuenta de la Data API debe tener permiso de conexión y lectura, pero no debe
poder insertar, actualizar, borrar ni modificar esquemas.

Ejemplo orientativo para ejecutar con una cuenta administradora:

```sql
USE [master];
CREATE LOGIN [solidset_data_reader]
WITH PASSWORD = 'cambiar-por-una-contrasena-segura';

USE [DEV_ISIFrameIsicom];
CREATE USER [solidset_data_reader] FOR LOGIN [solidset_data_reader];
ALTER ROLE [db_datareader] ADD MEMBER [solidset_data_reader];
```

No utilices `db_owner` para esta integración.

### Instancias nombradas

Si SQL Server utiliza una instancia como `SQL2017DEV`, existen dos opciones:

1. Configurar `SQL_SERVER_INSTANCE=SQL2017DEV` y mantener SQL Browser accesible.
2. Recomendado: asignar un puerto TCP fijo, dejar `SQL_SERVER_INSTANCE=` vacío
   y configurar `SQL_SERVER_PORT` con ese puerto.

Para conocer el puerto que está escuchando en Windows:

```powershell
$service = Get-CimInstance Win32_Service `
  -Filter "Name='MSSQL`$SQL2017DEV'"

Get-NetTCPConnection -State Listen -OwningProcess $service.ProcessId |
  Select-Object LocalAddress, LocalPort
```

## 4. Crear la configuración

```powershell
cd D:\Trabajo\agente-robotea\solidset-data-api
Copy-Item .env.example .env
notepad .env
```

Ejemplo recomendado usando un puerto TCP fijo:

```env
SOLIDSET_DATA_API_KEY=cambiar-por-una-clave-larga-aleatoria
SOLIDSET_DATA_API_PORT=8081
SOLIDSET_DATA_API_VERSION=0.1.0

SQL_SERVER_HOST=host.docker.internal
SQL_SERVER_INSTANCE=
SQL_SERVER_PORT=57258
SQL_SERVER_DATABASE=DEV_ISIFrameIsicom
SQL_SERVER_USERNAME=solidset_data_reader
SQL_SERVER_PASSWORD=cambiar-por-la-contrasena-real
SQL_SERVER_LOGIN_TIMEOUT=15
SQL_SERVER_QUERY_TIMEOUT=120

SOLIDSET_DATA_API_MAX_ROWS=5000
```

Alternativa con instancia nombrada:

```env
SQL_SERVER_HOST=host.docker.internal
SQL_SERVER_INSTANCE=SQL2017DEV
SQL_SERVER_PORT=1433
```

`host.docker.internal` representa el equipo Windows desde el contenedor. No
utilices `localhost` para SQL Server si la API se ejecuta en Docker.

El fichero `.env` está excluido de la imagen y no debe subirse al repositorio
porque contiene secretos.

## 5. Construir y arrancar

Desde el directorio `solidset-data-api`:

```powershell
docker compose up -d --build
docker compose ps
```

Cuando SQL Server sea el contenedor `sqlserver` de la red `network-db01`:

```env
SQL_SERVER_HOST=sqlserver
SQL_SERVER_INSTANCE=
SQL_SERVER_PORT=1433
SQL_SERVER_DATABASE=ISIFrameIsicom
SQL_SERVER_DOCKER_NETWORK=network-db01
```

```powershell
docker compose `
  -f docker-compose.yml `
  -f docker-compose.sql-container.yml `
  up -d --build
```

Después del periodo inicial, el estado esperado es `healthy`.

## 6. Comprobar la API

### Salud del proceso

No requiere autenticación ni consulta SQL Server:

```powershell
Invoke-RestMethod http://localhost:8081/health
```

```json
{"status":"ok"}
```

### Conectividad con SQL Server

```powershell
$headers = @{
  "X-SolidSET-Data-Key" = "cambiar-por-una-clave-larga-aleatoria"
}

Invoke-RestMethod `
  -Uri http://localhost:8081/api/v1/system/capabilities `
  -Headers $headers
```

La respuesta indica el catálogo, versión del servidor y disponibilidad de
`SysResource2Agent`.

### Catálogo estructurado del esquema

```powershell
Invoke-RestMethod `
  -Uri "http://localhost:8081/api/v1/schema/catalog?tables=SysMeeting,SysMeeting2Resource,SysResources" `
  -Headers $headers
```

La respuesta contiene tablas, columnas, claves primarias y claves foráneas. El
agente consulta este endpoint antes de generar una nueva consulta dinámica y
guarda snapshots completos por instancia en PostgreSQL. Las credenciales SQL
Server permanecen únicamente en esta API.

### Probar un dataset

```powershell
Invoke-RestMethod `
  -Uri http://localhost:8081/api/v1/datasets/resources `
  -Headers $headers
```

Datasets disponibles:

- `resources`
- `logins`
- `workrooms`
- `workroom-resources`

### Probar una consulta parametrizada

```powershell
$body = @{
  query = "SELECT TOP (%s) IDWorkRoom, Name FROM dbo.SysWorkRoom ORDER BY Name"
  parameters = @(10)
  maxRows = 10
} | ConvertTo-Json

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost:8081/api/v1/query/read `
  -Headers $headers `
  -ContentType "application/json" `
  -Body $body
```

Solo se admiten consultas `SELECT` y CTE. La API rechaza escrituras,
procedimientos, comentarios SQL, múltiples instrucciones y resultados que
superen el límite configurado.

## 7. Swagger

```text
http://localhost:8081/docs
```

Los endpoints protegidos necesitan:

```text
X-SolidSET-Data-Key: <valor de SOLIDSET_DATA_API_KEY>
```

## 8. Registrar la Data API en el agente

La clave debe coincidir exactamente con `SOLIDSET_DATA_API_KEY`.

```powershell
$instance = @{
  Code = "local-solidset"
  Name = "SolidSET local"
  BaseUrl = "http://host.docker.internal:52130"
  NotificationUrl = "http://host.docker.internal:52131"
  SourceIP = "localhost"
  CountryCode = "PT"
  Locale = "pt-PT"
  TimeZone = "Europe/Lisbon"
  DataAPI = @{
    BaseUrl = "http://host.docker.internal:8081"
    APIKey = "cambiar-por-una-clave-larga-aleatoria"
    TimeoutSeconds = 120
    MaxRows = 5000
    VerifyTLS = $false
    active = $true
  }
  active = $true
} | ConvertTo-Json -Depth 5

Invoke-RestMethod `
  -Method Post `
  -Uri http://localhost/api/v1/agent/solidset/instances `
  -ContentType "application/json" `
  -Body $instance
```

Si ambos servicios comparten una red Docker puede utilizarse
`http://solidset-data-api:8080`. Si se ejecutan mediante Compose separados,
utiliza una dirección alcanzable como `http://host.docker.internal:8081`.

Probar la configuración desde el agente:

```powershell
Invoke-RestMethod `
  -Method Post `
  -Uri "http://localhost/api/v1/agent/solidset/instances/local-solidset/test-connection"
```

## 9. Operaciones habituales

```powershell
# Logs
docker compose logs -f data-api

# Aplicar cambios del .env
docker compose up -d --force-recreate data-api

# Reconstruir sin caché
docker compose build --no-cache data-api
docker compose up -d data-api

# Detener
docker compose down
```

## 10. Solución de problemas

### `/health` funciona pero `capabilities` devuelve 503

La API está activa, pero no puede conectar con SQL Server. Revisa:

- Host, instancia o puerto incorrectos.
- SQL Browser no accesible para una instancia nombrada.
- TCP/IP desactivado en SQL Server Configuration Manager.
- Firewall bloqueando el puerto SQL.
- Usuario, contraseña o catálogo incorrectos.
- SQL Server escuchando solamente en `127.0.0.1`.

```powershell
docker compose logs --tail 100 data-api
```

### HTTP 401

`X-SolidSET-Data-Key` no coincide con `SOLIDSET_DATA_API_KEY`. Después de
modificarla, recrea el contenedor y actualiza `DataAPI.APIKey` en el agente.

### El puerto 8081 está ocupado

```env
SOLIDSET_DATA_API_PORT=8082
```

La URL pasará a ser `http://localhost:8082`.

### El agente no puede utilizar `localhost:8081`

Dentro del contenedor del agente, `localhost` apunta al propio agente. Utiliza:

```text
http://host.docker.internal:8081
```

o conecta ambos servicios a una red Docker compartida.

## 11. Endpoints

- `GET /health`
- `GET /api/v1/system/capabilities`
- `GET /api/v1/datasets/resources`
- `GET /api/v1/datasets/logins`
- `GET /api/v1/datasets/workrooms`
- `GET /api/v1/datasets/workroom-resources`
- `GET /api/v1/agents/{humanResourceId}`
- `POST /api/v1/query/read`
