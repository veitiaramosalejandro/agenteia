# API del agente SolidSET

> Entorno Docker de desarrollo: la API se ejecuta con Python 3.11 y el código del servicio mantiene compatibilidad sintáctica con esa versión.

El diagnóstico de inicio y el campo `runtime.startup_connectivity` de `GET /api/v1/agent/health` obtienen las instalaciones activas directamente de PostgreSQL `SysSolidSETInstance`. Para cada fila verifican `BaseUrl` y `NotificationUrl` e informan `Code`, `SourceIP`, URL configurada y URL efectiva. Dentro de Docker, una URL configurada con `localhost` se prueba mediante `host.docker.internal`, sin modificar el valor persistido. Las variables históricas `SOLIDSET_RESTAPI_BASE_URL` y `NOTIF_API_BASE_URL` no determinan este diagnóstico multiinstancia.

Los endpoints de notificación resuelven la instancia, en orden, mediante
`X-SolidSET-Instance`, la IP reenviada por Nginx, la IP TCP directa y el host
HTTP. Esto permite que una instalación registrada con
`SourceIP=android.isicom.pt` sea reconocida detrás del proxy Docker y evita el
`400 Instancia SolidSET desconocida` causado por la IP interna de Nginx.
Cuando ninguna señal coincide pero PostgreSQL contiene exactamente una
instancia activa, esa única instancia se utiliza sin ambigüedad. Si existen
varias instalaciones activas, el fallback no se aplica y el emisor debe enviar
`X-SolidSET-Instance` o una dirección registrada.

Los saludos directos (`hola`, `hola como estás` y equivalentes) se contestan de
forma inmediata, respetuosa y usando únicamente el `FullName` del remitente;
no se muestran perfiles ni canales y no se espera al LLM. Cuando una persona
habla con su propio agente, incluida una conversación de meeting, el envío usa
el UUID interno de `SysResourceIA` como identidad visual del agente para que la
respuesta aparezca como interlocutor distinto. El log informa las fases
`encolada`, `iniciando`, `enrutamiento completado` y el resultado del envío.
El envío a SolidSET tiene prioridad sobre el aprendizaje de la interacción: la
sesión y Qdrant se actualizan después de publicar, tienen tiempos máximos y sus fallos no impiden publicar la
respuesta. Antes del login se registra `base=<BaseUrl>` para mostrar qué URL de
`SysSolidSETInstance` está siendo utilizada. Dentro de Docker, un SolidSET
ejecutado en el host debe configurarse como
`http://host.docker.internal:52130`, no como `http://localhost:52130`.
El método de envío registra su entrada antes de validar el meeting. Si recibe
una URL localhost dentro de Docker, prueba primero su traducción a
`host.docker.internal` y evita esperar un timeout contra el propio contenedor.
La traducción conserva el binding HTTP original: conecta por TCP a
`host.docker.internal`, pero envía `Host: localhost:52130`. Esto permite usar
`BaseUrl=http://localhost:52130` en `SysSolidSETInstance` cuando IIS rechaza
otros nombres con `400 Bad Request - Invalid Hostname`.
Los intentos de envío registran la lectura de `SysLogin`, cada llamada a
`LoginJson` y la respuesta HTTP de `/Chat/SendMessageForm`, sin imprimir
contraseñas. El perfil de desarrollo utiliza `qwen2.5:3b` con contexto 2048
para reducir el consumo de memoria; producción conserva su modelo configurable.
El login contextual envía el hash persistido con `PasswordEncrypted=true` y el
nombre exacto `TimezoneId`. No envía `Resources[0]`: en el controlador C# esa
colección es opcional y el recurso vigente se selecciona mediante
`SysLogin.LastIDResource`. Los rechazos HTTP muestran hasta 500 caracteres del
cuerpo para diagnosticar ModelState sin registrar credenciales.

En Docker, Nginx publica la API mediante `http://android.isicom.pt/` y reenvía internamente hacia `http://agent-service:8000`. Por tanto, los endpoints conservan sus rutas; por ejemplo, salud está disponible en `http://android.isicom.pt/api/v1/agent/health` y Swagger en `http://android.isicom.pt/docs`. La ruta técnica `GET /nginx-health` comprueba únicamente el proxy.

Para HTTPS, `scripts/issue-letsencrypt.ps1 -Email <correo>` ejecuta Certbot mediante webroot, emite el certificado de `android.isicom.pt` y activa el virtual host TLS en el puerto 443. El desafío `/.well-known/acme-challenge/` permanece accesible por HTTP para renovaciones. `scripts/renew-letsencrypt.ps1` renueva los certificados próximos a vencer y recarga Nginx. El DNS público debe apuntar al servidor y el NAT/firewall debe admitir entrada TCP 80 y 443.

Si HTTP-01 no puede atravesar el NAT/firewall, `scripts/issue-letsencrypt-dns.ps1 -Email <correo>` permite emitir mediante DNS-01 manual creando un TXT en `_acme-challenge.android.isicom.pt`. Esta variante no tiene renovación desatendida: debe repetirse antes del vencimiento o sustituirse por un plugin/API del proveedor DNS.

Como alternativa exclusivamente interna, `scripts/issue-internal-certificate.ps1` crea una CA privada `ISICOM Internal Root CA`, emite un certificado con SAN `android.isicom.pt` y activa HTTPS en Nginx. Los clientes deben instalar `certbot/internal/isicom-internal-ca.crt` en su almacén de autoridades raíz. La clave `isicom-internal-ca.key` es sensible, no debe distribuirse y debe custodiarse fuera del servidor tras emitir los certificados necesarios.

La resolución habitual de identidad (`Username`, `FullName`, `IDLogin`, `IDResource`) utiliza exclusivamente la réplica PostgreSQL `SysLogin`; un identificador desconocido no desencadena conexiones a SQL Server. SQL Server queda reservado para ingestas y consultas operativas explícitas. En el perfil CPU de producción, Ollama usa `OLLAMA_KV_CACHE_TYPE=f16` porque una caché V cuantizada requiere Flash Attention.

