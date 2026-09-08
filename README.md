# Agente IA para SolidSET

Plataforma multiagente integrada con SolidSET Communicator. Cada recurso humano
puede disponer de un agente IA con identidad, memoria, conocimiento, canales,
modelo y configuración propios.

El sistema recibe eventos de SolidSET, identifica el agente seleccionado,
genera la respuesta mediante el modelo más adecuado y publica el resultado con
la identidad correcta del recurso IA.

## Características principales

- Agentes independientes por recurso SolidSET.
- Selección explícita mediante `Chat.destiny[].talkWithAgent=true`.
- Varios agentes en un mismo canal, cada uno con conocimiento aislado.
- Enrutamiento dinámico entre modelos y proveedores LLM.
- Modelos locales mediante Ollama.
- Interfaz para OpenAI, Azure OpenAI, Anthropic, Gemini y servidores
  OpenAI-compatible.
- Memoria conversacional aislada por agente, sesión, canal y meeting.
- RAG y conocimiento vectorial mediante Qdrant.
- Ingesta histórica incremental de chats y tareas.
- Aprendizaje individual, global y por reacciones de los usuarios.
- Procesamiento asíncrono con Redis Streams y workers escalables.
- Estado de respuesta para clientes WPF.
- PostgreSQL como configuración, auditoría y estado persistente.
- Acceso a SQL Server exclusivamente mediante SolidSET Data API.
- Integración opcional con n8n.
- Nginx como reverse proxy HTTP/HTTPS.

## Arquitectura

```text
SolidSET / WPF
      │ FrameworkMessage
      ▼
Nginx
      ▼
FastAPI ───────────────► Redis Streams
      │                       │
      │                       ▼
      │                 Agent Workers
      │                       │
      │             ▼
      │       Ollama Chat/GPU
      │
      ├────────────► Ollama Embeddings/CPU ──► Qdrant
      │
      ├────────────► PostgreSQL
      │              configuración, sesiones,
      │              cursores y auditoría
      │
      └────────────► SolidSET Data API
                            │
                            ▼
                        SQL Server
```

## Componentes

| Componente | Función |
|---|---|
| `agent-service` | API FastAPI, routing, generación, aprendizaje y endpoints. |
| `agent-worker` | Procesa solicitudes de respuesta desde Redis Streams. |
| `historical-producer` | Detecta y encola lotes históricos pendientes. |
| `historical-worker` | Procesa e indexa chats y tareas históricas. |
| `solidset-data-api` | Gateway independiente y de solo lectura para SQL Server. |
| `machining_db` | PostgreSQL/TimescaleDB para configuración y auditoría. |
| `machining_vector_db` | Qdrant para conocimiento semántico. |
| `machining_redis` | Colas, estados, memoria temporal y coordinación. |
| `machining_ollama_chat` | LLM interactivo aislado y mantenido en GPU. |
| `machining_ollama_embeddings` | Embeddings e ingestión aislados en CPU. |
| `machining_nginx` | Reverse proxy del agente. |
| `n8n` | Automatización opcional de workflows. |

## Flujo de una respuesta

1. SolidSET envía un `FrameworkMessage`.
2. La API obtiene `Chat.IDChat2` como `requestId`.
3. El router inspecciona `Chat.destiny`.
4. Solamente los elementos con `type=2` y `talkWithAgent=true` pueden activar
   un agente.
5. Se valida en SolidSET Data API que exista una relación activa en
   `SysResource2Agent`.
6. PostgreSQL confirma que el agente está activo y recupera su configuración.
7. El router selecciona proveedor y modelo según la capacidad requerida.
8. Se recuperan memoria, conocimiento privado, contexto del canal y aprendizaje
   relevante.
9. Un worker genera la respuesta.
10. El agente inicia sesión en SolidSET con el `SysLogin` sincronizado del
    recurso y publica la respuesta.
11. Redis y PostgreSQL actualizan el estado y la auditoría.
12. La interacción se incorpora al aprendizaje permitido para ese agente.