En el despliegue Windows actual, SQL Server se alcanza desde Docker mediante `SQL_SERVER_DOCKER_HOST=host.docker.internal` y `SQL_SERVER_INSTANCE=SQL2017DEV`. La API compone una única barra invertida y no fuerza el puerto cuando existe una instancia. Catálogo, usuario y contraseña permanecen en `SQL_SERVER_DB`, `SQL_SERVER_USER` y `SQL_SERVER_PASSWORD`.

El despliegue `docker-compose-prod.yml` utiliza Ollama por CPU de forma predeterminada y no exige el runtime NVIDIA. Cuando `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` funcione correctamente, la aceleración se activa añadiendo el overlay `docker-compose-prod.gpu.yml`. En producción Uvicorn se ejecuta sin `--reload`.

El despliegue `docker-compose-dev.yml` replica la topología funcional de
producción, pero publica exclusivamente HTTP en los puertos 80 y 8000, no
incluye Certbot ni monta certificados. Conserva el montaje del código fuente y
Uvicorn `--reload` para desarrollo. También espera la salud de PostgreSQL,
Redis, Ollama y Qdrant, utiliza el perfil CPU seguro y resuelve SolidSET desde
`SysSolidSETInstance` y las identidades desde `SysLogin` en PostgreSQL.

Última actualización: 19 de agosto de 2026.

> Este documento debe actualizarse en el mismo cambio que modifique una ruta, método HTTP, contrato de entrada, respuesta o comportamiento observable de la API.

Actualmente la API expone 25 endpoints funcionales. Puedes consultar siempre la documentación interactiva en:

```text
http://localhost:8000/docs
```

## Registro de IP por petición

Todas las peticiones HTTP, independientemente del endpoint, generan una línea en la consola del servicio con la IP TCP directa, la primera IP declarada por el proxy, método, ruta, estado y duración. Ejemplo:

```text
🌐 API_REQUEST ip=127.0.0.1 forwarded_ip=- method=POST endpoint=/api/v1/dialogue status=200 duration_ms=84.2
```

`ip` es la conexión observada por FastAPI y no puede ser sustituida mediante cabeceras. `forwarded_ip` muestra separadamente el primer valor de `X-Forwarded-For` o `X-Real-IP`; solo debe considerarse la IP real del cliente cuando el proxy que establece esas cabeceras sea de confianza. No se registran cuerpos ni parámetros de consulta.

## Flujo principal recomendado

El funcionamiento habitual sería:

```text
1. Sincronizar recursos desde SQL Server
2. Sincronizar las cuentas SysLogin
3. Sincronizar el catálogo SysWorkRoom
4. Sincronizar las relaciones recurso–canal
5. Configurar qué recursos son agentes IA
6. Activar agentes dentro de sus canales
7. Cargar conocimiento privado para cada agente
8. SolidSET selecciona uno o varios agentes
9. Ejecutar el diálogo multiagente
10. Cada agente responde con memoria y conocimiento independientes
```

# Configuración multiagente

## Proveedores LLM intercambiables

La lógica de SolidSET depende de una interfaz común de modelo de chat
(`invoke` y `bind_tools`) y no instancia Ollama directamente. El registro en
`app/llm/providers.py` incluye los identificadores:

- `ollama` (implementado y activo por defecto).
- `openai`.
- `azure_openai`.
- `anthropic`.
- `gemini`.
- `openai_compatible` o `local_openai` para servidores compatibles con la API
  de OpenAI.

Las variables siguientes son únicamente el respaldo de arranque cuando todavía
no existe una configuración activa en PostgreSQL:

```env
LLM_PROVIDER=ollama
MODEL_NAME=qwen2.5:3b
LLM_BASE_URL=
LLM_API_KEY=
LLM_TEMPERATURE=0.5
LLM_MAX_OUTPUT_TOKENS=1024
LLM_REQUEST_TIMEOUT_SECONDS=900
```

Para Azure también se utilizan:

```env
AZURE_OPENAI_ENDPOINT=https://<recurso>.openai.azure.com
AZURE_OPENAI_API_VERSION=2024-10-21
AZURE_OPENAI_DEPLOYMENT=<deployment>
```

Los proveedores remotos cargan sus integraciones de forma diferida. Solo debe
instalarse el paquete correspondiente cuando se active: `langchain-openai`,
`langchain-anthropic` o `langchain-google-genai`. Si falta, el arranque informa
el paquete exacto necesario. Las claves nunca se incluyen en salud ni logs.

### Configuración persistida en PostgreSQL

La tabla `SysLLMProviderConfiguration` es la fuente canónica del modelo de chat.
Admite varias configuraciones, una predeterminada global y una configuración
activa específica por `SysResourceIA.IDResource`. En cada conversación el router
resuelve primero la configuración del agente solicitado y, si no existe, usa la
global; solo entonces recurre al `.env`. Los cambios se aplican en la próxima
petición sin reiniciar el contenedor.

Las API keys se guardan cifradas con Fernet y nunca aparecen en respuestas API.
La clave maestra se conserva como secreto de despliegue:

```env
LLM_CREDENTIAL_ENCRYPTION_KEY=<clave-fernet>
```

Se genera una vez con:

```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

No debe cambiarse después de guardar credenciales.

#### Registrar o actualizar un proveedor

```http
PUT /api/v1/agent/llm/providers/{code}
```

Ejemplo global con Ollama:

```json
{
  "Code": "ollama-default",
  "Name": "Ollama coordinador",
  "Provider": "ollama",
  "Model": "qwen2.5:3b",
  "BaseUrl": "http://ollama-llm:11434",
  "Temperature": 0.5,
  "MaxOutputTokens": 1024,
  "TimeoutSeconds": 900,
  "IDResource": null,
  "IsDefault": true,
  "active": true
}
```

Para asignar otro proveedor a un agente concreto se informa `IDResource`; en
ese caso `IsDefault` se normaliza a `false`. Un `PUT` con `APIKey=null` conserva
la credencial existente. Solo puede existir una configuración global
predeterminada activa y una configuración activa por recurso.

```http
GET /api/v1/agent/llm/providers
DELETE /api/v1/agent/llm/providers/{code}
```

El listado devuelve `HasAPIKey`, pero nunca `APIKey`. `DELETE` realiza una baja
lógica (`active=false`).

Ollama continúa siendo necesario para los embeddings de Qdrant aunque el
modelo de conversación sea remoto. `GET /api/v1/agent/health` expone proveedor,
modelo, URL y `source=postgresql|environment_fallback` separadamente de
`ollama_embeddings`.

### Modelo asignado a cada agente: SysAgentIAModel

La selección autoritativa de SolidSET continúa siendo `Chat.destiny[].talkWithAgent=true`.
El `IDResource` seleccionado puede tener varias filas activas en `SysAgentIAModel`.
El router clasifica cada mensaje y elige una fila cuya colección `Capabilities`
contenga la capacidad requerida. Si ninguna coincide, usa `IsDefault=true` y,
finalmente, el proveedor global predeterminado.

```text
talkWithAgent
      -> SysResourceIA.IDResource
      -> clasificación de la pregunta
      -> SysAgentIAModel.Capabilities
      -> IDProviderConfiguration seleccionado
      -> SysLLMProviderConfiguration
      -> modelo de chat
      -> respuesta con identidad del recurso agente
```

Asignar o consultar el modelo de un agente:

```http
PUT /api/v1/agent/solidset/agents/{IDResource}/model
GET /api/v1/agent/solidset/agents/{IDResource}/model
```

```json
{
  "ProviderCode": "ollama-default",
  "Role": "general",
  "LocalExecution": true,
  "TrainingMode": "rag_reinforcement",
  "LearnFromOwner": true,
  "LearnFromSystem": true,
  "LearnFromReactions": true,
  "Capabilities": ["coding", "sql", "technical"],
  "Priority": 20,
  "IsDefault": false,
  "active": true
}
```

Capacidades iniciales:

- `general` y `external_web` → `qwen2.5:3b`.
- `coding`, `sql` y `technical` → `qwen2.5-coder:3b`.
- `reasoning`, `planning` y `analysis` → `llama3.2:3b`.

La identidad, sesión, memoria, conocimiento privado y login SolidSET permanecen
asociados al mismo `IDResource`; solo cambia el modelo que genera ese turno.

### Inversión del destinatario al responder

Cuando `Chat.destiny` contiene el recurso humano `type=1` y el agente solicitado
`type=2, talkWithAgent=true`, el primero selecciona el destinatario de respuesta
y el segundo selecciona el agente emisor. El formulario enviado a SolidSET queda
lógicamente invertido como agente → humano:

```text
Destiny.WorkRoom = Chat.destiny[].idChannel
Destiny.Dests[0].Login = Chat.destiny[type=1].idLogin
Destiny.Dests[0].Resource = Chat.destiny[type=1].idResource
Destiny.Dests[0].Kind = 2
Destiny.Dests[0].Type = 2
```

La API usa las claves planas `Destiny.Dests[0].*` porque `/Chat/SendMessageForm`
recibe formulario; el model binder de SolidSET lo convierte al objeto anidado
`Destiny.Dests`. `talkWithAgent` no se reenvía en la respuesta para evitar que
la respuesta generada vuelva a activar al agente.

`TrainingMode` admite `rag_reinforcement`, `rag_only` y `disabled`. La mejora
actual no modifica los pesos del modelo: utiliza conocimiento vectorial aislado,
mensajes del recurso propietario, conocimiento general permitido, memoria de
conversación y recompensas derivadas de reacciones. Esta estrategia puede operar
continuamente sin detener Ollama. Un futuro fine-tuning de pesos debe ejecutarse
como proceso separado, versionar el modelo resultante y registrarlo como una
nueva configuración antes de activarlo.

`LocalExecution=true` solo es válido para `ollama`, `local_openai` o un endpoint
`openai_compatible` desplegado en infraestructura propia. Los modelos oficiales
de OpenAI, Azure OpenAI, Anthropic y Gemini son remotos; guardar su configuración
en PostgreSQL no convierte esos modelos propietarios en modelos locales.

Para agregar otro motor basta implementar `ChatProvider.create_model()` y
registrarlo mediante `ProviderRegistry.register()`. El router, LangGraph,
herramientas, memoria y endpoints SolidSET permanecen sin cambios.

La selección local vigente fue calculada con `llmfit` y está documentada en
`docs/llmfit-model-selection.md`: `qwen2.5:3b` para coordinación/chat,
`qwen2.5-coder:3b` para código y SQL, `Phi-4-mini-reasoning` como razonador
opcional secuencial y `nomic-embed-text` para conservar la colección actual.
`Qwen3-Embedding-0.6B` queda reservado para una migración con reindexado.

## 0. Registrar una instancia SolidSET

```http
POST /api/v1/agent/solidset/instances
```

Registra o actualiza por `Code` las URLs que antes se seleccionaban desde `.env`:

```json
{
  "Code": "solidset-lisboa",
  "Name": "SolidSET Lisboa",
  "BaseUrl": "http://192.168.10.20:52130",
  "NotificationUrl": "http://192.168.10.20:52131",
  "SourceIP": "192.168.10.20",
  "active": true
}
```

La configuración se guarda en PostgreSQL `SysSolidSETInstance`. Antes de insertar, la API busca coincidencias por `Code`, `BaseUrl` o `SourceIP`; si encuentra alguna, actualiza esa misma fila y conserva su `ID`. La respuesta indica `status=created` o `status=updated`. Existen índices únicos normalizados para impedir duplicados incluso con diferencias de mayúsculas o una barra final en la URL. `BaseUrl` se utiliza para `/User/LoginJson`, consultas y respuestas; `NotificationUrl` se utiliza únicamente para reenviar al servicio de notificaciones correspondiente.

Cada SolidSET debe llamar los endpoints de entrada con:

```http
X-SolidSET-Instance: solidset-lisboa
```

El encabezado tiene precedencia. Si falta, la API busca en `SourceIP` la IP
reenviada, la IP TCP directa y el host HTTP. Si ninguna coincide pero existe
exactamente una instancia activa, utiliza esa única instancia; con varias,
mantiene el rechazo `400` para impedir un enrutamiento ambiguo. La instancia se
conserva en la huella del evento, sesión del agente, login y envío de respuesta.

La resolución por IP admite que `X-SolidSET-Instance` no esté presente: los parámetros opcionales se tipan explícitamente en PostgreSQL, de modo que una búsqueda únicamente por `SourceIP` no produce un `503`. Los errores de acceso a PostgreSQL continúan devolviendo `503`; una IP simplemente no registrada devuelve `400`.

## 1. Guardar o actualizar un agente

```http
POST /api/v1/agent/solidset/chat-configuration
```

Crea o actualiza un agente utilizando `IDResource` como identificador único.

```json
{
  "Name": "Agente de mantenimiento",
  "Stamp": "2026-08-17T16:30:00",
  "IDResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
  "active": true
}
```

Comportamiento:

- Si el recurso no existe, crea `SysResourceIA`.
- Si ya existe, actualiza nombre, fecha y estado.
- `active = false` impide que el agente responda.
- El campo `ID` interno lo genera PostgreSQL automáticamente.

---

## 2. Configurar un agente dentro de un canal

```http
PUT /api/v1/agent/solidset/agents/{agent_resource_id}/workrooms/{workroom_id}
```

Activa, desactiva u ordena al agente dentro de un canal específico.

```json
{
  "active": true,
  "response_order": 1
}
```

Ejemplo:

```http
PUT /api/v1/agent/solidset/agents/ce0e837a-fe28-47ae-9ba0-8841fe042ca8/workrooms/007e3b2a-bbf6-4f46-8cbd-26d26db06ec1
```

Comportamiento:

- Hace `UPSERT` en `SysChatIAResource`.
- `active` controla si puede responder en ese canal.
- `response_order` determina su posición frente a otros agentes.
- Un agente puede estar activo globalmente y desactivado solamente en un canal.

---

## 3. Añadir conocimiento propio a un agente

```http
POST /api/v1/agent/solidset/agents/{agent_resource_id}/knowledge
```

Guarda conocimiento privado en PostgreSQL y lo indexa en Qdrant.

Conocimiento general del agente:

```json
{
  "Title": "Especialización",
  "KnowledgeText": "Este agente está especializado en mantenimiento preventivo de tornos CNC.",
  "Source": "solidset",
  "active": true
}
```

Conocimiento específico de un canal:

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "Title": "Máquinas del canal",
  "KnowledgeText": "Este canal gestiona los tornos de la línea cuatro.",
  "Source": "solidset",
  "active": true
}
```

Comportamiento:

- Guarda el contenido en `SysResourceIAKnowledge`.
- Si no contiene `IDWorkRoom`, puede utilizarlo el agente en todos sus canales.
- Si contiene `IDWorkRoom`, solo se utiliza dentro de ese canal.
- Otro agente no puede recuperar este conocimiento.
- `active = false` conserva el contenido, pero deja de utilizarlo.

---

## 4. Ejecutar varios agentes

```http
POST /api/v1/agent/solidset/multi-agent/dialogue
```

Es el endpoint principal de la arquitectura multiagente.

```json
{
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "RawMessage": "¿Cuál puede ser la causa de esta alarma?",
  "SenderResourceId": "ba55b081-3e30-4f38-9816-194720c6701f",
  "SelectedAgentResourceIds": [
    "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
    "272700d8-d1ba-46a6-a121-b76fce8ecb9f"
  ],
  "SendToSolidSET": true,
  "SolidSETInstanceCode": "solidset-lisboa"
}
```

`SolidSETInstanceCode` es obligatorio cuando `SendToSolidSET=true`. Si solo se desea generar la respuesta sin publicarla, puede mantenerse `SendToSolidSET=false` y omitir la instancia.

Comportamiento:

1. Comprueba que cada agente existe.
2. Comprueba que está activo globalmente.
3. Comprueba que está activo y asignado al canal.
4. Crea o actualiza una fila en `SysAgentIASession`.
5. Ejecuta todos los agentes en paralelo.
6. Cada uno utiliza su propia memoria y conocimiento.
7. Devuelve una respuesta independiente por agente.
8. Con `SendToSolidSET = true`, publica las respuestas en el canal.

El mismo `IDResource` puede aparecer como remitente y agente configurado. Esto es válido cuando una persona interviene usando el recurso que SolidSET ha configurado como agente; el bloqueo de bucles se realiza mediante `Info.generated_by_ia`, no descartando el recurso remitente.

Respuesta:

```json
{
  "IDSession": "06e64429-fb46-4544-a0be-c6bbde4acd66",
  "IDWorkRoom": "007e3b2a-bbf6-4f46-8cbd-26d26db06ec1",
  "responses": [
    {
      "IDAgentResource": "ce0e837a-fe28-47ae-9ba0-8841fe042ca8",
      "AgentName": "Agente de mantenimiento",
      "response": "La alarma puede estar relacionada con...",
      "sent": false,
      "sendDetail": null
    }
  ]
}
```

`IDSession` debe ser UUID. Si no se envía, la API genera uno.

# Sincronización con SQL Server

## 5. Sincronizar recursos

```http
POST /api/v1/agent/solidset/resources/sync
```

Ejecuta la consulta de `SysResources` y `SysLogin`.

Mapeo:

```text
SysResources.DisplayName → SysResourceIA.Name
SysResources.ResourceId  → SysResourceIA.IDResource
SysResources.ActiveIDLogin2Resource → SysResourceIA.ActiveIDLogin2Resource
```

La sincronización es idempotente:

- Inserta recursos nuevos.
- Actualiza recursos existentes.
- No crea duplicados.
- Conserva el estado `active`.