## Identidad del agente

Las identidades tienen funciones diferentes:

- `IDResource`: recurso humano propietario del agente.
- `IDAgentResource`: recurso Software IA registrado en
  `dbo.SysResource2Agent`.
- `SysResourceIA.ID`: identificador interno de PostgreSQL.

Al responder, `Chat.IDSenderResource` y el origen `type=1` utilizan
`IDAgentResource`. El destino `type=2` utiliza el recurso humano. Esto permite
que WPF muestre:

```text
Humano → IA: From Me · To Me [IA]
IA → humano: From Me [IA] · To Me
```

## Agentes y conocimiento

### Conocimiento aislado

Cada agente responde principalmente con:

- Mensajes escritos por su recurso propietario.
- Conversaciones privadas y canales donde participa.
- Meetings asociados.
- Tareas relacionadas con el recurso.
- Conocimiento añadido explícitamente.
- Reacciones recibidas sobre respuestas anteriores.

El conocimiento privado de un agente no debe mezclarse con el de otro.

### Aprendizaje global

El sistema también puede aprender contexto general de conversaciones y datos
compartidos. La recuperación aplica filtros de instancia, recurso, canal y
procedencia para evitar fugas entre agentes.

### Qué significa “aprender”

El aprendizaje operativo actual no modifica automáticamente los pesos del
modelo. Consiste en:

- Indexación vectorial en Qdrant.
- Memoria de conversación en Redis.
- Conocimiento y auditoría persistentes en PostgreSQL.
- Priorización mediante feedback y reacciones.
- Recuperación de contexto antes de generar respuestas.

El entrenamiento o fine-tuning de pesos debe tratarse como un proceso separado,
versionado y evaluado.

## Modelos y proveedores

Los proveedores se configuran en PostgreSQL y no están acoplados a SolidSET.
Cada agente puede disponer de varios modelos con capacidades diferentes:

- Conversación general.
- Razonamiento.
- Programación.
- Consultas internas y RAG.
- Información externa.
- Embeddings.

El router analiza la solicitud y elige dinámicamente el modelo activo más
adecuado. Si no existe una configuración específica, utiliza el proveedor
predeterminado.

Al crear o sincronizar un recurso en `SysResourceIA`, PostgreSQL garantiza una
asignación activa con `IsDefault=true` en `SysAgentIAModel`. Si falta, utiliza el
proveedor Ollama activo predeterminado; en su ausencia prioriza `ollama-default`
y después el código del proveedor. Debe existir al menos un proveedor Ollama
activo. La asignación inicial tiene capacidad `general` y sirve de respaldo para
las capacidades sin un modelo específico.

La sincronización conserva los modelos personalizados y sus parámetros. Para
cambiar el modelo principal del gemelo, se usa el endpoint de configuración de
modelos con `IsDefault=true` (y `LocalExecution=false` para OpenAI). La migración
`024_assign_default_agent_model.sql`, también instalada al iniciar la API,
completa los recursos existentes sin predeterminado sin alterar sus modelos
especializados.

Para desarrollo local, Ollama es el proveedor habitual:

```powershell
docker exec machining_ollama_chat ollama list
docker exec machining_ollama_embeddings ollama list
```

## Búsqueda externa con OpenAI

### Respuesta directa y aprendizaje local compartido

Si un gemelo tiene cualquier asignación activa de proveedor `openai`, sus
mensajes se envían directamente a ese modelo, independientemente de las
capacidades de la asignación. Si hay varias, se prioriza `IsDefault`, después
`Priority` y el código. Un proveedor global sin asignación explícita no activa
esta ruta. Los agentes sin OpenAI conservan su flujo local.

La respuesta textual de OpenAI se entrega sin clasificación ni reescritura
local. En consejos se conserva el contrato de array JSON de la API. Esta ruta
no ejecuta herramientas SQL o web: OpenAI no recibe acceso automático a datos
internos ni garantiza información actualizada. Los errores de OpenAI no
provocan un cambio silencioso a Ollama.