Respuesta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 126,
  "synchronized": 126,
  "inserted": 0,
  "updated": 126,
  "skipped": 0
}
```

---

## 6. Sincronizar recursos y canales

```http
POST /api/v1/agent/solidset/chat-workroom/sync
```

Sincroniza las asignaciones de recursos a canales desde:

```text
SysResources
SysLogin
SysWorkRoomResource
SysWorkRoom
```

Mapeo:

```text
ResourceId → SysChatIAResource.IDResource
IDWorkRoom → SysChatIAResource.IDWorkRoom
```

Comportamiento:

- Crea las relaciones recurso–canal.
- Evita duplicados por `(IDResource, IDWorkRoom)`.
- No modifica las sesiones.
- Las relaciones nuevas quedan activas por defecto.

---

## 7. Sincronizar el catálogo de canales

```http
POST /api/v1/agent/solidset/workrooms/sync
```

Ejecuta esta consulta en SQL Server:

```sql
SELECT Code, Name, Description, IDWorkRoom
FROM dbo.SysWorkRoom;
```

Mapeo hacia PostgreSQL:

```text
Code        → SysWorkRoom.Code
Name        → SysWorkRoom.Name
Description → SysWorkRoom.Description
IDWorkRoom  → SysWorkRoom.IDWorkRoom
```

La sincronización hace `UPSERT` por `IDWorkRoom`, elimina los espacios añadidos por el tipo `NCHAR` de SQL Server y no crea duplicados.

`SysWorkRoom.IDWorkRoom` es la clave padre de:

- `SysChatIAResource.IDWorkRoom`.
- `SysResourceIAKnowledge.IDWorkRoom`.
- `SysAgentIASession.IDWorkRoom`.

Respuesta aproximada:

```json
{
  "status": "synchronized",
  "sourceRows": 29976,
  "synchronized": 29976,
  "inserted": 0,
  "updated": 29976,
  "skipped": 0
}
```

---

## 8. Sincronizar cuentas de acceso

```http
POST /api/v1/agent/solidset/logins/sync
```

Ejecuta en SQL Server:

```sql
SELECT Username, FullName, Password, Salt, IDLogin,
       LastIDResource, ActiveIDLogin2Resource
FROM dbo.SysLogin;
```

Guarda los datos en PostgreSQL `SysLogin` mediante un `UPSERT` por `IDLogin`. La cuenta exacta del agente se resuelve uniendo `SysResourceIA.ActiveIDLogin2Resource` con `SysLogin.ActiveIDLogin2Resource`; esto evita elegir otro usuario que tenga el mismo `LastIDResource`.

Mapeo adicional:

```text
dbo.SysLogin.FullName → PostgreSQL SysLogin.FullName
```

Al enviar una respuesta automática o multiagente, el router entrega el `agent_resource_id` seleccionado al método `_solidset_login`. Este método busca una cuenta cuyo `SysLogin.LastIDResource` coincida con `SysResourceIA.IDResource` y, antes de autenticar, exige que `SysResourceIA.active=true`. Después inicia una sesión independiente con `POST /User/LoginJson` y publica el mensaje con las cookies de esa misma sesión.

La autenticación del agente envía internamente:

```text
UserName          = SysLogin.Username
Password          = SysLogin.Password
PasswordEncrypted = true
TimezoneID        = SOLIDSET_TIMEZONE_ID
Resources[0]      = SysResourceIA.IDResource
```

`SysLogin.Password` es el HMAC ya generado por SolidSET, no una contraseña reversible. `PasswordEncrypted=true` hace que el método de SolidSET omita `GenerateHMAC` y compare directamente ese valor. `Resources[0]` obliga a registrar la sesión con el recurso agente solicitado cuando el login dispone de varios recursos. Si el recurso no es un agente activo, no tiene una cuenta válida o `LoginJson` rechaza el acceso, el envío falla explícitamente y no utiliza la identidad global configurada en `.env`.

La respuesta publicada usa `SysLogin.FullName` y conserva una identificación visible con el formato `Asistente IA {FullName}: respuesta`; por ejemplo, `Asistente IA Alejandro Veitia: ...`. SolidSET muestra además como emisor el login propio del recurso. Si excepcionalmente `FullName` está vacío, se utiliza `SysResourceIA.Name` como respaldo.

La respuesta contiene únicamente contadores; nunca devuelve ni registra `Password` o `Salt`:

```json
{
  "status": "synchronized",
  "sourceRows": 325,
  "synchronized": 325,
  "inserted": 325,
  "updated": 0,
  "skipped": 0
}
```

Los campos `Password` y `Salt` son datos sensibles. El acceso al esquema PostgreSQL debe quedar limitado al servicio del agente y no deben incluirse en endpoints de consulta, logs ni mensajes de error.

# Entrada de mensajes desde SolidSET

## 9. Recibir una notificación FrameworkMessage

```http
POST /api/v1/agent/notification/framework-message
```

Recibe directamente un `FrameworkMessage` de SolidSET.

Funciones:

- Normaliza el mensaje.
- Lo captura para aprendizaje.
- Lo indexa en Qdrant.
- Identifica los agentes seleccionados.
- Programa respuestas automáticas si corresponde.
- Descarta mensajes marcados como `generated_by_ia`.

Este endpoint no funciona como proxy: recibe y procesa el mensaje.

Cada mensaje humano se indexa primero como aprendizaje global del sistema. Si `IDSenderResource` coincide con un `SysResourceIA.IDResource` activo, también se indexa una copia privada con `scope=agent_owner_behavior` y `agent_resource_id` del propietario. Así, cada agente aprende el conocimiento, vocabulario y patrones expresados por su propio recurso humano, mientras todos los agentes continúan aprendiendo del contexto general permitido. La copia privada queda excluida de los demás agentes mediante el filtro `agent_resource_id`; las respuestas generadas por IA no vuelven a entrar en este ciclo.

Para identificar agentes candidatos, el router admite estas fuentes del payload:

```text
Chat.destiny[].talkWithAgent=true + type=2 (prioridad absoluta)
Destiny.dests[].resource
SelectedAgentResourceIds[] (solo cuando Destiny.dests está vacío)
Destiny.resource (solo cuando Destiny.dests está vacío)
```

La nueva señal canónica es `Chat.destiny[].talkWithAgent`. Si el campo aparece en cualquiera de las entradas, esa colección tiene precedencia absoluta: únicamente responde cada entrada con `talkWithAgent=true`, `type=2` y un `idResource` válido. Las entradas humanas (`type=1`), los agentes con `talkWithAgent=false` y cualquier agente presente solamente en las fuentes antiguas quedan excluidos. Si la señal no aparece, se mantienen las reglas de compatibilidad anteriores basadas en `Destiny.dests` y en el contexto de chat privado o meeting.

Solo responde el recurso seleccionado si existe en `SysResourceIA`, tiene `active=true` y está habilitado para el canal. Una lista auxiliar `SelectedAgentResourceIds` no puede añadir otros agentes cuando el payload contiene una selección autoritativa.

La respuesta invierte siempre la relación del mensaje original. Si la entrada es `Alejandro -> Víctor`, el agente inicia sesión con la cuenta de Víctor y publica `Víctor -> Alejandro`: `Destiny.WorkRoom` conserva el canal y `Destiny.Dests[0].Resource`/`Login` contienen el recurso y login del autor original. Esta inversión se aplica después de seleccionar el agente, porque la detección inicial solo puede conocer una identidad global y no todos los agentes dinámicos registrados.

En el formulario de respuesta se envían `Destiny.Dests[0].Type=2` y `Destiny.Dests[0].Kind=2` para que las versiones nuevas y anteriores de SolidSET reconozcan la intervención de IA.

`Chat.resourceTable` por sí sola nunca selecciona agentes. `Chat.destiny` solo los selecciona mediante `talkWithAgent=true` o mediante las reglas antiguas específicas de chat privado y meeting. De este modo, estar presente en el canal no autoriza a un agente a responder. Si el recurso destinatario activo todavía no tiene relación con un canal privado o dinámico, el router crea exclusivamente para ese destino `SysChatIAResource(IDResource, IDWorkRoom)` con `active=true`.

`Chat.channels[].idChannel` y `Chat.idWorkRoom` se interpretan como `SysWorkRoom.IDWorkRoom`.

Un mensaje humano puede tener el mismo `Sender.resource` que el agente configurado. El agente puede responder porque SolidSET utiliza ese recurso como identidad compartida; únicamente se descartan mensajes que lleguen marcados con `Info.generated_by_ia`.

En un chat privado propio (`Chat.channels[].channelKind=1`) es válido conversar con el agente asociado al mismo recurso del usuario. Cuando `Destiny.dests` está vacío, el router toma exclusivamente `Chat.destiny[].idResource` con `type=1` como propietario del canal privado. Ese recurso todavía debe existir como agente activo. Esta excepción solo aplica a chats privados y no altera la regla de meetings, donde `type=1` es el autor y nunca responde.

Cuando el propietario y el agente comparten el mismo `IDResource`, la respuesta conserva `SysResourceIA.IDResource` para login y permisos y envía `SysResourceIA.ID` como identidad lógica en `Info[agent_resource_id]`, `IDAgentIA`, `Info[id_agent_ia]` e `Info[agent_id]`. No se intenta sustituir `Sender` desde el formulario: SolidSET llama `St_SendMessageSync(req, currentL, currentS, currentR)` y persiste el remitente de la sesión autenticada.

Para mostrar una autorrespuesta a la izquierda, el cliente SolidSET debe considerar la marca de agente al calcular `ChatView.FromSelf`: si `Info[generated_by_ia]=1` y `Info[id_agent_ia]` contiene un UUID distinto, el mensaje debe tratarse visualmente como `FromSelf=false`, aunque `Chat.IDSender`/`IDSenderResource` coincidan con el usuario autenticado. La alternativa estructural es registrar para cada agente un login y recurso SolidSET independientes; en ese caso no se necesita una excepción visual.

### Respuestas dentro de meetings

Cuando el mensaje contiene `Info.meeting_id`, `ExtraData.meeting_id` o `Chat.idMeeting`, la respuesta se mantiene dentro del meeting. `meeting_mirror_general` ya no es necesario para detectar este contexto.

El formulario enviado a `/Chat/SendMessageForm` incluye:

```text
Destiny.WorkRoom      = canal técnico subyacente
Info[meeting_id]      = UUID del meeting
Info[meeting_code]    = código opcional, por ejemplo M10
ExtraData             = {"meeting_id":"...","meeting_code":"M10"}
```

El `WorkRoom` se conserva únicamente porque SolidSET lo utiliza como ruta de transporte. `ExtraData.meeting_id` es lo que vincula el nuevo chat al meeting y activa las validaciones de participante bloqueado o expulsado mostradas por `MeetingChatSendGuard`. La API no añade `Info[meeting_mirror_general]`, evitando convertir la respuesta en un espejo general del canal.

Antes del envío, la API valida que `meeting_id` exista en `dbo.SysMeeting`, esté activo y que su `IDChannel` coincida con `Destiny.WorkRoom`. Si el identificador recibido es obsoleto, intenta resolver el meeting mediante `meeting_code` dentro del mismo canal. Si ninguna reunión coincide, omite el ámbito meeting y envía al canal técnico, evitando conflictos con la FK `FK_SysChat2SysWorkRoom_SysMeeting`.

Cuando `Chat.chatQuestion` está presente, el agente recibe `chatQuestion.rawMessage` y `chatQuestion.idChat2` como contexto del mensaje citado. La petición actual continúa siendo `RawMessage`; el mensaje citado no sustituye al autor, los destinatarios ni el meeting actuales y se trata como contenido no confiable, no como una instrucción del sistema.

En meetings, `Chat.destiny` es la fuente canónica para decidir qué agente responde:

```text
Chat.destiny[].type = 1 → autor de la pregunta; nunca responde
Chat.destiny[].type = 2 → destinatario solicitado; puede responder
Chat.destiny[].sequence → orden de los destinatarios
```

Cuando `Chat.destiny` está presente, el router ignora `Destiny.dests`, porque esta última colección puede contener copias técnicas para el autor y otros participantes del meeting. Solo los recursos `type=2` pasan después por las validaciones de `SysResourceIA.active` y asignación al canal técnico. Si únicamente existe una entrada `type=1`, no se ejecuta ningún agente. `Destiny.dests` se utiliza como respaldo exclusivamente si el payload de meeting no contiene `Chat.destiny`.

---

## 10. Capturar y reenviar FrameworkHub

```http
POST /api/v1/agent/notification/frameworkHub/SendMessage
```

Funciona como proxy entre SolidSET y el endpoint real de notificaciones.

Flujo:

```text
Mensaje entrante
    ↓