Después de responder OpenAI, la interacción se encola en PostgreSQL
(`OpenAILocalLearning`). El proceso `system-knowledge-worker` ejecuta Ollama
en segundo plano para extraer una nota e indexarla con embeddings locales en
Qdrant con alcance `global_shared`. Es aprendizaje RAG compartido entre
todos los gemelos de la misma instancia SolidSET, nunca entre instancias, y no
entrenamiento de pesos. Se conserva en PostgreSQL el recurso del gemelo que lo
originó, junto con proveedor, modelo, pregunta, respuesta y estado. También se
mantienen la procedencia y la marca de contenido generado por IA no verificado.
Cada respuesta satisfactoria de un gemelo que use OpenAI se encola siempre,
incluso con `TrainingMode=disabled` o `LearnFromSystem=false`.

La cola evita duplicados de la misma interacción y admite tres intentos con
espera de 60 segundos. Los trabajos agotados quedan en estado `failed` con el
tipo de error, sin registrar credenciales ni conversaciones en los logs.
Si PostgreSQL no permite encolar, se conserva la respuesta y se registra el
fallo de aprendizaje. Para activar cambios de código deben reiniciarse la API,
los workers de respuestas/consejos y `system-knowledge-worker`.

Para crear una conexión desde el entorno sin copiar secretos, utiliza en Swagger
**LLM Providers → POST /api/v1/agent/llm/providers/from-env**:

```json
{
  "Source": "openai_search",
  "Code": "openai-search",
  "Name": "OpenAI para búsquedas externas",
  "IsDefault": false
}
```

`Source=runtime` copia el proveedor y modelo conversacional de `LLM_PROVIDER` y
`MODEL_NAME`; `openai_search` copia `OPENAI_SEARCH_MODEL`, `OPENAI_API_KEY` y sus
límites de búsqueda. Se usan los valores cargados al arrancar el servicio,
incluidos los predeterminados cuando una variable no está definida. Tras editar
`.env`, recrea el contenedor para que reciba las variables nuevas.
La creación devuelve 201; si el código ya existe, devuelve 409 sin modificarlo.
Las claves se cifran y la respuesta solo indica `HasAPIKey`. Crear no realiza
una llamada de prueba al proveedor. Las conexiones son generales y no aceptan
`IDResource`. Para asignar una conexión existente usa
`PUT /api/v1/agent/solidset/agents/{IDResource}/model` con su `ProviderCode`,
capacidades, prioridad y comportamiento. La relación se guarda exclusivamente
en `SysAgentIAModel`. Para editar una conexión existente sigue disponible PUT.

Con `WEB_SEARCH_ENABLED=true` y `EXTERNAL_SEARCH_PROVIDER=openai`, la herramienta
`google_web_search` usa Responses API con `web_search`. El backend inyecta la
identidad del agente; el modelo solo proporciona la consulta pública.

La búsqueda resuelve una conexión OpenAI activa asignada al recurso, priorizando
la capacidad `external_web`, o una conexión OpenAI global predeterminada. Si no
existe ninguna, utiliza `OPENAI_API_KEY` y `OPENAI_SEARCH_MODEL` del entorno.
Las conexiones de otros recursos no se utilizan. Una conexión seleccionada con
credencial ausente o inválida produce un error, sin cambiar de cuenta.

Las conexiones específicas usan su modelo, URL, proyecto y organización; las
consultas de búsqueda mantienen los límites globales de tiempo, reintentos y
tokens (`WEB_SEARCH_TIMEOUT_SECONDS`, `OPENAI_MAX_RETRIES`,
`OPENAI_SEARCH_MAX_OUTPUT_TOKENS`). El historial y el perfil del recurso no se
incluyen en la petición a OpenAI. La consulta se limita a 2000 caracteres y se
exige una respuesta completada, con texto y fuentes HTTP(S). Se usa `store=false`
y los errores presentados al usuario no incluyen mensajes del SDK ni claves.

Pruebas locales, sin llamadas externas reales:

```powershell
docker exec machining_agent python -m unittest test.test_external_search test.test_web_search test.test_llm_providers test.test_orchestrator test.test_orchestrator_routing
```

Contrato oficial: [OpenAI Web search](https://developers.openai.com/api/docs/guides/tools-web-search).

## Datos y persistencia

| Directorio/volumen | Contenido |
|---|---|
| `postgres_data/` | Configuración, cursores, sesiones y auditoría. |
| `qdrant_data/` | Vectores y conocimiento recuperable. |
| `ollama_storage/` | Modelos descargados. |
| `ollama_embedding_storage/` | Modelo de embeddings del runtime CPU. |
| `data/` | Clave de cifrado y datos persistentes del agente. |
| `n8n_data/` | Configuración y workflows de n8n. |
| `audio/` | Entrada y salida de audio. |

No elimines estos directorios al reconstruir contenedores.

## SolidSET Data API

El agente no conecta directamente con SQL Server. Cada instancia SolidSET debe
tener una Data API activa, instalada junto a su base de datos.

La guía completa está en
[solidset-data-api/README.md](solidset-data-api/README.md).

Despliegue independiente resumido:

```powershell
cd solidset-data-api
Copy-Item .env.example .env
# Configurar SQL_SERVER_* y SOLIDSET_DATA_API_KEY.
docker compose up -d --build
Invoke-RestMethod http://localhost:8081/health
```

Sin una `DataAPI` válida, el agente no realiza sincronizaciones, ingestas,
consultas operativas ni validaciones en SQL Server.

## Requisitos de desarrollo

- Docker Desktop y Docker Compose v2.
- 16 GB de RAM como mínimo práctico; 32 GB recomendados.
- Espacio suficiente para modelos Ollama y Qdrant.
- SQL Server/SolidSET accesible mediante SolidSET Data API.
- Puertos disponibles: 80, 8000, 5432, 6333, 6379, 11434, 11435 y 5678 según los
  servicios publicados.

El runtime de chat usa la GPU en desarrollo; el runtime de embeddings queda en
CPU para no expulsar al modelo interactivo. El overlay GPU aplica la misma
reserva al despliegue de producción.

## Inicio rápido de desarrollo

### 1. Preparar variables

```powershell
Copy-Item .env.example .env
notepad .env
```

No guardes contraseñas ni API keys reales en Git.

### 2. Arrancar la plataforma

```powershell
docker compose -f docker-compose-dev.yml up -d --build
docker compose -f docker-compose-dev.yml ps
```

### 3. Verificar modelos

```powershell
docker exec machining_ollama_chat ollama list
docker exec machining_ollama_embeddings ollama list
```

Los servicios de inicialización descargan automáticamente `MODEL_NAME` y
`EMBEDDING_MODEL_NAME` en sus almacenes independientes durante el arranque.

### 4. Comprobar servicios

```powershell
Invoke-RestMethod http://localhost/api/v1/agent/health
Start-Process http://localhost/docs
```

### 5. Seguir logs

```powershell
docker compose -f docker-compose-dev.yml logs -f agent-service agent-worker
```

## Despliegue de producción

```powershell
docker compose -f docker-compose-prod.yml up -d --build
docker compose -f docker-compose-prod.yml ps
```

Reconstrucción sin caché:

```powershell
docker compose -f docker-compose-prod.yml build --no-cache agent-service agent-worker
docker compose -f docker-compose-prod.yml up -d --force-recreate
```

La variante GPU se activa únicamente cuando `docker run --gpus all ...
nvidia-smi` funciona:

```powershell
docker compose `
  -f docker-compose-prod.yml `
  -f docker-compose-prod.gpu.yml `
  up -d
```

## Endpoints principales

La referencia completa está en [api-docs.md](api-docs.md) y Swagger en
`/docs`.

### Mensajes y respuestas

```http
POST /api/v1/agent/notification/framework-message
POST /api/v1/agent/notification/framework-message/preview
POST /api/v1/agent/notification/chat-question/suggest-response
GET  /api/v1/agent/responses/{requestId}/status?lang=es
GET  /api/v1/agent/responses/status?chatId={IDChat2}&lang=es
GET  /api/v1/agent/responses/queue/status
```

### Configuración y sincronización SolidSET

```http
POST /api/v1/agent/solidset/instances
POST /api/v1/agent/solidset/instances/{code}/test-connection
GET  /api/v1/agent/solidset/resources?instanceCode=beta-solidset&offset=0&limit=100
GET  /api/v1/agent/solidset/workrooms?instanceCode=beta-solidset&offset=0&limit=100
POST /api/v1/agent/solidset/resources/sync
POST /api/v1/agent/solidset/logins/sync
POST /api/v1/agent/solidset/workrooms/sync
POST /api/v1/agent/solidset/chat-workroom/sync
```

Los GET consultan una página de la Data API de la instancia indicada, sin sincronizar
ni modificar datos. `instanceCode` es obligatorio; `offset` comienza en 0 y `limit`
admite de 1 a 1000 (100 por defecto). La respuesta contiene `instanceCode`, `rows`,
`rowCount`, `offset`, `limit`, `hasMore` y `nextOffset`. Utiliza `nextOffset` para
leer la siguiente página. Una instancia inexistente devuelve 404; una Data API
inactiva o no disponible devuelve 503.

El diálogo `POST /api/v1/agent/solidset/multi-agent/dialogue` distingue
`SenderResourceId` (interlocutor) de `SelectedAgentResourceIds` (recursos humanos
cuyos gemelos responden). Cada respuesta recibe la identidad y el conocimiento
privado del gemelo seleccionado en `IDWorkRoom`. Incluye `SolidSETInstanceCode`
para cargar también su perfil y plantilla publicada; se utiliza incluso con
`SendToSolidSET=false`. Las sesiones se separan por instancia, gemelo, canal y
conversación. Las respuestas con contexto personal no se reutilizan ni se guardan
en la caché global de respuestas; el diálogo conserva su aprendizaje privado.

### Histórico

```http
POST   /api/v1/agent/historical-ingestion/start
POST   /api/v1/agent/historical-ingestion/pause
POST   /api/v1/agent/historical-ingestion/resume
POST   /api/v1/agent/historical-ingestion/approve-dry-run
GET    /api/v1/agent/historical-ingestion/status
GET    /api/v1/agent/historical-ingestion/batches
DELETE /api/v1/agent/historical-ingestion/messages/{idChat2}
```

### Modelos

```http
PUT    /api/v1/agent/llm/providers/{code}
GET    /api/v1/agent/llm/providers
DELETE /api/v1/agent/llm/providers/{code}
PUT    /api/v1/agent/solidset/agents/{agentResourceId}/model
GET    /api/v1/agent/solidset/agents/{agentResourceId}/model
```

## Estados de respuesta

El cliente puede consultar el progreso usando `IDChat2`:

| Código | Estado | Significado |
|---:|---|---|
| 0 | `queued` | Esperando un worker. |
| 1 | `processing` | Validando y preparando la solicitud. |
| 2 | `searching` | Recuperando información. |
| 3 | `thinking` | Generando la respuesta. |
| 4 | `sending` | Publicando en SolidSET. |
| 5 | `completed` | Respuesta terminada. |
| 6 | `failed` | No se pudo responder. |
| 7 | `cancelled` | Solicitud cancelada. |

Los mensajes visibles están disponibles en español, inglés y portugués.

## Ingesta histórica

La ingesta se ejecuta solamente para recursos con agentes activos y verificados
en `SysResource2Agent`. Cada agente mantiene cursores independientes para chat y
tareas.

La ingesta no vuelve a comenzar al reiniciar Docker. Continúa desde el último
cursor persistido en PostgreSQL. Se considera actualizada cuando:

- Todos los cursores activos están en `completed`.
- `queue.pending` es `0`.
- `queue.lag` es `0`.
- No existen cursores `queued`, `processing`, `failed` o `dry_run` pendientes de
  tratamiento.

`dry_run=true` valida el proceso, pero no representa conocimiento indexado.

## Seguridad

- SQL Server no es accesible directamente desde el agente.
- SolidSET Data API acepta únicamente lecturas.
- Las API keys y contraseñas se almacenan cifradas cuando corresponde.
- No deben utilizarse cuentas SQL con `db_owner`.
- Nginx debe terminar TLS en producción.
- Los endpoints administrativos deben protegerse con la credencial configurada.
- No se deben registrar contraseñas, hashes ni API keys en logs.
- Qdrant y PostgreSQL no deberían exponerse públicamente.

## Escalabilidad

FastAPI recibe y encola; los workers realizan la generación. El Compose actual
usa `container_name` para `agent-worker`, por lo que primero debe eliminarse ese
nombre fijo antes de escalar réplicas del servicio. Después puede utilizarse:

```powershell
docker compose -f docker-compose-prod.yml up -d --scale agent-worker=4
docker compose -f docker-compose-prod.yml up -d --scale historical-worker=2
```

El número útil de workers está limitado principalmente por memoria, GPU,
modelo y latencia. Incrementar workers sin capacidad de inferencia suficiente
solo aumenta la cola interna de Ollama.

## n8n

n8n accede al agente dentro de la red Docker mediante:

```text
http://agent-service:8000
```

Puede utilizar nodos HTTP Request para iniciar diálogos, consultar estados,
activar sincronizaciones o ejecutar procesos posteriores a una respuesta.

## Operación y diagnóstico

```powershell
# Estado
docker compose -f docker-compose-dev.yml ps

# Logs del agente
docker compose -f docker-compose-dev.yml logs -f agent-service

# Logs de workers
docker compose -f docker-compose-dev.yml logs -f agent-worker historical-worker historical-producer

# Modelos instalados en cada runtime
docker exec machining_ollama_chat ollama list
docker exec machining_ollama_embeddings ollama list

# Validar Nginx
docker exec machining_nginx nginx -t
```

Problemas habituales:

- `Cannot allocate memory`: usar un modelo menor o reducir contexto y
  concurrencia.
- `Connection refused` en Qdrant/Ollama/Redis: comprobar salud y red Docker.
- Data API devuelve 503: revisar host, puerto, instancia y credenciales SQL.
- Respuesta no publicada: comprobar selección `talkWithAgent`, agente activo,
  relación `SysResource2Agent`, `SysLogin` y login de SolidSET.
- Respuesta en idioma incorrecto: revisar el mensaje original y la detección de
  idioma; `Locale` no debe sustituir el idioma de la pregunta.

## Documentación relacionada

- [Documentación completa de endpoints](api-docs.md)
- [Despliegue de SolidSET Data API](solidset-data-api/README.md)
- [Compose de desarrollo](docker-compose-dev.yml)
- [Compose de producción](docker-compose-prod.yml)
- [Configuración de Nginx](nginx/)
- [Scripts operativos](scripts/)

### Separación entre proveedores y asignaciones

`SysLLMProviderConfiguration` conserva proveedor, modelo, endpoint y credencial
cifrada, sin vínculo directo a un recurso. `SysAgentIAModel` relaciona cada recurso
humano con esas conexiones reutilizables. El router prioriza una asignación activa
compatible con la capacidad solicitada, después la predeterminada del recurso y
finalmente la conexión global predeterminada. Una asignación especializada no
sustituye el modelo de conversación general.

La migración `023_separate_llm_provider_assignments.sql` traslada los antiguos
vínculos directos y elimina la columna `IDResource` de proveedores en una
transacción. Conserva las asignaciones existentes y los secretos cifrados; puede
repetirse. También se aplica al inicializar el esquema desde el servicio. Actualiza
y reinicia todos los procesos del agente juntos: versiones anteriores del código
no son compatibles con la columna eliminada. Antes de migrar una instalación,
guarda una copia de las dos tablas; revertir requiere restaurar esquema y datos,
además del código anterior.