Captura e indexación
    ↓
Reenvío al endpoint real de SolidSET
    ↓
Programación de agentes seleccionados
```

Conserva el cuerpo, las cabeceras y los parámetros relevantes.

La respuesta incluye cabeceras como:

```text
X-Agent-Capture-Learned
X-Agent-Replies-Scheduled
```

# Conversación tradicional

## 11. Diálogo con un único agente

Los saludos identifican respetuosamente al interlocutor mediante el `FullName` asociado al recurso, por ejemplo: `¡Hola, Alejandro Veitia! Es un placer saludarte. ¿En qué puedo ayudarte?`. No muestran el alias del recurso, perfil, rol, permisos ni cantidad o nombres de canales. Si no se puede resolver `FullName`, se utiliza el mismo saludo sin nombre.

```http
POST /api/v1/agent/dialogue
```

Procesa un `FrameworkMessage` mediante el agente tradicional.

Utiliza principalmente:

```json
{
  "RawMessage": "Consulta del usuario",
  "Sender": {
    "resource": "UUID-del-usuario",
    "login": "UUID-del-login"
  },
  "Destiny": {
    "workRoom": "UUID-del-canal"
  }
}
```

Funciones:

- Valida contenido y longitud.
- Detecta prompt injection.
- Resuelve usuario, recurso y canal.
- Recupera contexto SQL Server y Qdrant.
- Usa memoria Redis.
- Ejecuta herramientas permitidas.
- Devuelve una única respuesta.

Para nuevos desarrollos con varios agentes debe preferirse `/multi-agent/dialogue`.

---

## 12. Registrar feedback

```http
POST /api/v1/agent/feedback
```

Registra una valoración, corrección o señal de aprendizaje.

```json
{
  "session_id": "session-123",
  "user_id": "usuario",
  "user_text": "La pregunta original",
  "agent_response": "La respuesta del agente",
  "corrected_response": "La respuesta correcta",
  "canal_id": "UUID-del-canal",
  "feedback_type": "explicit",
  "reason": "La causa indicada era incorrecta",
  "update_profile": true
}
```

Funciones:

- Analiza la reacción.
- Guarda la corrección.
- Actualiza el perfil dinámico.
- Incorpora aprendizaje de largo plazo.

Para feedback multiagente convendría que SolidSET conserve también el `IDAgentResource` que produjo la respuesta.

---

## 13. Capturar una reacción de SolidSET

```http
POST /api/v1/agent/solidset/reactions/capture
```

Recibe el mismo contrato de `ChangeReactionRequest` después de que SolidSET haya establecido `IDUser` desde la sesión:

```json
{
  "IDChat": 1822812,
  "IDUser": "1790fc78-023d-4506-a7e8-5c030e9386d1",
  "IDChannel": "d8e82821-d52f-44bf-9b70-682651a6196e",
  "IDEmoji": "U+1F64F",
  "Counter": 1
}
```

La API consulta `dbo.SysChat.IDChat2`, comprueba que el emisor esté registrado en `SysResourceIA` y que el mensaje comience con `Asistente IA`. Después guarda el evento en PostgreSQL `SysAgentIAReaction` y lo incorpora al aprendizaje aislado del agente que emitió la respuesta.

Este endpoint cierra un ciclo de Reinforcement Learning basado en memoria de preferencias:

```text
positive → reward = +Counter
negative → reward = -Counter
neutral  → reward = +0.1 × Counter
removed  → reward = 0
```

Antes de generar futuras respuestas, el agente consulta sus recompensas del canal. Los patrones positivos se presentan como ejemplos cuyo enfoque y claridad debe favorecer; los negativos como patrones que debe corregir y evitar. La política está aislada por `IDAgentResource` y `IDChannel`, no mezcla reacciones entre agentes y nunca expone al usuario las recompensas internas.

Se trata de RL con memoria y recuperación de preferencias, apropiado para mejora online segura. No modifica en caliente los pesos del modelo base ni copia literalmente respuestas anteriores.

Señales posibles:

```text
positive → aprobación, agradecimiento, corazón, celebración
negative → desaprobación, enfado o tristeza
neutral  → emoji sin clasificación explícita
removed  → Counter = 0; se registra la retirada pero no se aprende
```

La combinación `(IDChat, IDUser, IDEmoji)` es idempotente. Repetir el mismo contador no genera aprendizaje duplicado.

Respuesta:

```json
{
  "status": "captured",
  "learned": true,
  "changed": true,
  "signal": "positive",
  "reward": 1.0,
  "IDChat": 1822812,
  "IDAgentResource": "272700d8-d1ba-46a6-a121-b76fce8ecb9f",
  "AgentName": "Asistente IA Victor Vargas"
}
```

# Memoria y archivos

## 14. Consultar historial

```http
GET /api/v1/agent/history/{session_id}
```

Devuelve mensajes almacenados en Redis.

Parámetros opcionales:

```text
before
limit
```

Se utiliza para paginar el historial de una conversación.

En multiagente, el identificador interno incluye agente, canal y sesión.

---

## 15. Eliminar historial

```http
DELETE /api/v1/agent/history/{session_id}
```

Borra la memoria Redis correspondiente a una sesión.

Es una operación destructiva: elimina el historial conversacional de esa clave.

---

## 16. Obtener audio generado

```http
GET /api/v1/agent/audio-response?file=nombre.mp3
```

Devuelve un archivo de audio creado previamente por el agente.

Valida que el archivo exista y que la ruta solicitada sea segura.

# Supervisión y diagnóstico

## 17. Estado general del agente

```http
GET /api/v1/agent/health
```

Comprueba el estado del servicio y dependencias como:

- Ollama.
- Qdrant.
- Redis.
- PostgreSQL.
- SQL Server.
- SolidSET.
- Notification API.

Es el endpoint principal para monitorización.

---

## 18. Resumen de evaluación

```http
GET /api/v1/agent/evaluation/summary
```

Devuelve métricas operativas relacionadas con:

- Diálogos procesados.
- Duración.
- Caché.
- Errores.
- Captura de notificaciones.
- Autorrespuestas.
- Estado de integraciones.

---

## 19. Mensajes recientes capturados

```http
GET /api/v1/agent/notification/recent-messages?limit=30
```

Devuelve los últimos mensajes capturados por el listener.

El límite permitido está entre 1 y 200.

Sirve para comprobar que SolidSET está enviando correctamente:

- Mensaje.
- Canal.
- Remitente.
- Identificadores.
- Tipo de evento.

---

## 20. Contexto de un usuario

```http
GET /api/v1/agent/context/{user_id}
```

Devuelve el contexto calculado para un usuario:

- Identidad.
- Roles.
- Canales.
- Actividades recientes.
- Recursos disponibles.
- Permisos.
- Perfil aprendido.

Se usa principalmente para depuración.

---

## 21. Métricas de reintentos SQL

```http
GET /api/v1/agent/sql-retry-stats
```

Muestra:

- Reintentos de conexión.
- Reintentos de consultas.
- Operaciones que generaron reintentos.
- Última fecha de reintento.

---

## 22. Reiniciar métricas SQL

```http
POST /api/v1/agent/sql-retry-stats/reset
```

Pone a cero las métricas de reintentos SQL.

No modifica tablas ni datos de SolidSET; solamente reinicia contadores internos.

# Conectividad

## 23. Probar SolidSET

```http
GET /api/v1/connectivity/solidset
```

Prueba la conectividad con SolidSET, normalmente mediante su endpoint de heartbeat.

Sirve para diagnosticar:

- URL incorrecta.
- Servicio no disponible.
- Timeout.
- Problemas TLS.
- Respuesta HTTP inesperada.

---

## 24. Probar todas las integraciones

```http
GET /api/v1/connectivity/all
```

Ejecuta una comprobación conjunta de los servicios configurados.

Permite localizar rápidamente si el problema está en:

- SolidSET.
- Notification API.
- PostgreSQL.
- SQL Server.
- Redis.
- Qdrant.
- Ollama.

## Tablas y responsabilidades

```text
SysResourceIA ──────────────┐
Identidad del agente        │
                            ├──► SysChatIAResource
SysWorkRoom ────────────────┘    Asignación agente–canal
Catálogo de canales                  │
                                     ├── active
                                     └── response_order
                                     │
                                     ▼
SysAgentIASession
    Conversaciones por agente y canal

SysResourceIAKnowledge
    Conocimiento privado por agente y, opcionalmente, canal
        │
        ▼
Qdrant
    Recuperación semántica aislada

SysAgentIASession
        │
        ▼
Redis
    Memoria conversacional rápida
```

La regla central es:

```text
Un mensaje puede seleccionar varios agentes.
Cada agente se valida y ejecuta por separado.
Cada agente mantiene su propia sesión, memoria y conocimiento.
```
# Conectividad de producción: SQL Server y Qdrant

En este servidor Docker, SQL Server se configura como instancia nombrada. El
host y la instancia se declaran por separado y la API construye para FreeTDS
el destino `host.docker.internal\SQL2017DEV` sin forzar el puerto `1433`. No se
debe usar `.\\SQL2017DEV`: el punto identifica al propio contenedor y las dos
barras literales provocan un destino inválido.

Variables usadas por `agent-service`:

- `SQL_SERVER_DOCKER_HOST`: host alcanzable desde Docker; normalmente
  `host.docker.internal` cuando SQL Server está en el mismo servidor Windows.
- `SQL_SERVER_INSTANCE`: nombre de instancia, actualmente `SQL2017DEV`. Cuando
  tiene valor, no se envía un puerto explícito y FreeTDS consulta SQL Browser.
- `SQL_SERVER_DOCKER_PORT`: solamente se utiliza cuando
  `SQL_SERVER_INSTANCE` está vacío; su valor predeterminado es `1433`.
- `SQL_SERVER_DB`, `SQL_SERVER_USER` y `SQL_SERVER_PASSWORD`: base de datos y
  credenciales de SQL Server.

El `.env` de producción debe declarar `ENVIRONMENT=production`, utilizar
`OLLAMA_BASE_URL=http://ollama-llm:11434` y no contener una cuenta global en
`SOLIDSET_LOGIN_*`; la identidad para responder se obtiene de `SysLogin` según
el recurso agente seleccionado.

Ejemplo para producción:

```env
SQL_SERVER_DOCKER_HOST=host.docker.internal
SQL_SERVER_INSTANCE=SQL2017DEV
SQL_SERVER_DOCKER_PORT=1433
SQL_SERVER_DB=DEV_ISIFrameIsicom
SQL_SERVER_USER=sa
SQL_SERVER_PASSWORD=<secreto>
```

Si SQL Browser/UDP 1434 no es alcanzable desde Docker, debe dejarse
`SQL_SERVER_INSTANCE=` vacío y asignar a `SQL_SERVER_DOCKER_PORT` el puerto TCP
real publicado por `SQL2017DEV`. El Compose preserva expresamente el valor
vacío para activar este modo de conexión directa.

La cuenta global antigua de SolidSET queda deshabilitada en
`docker-compose-prod.yml`. Cada respuesta inicia sesión con el `SysLogin` del
recurso agente almacenado en PostgreSQL.

Qdrant dispone de una comprobación TCP de salud. El agente no comienza hasta
que `vector-db:6333` acepta conexiones, evitando que la creación inicial de la
colección `machining_docs` falle por una carrera de arranque.
