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

La resolución habitual de identidad (`Username`, `FullName`, `IDLogin`, `IDResource`) utiliza exclusivamente la réplica PostgreSQL `SysLogin`. Todas las lecturas de SQL Server —sincronización, histórico, validación, aprendizaje y consultas operativas— se realizan mediante la SolidSET Data API independiente. El agente no abre conexiones TCP a SQL Server ni utiliza sus variables de conexión. En el perfil CPU de producción, Ollama usa `OLLAMA_KV_CACHE_TYPE=f16` porque una caché V cuantizada requiere Flash Attention.

Cada instancia configura su gateway en PostgreSQL `SysSolidSETDataAPI`. La URL,
timeout, límite y validación TLS se guardan por instancia; la API key se cifra y
nunca se devuelve. Las credenciales SQL Server existen únicamente en el fichero
de entorno del proyecto independiente `solidset-data-api`, desplegado junto al
servidor de base de datos.

El despliegue `docker-compose-prod.yml` utiliza Ollama por CPU de forma predeterminada y no exige el runtime NVIDIA. Cuando `docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi` funcione correctamente, la aceleración se activa añadiendo el overlay `docker-compose-prod.gpu.yml`. En producción Uvicorn se ejecuta sin `--reload`.

El despliegue `docker-compose-dev.yml` replica la topología funcional de
producción, pero publica exclusivamente HTTP en los puertos 80 y 8000, no
incluye Certbot ni monta certificados. Conserva el montaje del código fuente y
Uvicorn `--reload` para desarrollo. También espera la salud de PostgreSQL,
Redis, Ollama y Qdrant, utiliza el perfil CPU seguro y resuelve SolidSET desde
`SysSolidSETInstance` y las identidades desde `SysLogin` en PostgreSQL.

Última actualización: 22 de agosto de 2026.

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

En producción, docker-compose-prod.yml carga .env.production. Este archivo
contiene únicamente el fallback de modelo y el secreto maestro de cifrado; las
asignaciones dinámicas se leen de PostgreSQL mediante
SysLLMProviderConfiguration y SysAgentIAModel. Debe conservarse fuera del
control de versiones y copiarse junto al Compose durante el despliegue.

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

Las consultas directas de conteo en SQL Server solo se habilitan cuando la
pregunta contiene explícitamente `recurso(s)`, `usuario(s)` o sus equivalentes
portugués/inglés. Un cuantificador aislado, por ejemplo «cuántas Champions», no
activa SQL y continúa por el proveedor de conocimiento externo correspondiente.

Además se envía el bloque `Chat` que utiliza el cliente SolidSET para pintar
`From` y `To`. En una conversación con el agente propio queda:

```text
Chat.IDSenderResource = SysResourceIA.IDAgentResource
Chat.IDSender = login del propietario del agente, si existe; si no, se omite
Chat.IDWorkRoom = IDWorkRoom
Chat.IDMeeting = meeting válido (si existe)
Chat.RawMessage = respuesta
Chat.Kind = 60
Chat.Destiny[0] = agente, Type=1, TalkWithAgent=true
Chat.Destiny[1] = recurso humano, Type=2
```

El sobre de respuesta declara además `Sender.Resource=IDAgentResource`,
`Sender.Login=login del propietario` cuando existe y mantiene
`Sender.Session`/`Sender.WorkRoom` en GUID cero. El canal de entrega se indica
en `Destiny.WorkRoom`; su único destino es el recurso humano con `Kind=2`.

Así la UI recibe `From: agente [IA] To: humano`, en lugar de reutilizar la
dirección del mensaje original `From: humano To: agente [IA]`.

`SysResourceIA.IDResource` identifica al recurso humano propietario y se usa
para seleccionar el agente, resolver su login, memoria y conocimiento.
`SysResourceIA.IDAgentResource` identifica al recurso software que representa
al remitente técnico del agente en SolidSET y procede de
`dbo.SysResource2Agent.IDAgentResource`; se usa en `IDAgentIA` y como identidad
técnica. También identifica siempre al participante From mediante
`Chat.Destiny[0].IDResource`, incluso cuando el propietario conversa con su
propia IA. `Chat.Destiny[1]` contiene exclusivamente el recurso humano
destinatario y no lleva `TalkWithAgent`. `Chat.IDSenderResource` contiene el
mismo `IDAgentResource` verificado que identifica al From. `Chat.IDSender`
contiene exclusivamente el login del propietario cuando está disponible;
nunca contiene un GUID de recurso y se omite si el agente no tiene login.
Nunca se utiliza
el UUID interno `SysResourceIA.ID` como
participante de SolidSET. Si un agente activo todavía no tiene
`IDAgentResource`, la respuesta se omite para no publicarla con una identidad
técnica incorrecta.

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

Registra o actualiza por `Code` las URLs y la SolidSET Data API de una
instalación. El agente no recibe credenciales SQL Server:

```json
{
  "Code": "solidset-lisboa",
  "Name": "SolidSET Lisboa",
  "BaseUrl": "http://192.168.10.20:52130",
  "NotificationUrl": "http://192.168.10.20:52131",
  "SourceIP": "192.168.10.20",
  "CountryCode": "PT",
  "Locale": "pt-PT",
  "TimeZone": "Europe/Lisbon",
  "DataAPI": {
    "BaseUrl": "https://192.168.10.20:8081",
    "APIKey": "<secret>",
    "TimeoutSeconds": 120,
    "MaxRows": 5000,
    "VerifyTLS": true,
    "active": true
  },
  "active": true
}
```

La configuración general se guarda en `SysSolidSETInstance` y el gateway en
`SysSolidSETDataAPI`. La API key se cifra con Fernet antes de persistirse y nunca
se devuelve: la respuesta solo contiene `APIKeyConfigured=true`. Para actualizar
una instancia sin cambiarla se omite `DataAPI.APIKey`.

El campo `Database` ya no forma parte del contrato de este endpoint. Si se
envía, FastAPI responde `422` porque las credenciales y parámetros de SQL Server
pertenecen exclusivamente al despliegue independiente `solidset-data-api/.env`.
La tabla PostgreSQL heredada `SysSolidSETDatabase` se conserva temporalmente
para una migración segura, pero este endpoint ya no la lee ni la actualiza.
Si no se proporciona `LLM_CREDENTIAL_ENCRYPTION_KEY`, la API genera una clave
Fernet una sola vez en `/app/data/credential.key`. El directorio `data` ya está
montado de forma persistente en desarrollo, API, producer y worker. También se
puede proporcionar la clave como secreto de despliegue; no es la API key ni una
credencial SQL Server. El fichero debe incluirse en las copias de seguridad: si
se pierde, las credenciales guardadas no se pueden recuperar.
Si la variable o el fichero contienen una clave que no es Fernet válida, la
variable se ignora y el fichero se conserva como
`credential.key.invalid-<timestamp>` antes de generar una clave correcta.

`DataAPI.BaseUrl` debe ser alcanzable desde los contenedores del agente. Si el
gateway de prueba está en el mismo compose se usa
`http://solidset-data-api:8080`; si está en el servidor SolidSET se utiliza su
DNS o IP HTTPS. Para facilitar el desarrollo, una URL registrada con
`localhost`, `127.0.0.1` o `::1` se traduce en tiempo de ejecución a
`host.docker.internal` cuando el agente está dentro de Docker. Fuera de Docker
la URL se conserva sin cambios.

Si SQL Server está en otro Compose, la Data API puede iniciarse además con
`solidset-data-api/docker-compose.sql-container.yml`. El overlay incorpora la
red externa configurada en `SQL_SERVER_DOCKER_NETWORK` y permite usar el nombre
del contenedor SQL como `SQL_SERVER_HOST`, sin depender de un puerto del host.

Antes de insertar, la API busca coincidencias por `Code`, `BaseUrl` o `SourceIP`;
si encuentra alguna, actualiza la misma fila y conserva su `ID`. `BaseUrl` se
utiliza para login y respuestas; `NotificationUrl`, para notificaciones.

Después del registro se verifica la conexión mediante:

```http
POST /api/v1/agent/solidset/instances/solidset-lisboa/test-connection
```

La prueba atraviesa la Data API y devuelve el catálogo real, una versión
abreviada del servidor, el adaptador y si existe `dbo.SysResource2Agent`; nunca
incluye usuario, contraseña, API key cifrada ni cadena completa. Las trazas del
servidor muestran únicamente `instance`, `DataAPI.BaseUrl`, el tipo de error y
una causa técnica abreviada.

El proyecto independiente está en `solidset-data-api/` y expone:

```http
GET  /health
GET  /api/v1/system/capabilities
GET  /api/v1/datasets/{dataset}
GET  /api/v1/agents/{humanResourceId}
POST /api/v1/query/read
```

Los endpoints protegidos requieren `X-SolidSET-Data-Key`. `query/read` admite
solo `SELECT` o CTE parametrizadas, rechaza escritura, procedimientos,
comentarios y múltiples instrucciones, y limita el número de filas. La cuenta
SQL configurada en el gateway también debe tener permisos exclusivamente de
lectura. Esta primera versión conserva las consultas existentes mientras su
ejecución y las credenciales quedan fuera del agente.

El adaptador de compatibilidad elimina comentarios SQL heredados antes de
enviar una consulta de lectura al gateway. Los valores temporales devueltos por
JSON se normalizan desde ISO 8601 antes de construir el contexto del agente.

Los datasets `resources`, `logins`, `workrooms` y `workroom-resources`, junto
con la validación `agents/{humanResourceId}`, mantienen sus consultas dentro del
proyecto independiente. Las consultas históricas y de aprendizaje cuyo SQL se
adapta dinámicamente al esquema utilizan `query/read`, pero también se ejecutan
exclusivamente dentro del gateway.

`GET /api/v1/datasets/{dataset}` admite `offset` y `limit` y devuelve además
`hasMore` y `nextOffset`. El conector del agente recorre automáticamente todas
las páginas, por lo que una instalación con más filas que `MaxRows` no queda
sincronizada parcialmente.

Al responder dentro de un meeting, el agente valida el `meeting_id` utilizando
la `BaseUrl` de la instancia seleccionada. Las preguntas sobre recursos o
participantes de un meeting no utilizan el contador global de recursos: se
resuelven contra `dbo.SysMeeting2Resource` usando el `meeting_id` de la
conversación y excluyen recursos pendientes, bloqueados o expulsados. La URL
lógica `localhost` y su traducción Docker `host.docker.internal` identifican la
misma instancia al seleccionar el `SysLogin` del agente.

Para ejecutar el gateway de prueba en la misma máquina:

```powershell
docker compose -f docker-compose-dev.yml --profile data-api up -d --build solidset-data-api
```

Para desplegarlo completamente separado en el servidor donde está SQL Server,
se utiliza el Compose incluido dentro del proyecto independiente:

```powershell
Set-Location solidset-data-api
Copy-Item .env.example .env
# Configurar SQL_SERVER_* y SOLIDSET_DATA_API_KEY en .env.
docker compose up -d --build
```

Este Compose crea solamente `solidset_data_api`, su red privada y el
healthcheck; no requiere ningún contenedor del agente.

`CountryCode`, `Locale` y `TimeZone` definen el contexto regional de las
respuestas. `TimeZone` debe ser una zona IANA válida, como `Europe/Lisbon`, y
`Locale` utiliza formato BCP 47, como `pt-PT`. Las instalaciones existentes se
migran automáticamente con `PT`, `pt-PT` y `Europe/Lisbon`. No se geolocaliza la
IP privada ni se deduce el país a partir del idioma: esos métodos no son fiables
detrás de NAT, VPN o Docker.

El agente recibe estos valores en todas las respuestas de esa instancia. Para
`pt-PT` emplea vocabulario y ortografía de Portugal. Las consultas explícitas de
fecha u hora (`Que dia é hoje?`, `Que horas são?`) se calculan directamente con
`TimeZone`, sin pedir al LLM que adivine la ubicación; por ello no puede responder
con la hora de Brasilia cuando la instancia está configurada en Portugal.

El idioma de la pregunta siempre tiene prioridad sobre `Locale`: una pregunta
en inglés recibe una respuesta en inglés aunque la instancia use `pt-PT`; una
pregunta en español recibe español. `Locale` solo determina la variante regional
cuando el idioma coincide y nunca obliga a traducir la respuesta al portugués.

Si el cliente conoce la ubicación efectiva del recurso —por ejemplo, porque el
usuario está temporalmente en otro país— puede incluir en `Info`, `TimeData` o
`UserData` los campos `country_code`, `locale` y `time_zone`. Estos valores
específicos del mensaje tienen prioridad sobre la instancia; una zona IANA no
válida se descarta. Cuando el payload no los incluye, se usa la configuración de
`SysSolidSETInstance`. La IP observada por la API corresponde normalmente al
servidor SolidSET o al proxy, no al equipo WPF, por lo que no se usa para ubicar
al recurso.

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

Ejecuta la consulta de `SysResources` y `SysLogin`, y obtiene la identidad del
recurso software mediante la relación activa de `SysResource2Agent`.

Mapeo:

```text
SysResources.DisplayName → SysResourceIA.Name
SysResources.ResourceId  → SysResourceIA.IDResource
SysResources.ActiveIDLogin2Resource → SysResourceIA.ActiveIDLogin2Resource
SysResource2Agent.IDHumanResource → SysResourceIA.IDResource
SysResource2Agent.IDAgentResource → SysResourceIA.IDAgentResource
```

`SysResourceIA.ID` continúa siendo la clave interna autogenerada de PostgreSQL.
No se devuelve como identidad del agente SolidSET. Siempre que un contrato de
respuesta expone `IDAgentResource`, devuelve el GUID sincronizado desde
`dbo.SysResource2Agent.IDAgentResource`; `IDResource` conserva el GUID del
recurso humano propietario.

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

Al enviar una respuesta automática o multiagente, el router entrega el
`agent_resource_id` y el `IDLogin` seleccionado en `Chat.destiny` al método
`_solidset_login`. Este método exige que `SysResourceIA.active=true`, busca las
cuentas relacionadas por `ActiveIDLogin2Resource` y prioriza exactamente ese
`IDLogin`; así evita escoger una fila histórica mediante un orden arbitrario.
Después inicia una sesión independiente con `POST /User/LoginJson` y publica el
mensaje con las cookies de esa misma sesión. Si SolidSET devuelve HTTP 200 con
`Success=false`, la API resincroniza `dbo.SysLogin` hacia PostgreSQL y reintenta
una sola vez, cubriendo cambios recientes de ID o contraseña sin crear bucles.

La autenticación del agente envía internamente:

```text
UserName          = SysLogin.Username
Password          = SysLogin.Password
PasswordEncrypted = true
TimezoneID        = SOLIDSET_TIMEZONE_ID
Resources[0]      = SysResourceIA.IDResource
```

`SysLogin.Password` es el HMAC ya generado por SolidSET, no una contraseña reversible. `PasswordEncrypted=true` hace que el método de SolidSET omita `GenerateHMAC` y compare directamente ese valor. `Resources[0]` obliga a registrar la sesión con el recurso agente solicitado cuando el login dispone de varios recursos. Si el recurso no es un agente activo, no tiene una cuenta válida o `LoginJson` rechaza el acceso, el envío falla explícitamente y no utiliza la identidad global configurada en `.env`.

La respuesta publicada usa `SysLogin.FullName` y conserva una identificación visible con el formato `{FullName}: respuesta`; por ejemplo, `Alejandro Veitia: ...`. SolidSET muestra además como emisor el login propio del recurso. Si excepcionalmente `FullName` está vacío, se utiliza `SysResourceIA.Name` como respaldo.

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

En el modo predeterminado `AGENT_RESPONSE_QUEUE_ENABLED=true`, este endpoint
solo valida la instancia, toma `Chat.IDChat2`, crea el estado y publica el
mensaje original en Redis Stream. Devuelve inmediatamente; la captura Qdrant,
selección del agente, LLM y envío a SolidSET se ejecutan en `agent-worker`.
La confirmación HTTP es `202 Accepted`.
La resolución de `SysSolidSETInstance` se conserva 60 segundos en memoria para
evitar una consulta PostgreSQL por cada petición durante picos de carga; guardar
una configuración de instancia invalida inmediatamente esa caché.

La respuesta conserva `Result`, `Message` y `Error`, y añade los datos para
seguir el trabajo asíncrono:

```json
{
  "Result": 0,
  "requestId": "1824911",
  "status": "queued",
  "statusUrl": "/api/v1/agent/responses/1824911/status"
}
```

`requestId` corresponde directamente a `Chat.IDChat2`, convertido a texto. De
esta manera WPF puede relacionar el loading y los estados con el mensaje que ya
conoce. Solo las notificaciones técnicas sin `IDChat2` reciben un UUID temporal
de contingencia.

El cliente WPF debe conservar `requestId`, mostrar un indicador indeterminado y
consultar `statusUrl` cada 1–2 segundos hasta que `completed=true`.

### Sugerir una respuesta para `Chat.chatQuestion`

```http
POST /api/v1/agent/notification/chat-question/suggest-response
Content-Type: application/json
```

Recibe el mismo `FrameworkMessage`, pero no captura el mensaje como una nueva
petición de autorrespuesta ni envía nada a SolidSET. El endpoint toma:

- `Chat.IDChat2` como `requestId` para el seguimiento del estado.
- `Chat.IDSenderResource` como el recurso humano que solicita la sugerencia.
- `Chat.chatQuestion.IDSenderResource` como el autor del mensaje anterior.
- `Chat.chatQuestion.IDChat2` y `RawMessage` como el mensaje que debe responderse.
- `Chat.IDWorkRoom`, `Chat.IDMeeting` y `Info.meeting_code` como contexto.

Antes de generar, comprueba en SQL Server que el solicitante tiene una relación
activa en `dbo.SysResource2Agent`, sincroniza `IDAgentResource` y resuelve su
agente activo en PostgreSQL. La generación utiliza el conocimiento privado y el
contexto de refuerzo del agente propio del solicitante. No utiliza el agente del
autor citado, no consulta Internet y trata el texto citado como datos no
confiables.

La operación de sugerencia solo es válida cuando `Chat.RawMessage` está vacío.
El texto que debe contestarse procede de `Chat.chatQuestion.RawMessage`; si el
mensaje actual ya contiene texto, el endpoint devuelve HTTP 422 para evitar que
una respuesta escrita por el usuario sea sustituida.

Si termina correctamente devuelve HTTP 200 con una lista JSON de alternativas
independientes. El modelo intenta producir tres variantes —directa, breve y
colaborativa— en el mismo idioma del mensaje citado:

```json
{
  "requestId": "1824995",
  "questionChatId": "1824994",
  "status": "completed",
  "code": 5,
  "language": "pt",
  "suggestions": [
    {"id": "1", "text": "Obrigado pela informação. Vou confirmar esse ponto."},
    {"id": "2", "text": "Entendido, obrigado."},
    {"id": "3", "text": "Obrigado. Pretende que validemos este ponto em conjunto?"}
  ],
  "statusUrl": "/api/v1/agent/responses/1824995/status"
}
```

Cada `text` es apto para asignarse a `RawMessage`; no contiene nombre del
agente, prefijo ni payload de envío. La selección pertenece exclusivamente al
cliente y este endpoint nunca publica ninguna alternativa en SolidSET. Para
mostrar progreso mientras la llamada está abierta, WPF puede consultar en paralelo:

```http
GET /api/v1/agent/responses/{Chat.IDChat2}/status?lang=es
```

La secuencia normal es `queued` → `processing` → `searching` → `thinking` →
`completed`. No aparece `sending`, porque este endpoint nunca publica el texto
en SolidSET. Al completarse, el estado incluye también `result.questionChatId`,
`result.language` y `result.suggestions`, de modo que el cliente puede recuperar
las alternativas aunque se cierre la petición POST. Los errores de validación devuelven HTTP 422; la ausencia de un
agente propio activo devuelve HTTP 404; las dependencias o la generación no
disponibles devuelven HTTP 503 y dejan el estado en `failed`.

### Consultar el estado de una respuesta

```http
GET /api/v1/agent/responses/{requestId}/status?lang=es
```

Como recuperación alternativa usando el mensaje original:

```http
GET /api/v1/agent/responses/status?chatId={IDChat2}&lang=es
```

Los estados se guardan temporalmente en Redis durante
`AGENT_RESPONSE_STATUS_TTL_SECONDS` (86400 segundos por defecto):

`lang` admite `es`, `en` y `pt`; el valor predeterminado es `es`. Cada respuesta
incluye además `displayMessages` con las tres traducciones para que WPF pueda
cambiar el idioma sin volver a consultar la API.

| Code | Estado | Español | English | Português |
|---:|---|---|---|---|
| `0` | `queued` | `Esperando…` | `Waiting…` | `Aguardando…` |
| `1` | `processing` | `Procesando…` | `Processing…` | `Processando…` |
| `2` | `searching` | `Buscando información…` | `Searching for information…` | `Procurando informações…` |
| `3` | `thinking` | `Pensando…` | `Thinking…` | `Pensando…` |
| `4` | `sending` | `Enviando respuesta…` | `Sending response…` | `Enviando resposta…` |
| `5` | `completed` | `Respondido` | `Answered` | `Respondido` |
| `6` | `failed` | `No se pudo responder` | `Unable to respond` | `Não foi possível responder` |
| `7` | `cancelled` | `Cancelado` | `Cancelled` | `Cancelado` |

La respuesta de estado incluye `agents` para mostrar cada agente por separado,
`stageHistory`, `responseCount`, `createdAt`, `updatedAt`, `completedAt` y
`error`. El polling debe finalizar al recibir `completed`, `failed` o
`cancelled` (todos devuelven `completed=true`). Un HTTP 404 significa que el
`requestId` no existe o ya expiró.

### Cola durable y escalado de workers

La cola usa Redis Streams con consumer group. Un mensaje solo se confirma con
`XACK` después de terminar; los mensajes abandonados por un worker se recuperan
con `XAUTOCLAIM`. Los fallos se reencolan hasta
`AGENT_RESPONSE_MAX_RETRIES`; después quedan en estado `failed` y PostgreSQL
conserva el error.

```env
AGENT_RESPONSE_QUEUE_ENABLED=true
AGENT_RESPONSE_STREAM=machining:agent-responses:v1
AGENT_RESPONSE_CONSUMER_GROUP=agent-response-workers-v1
AGENT_RESPONSE_STREAM_MAXLEN=100000
AGENT_RESPONSE_MAX_RETRIES=3
AGENT_RESPONSE_CLAIM_IDLE_MS=300000
AGENT_RESPONSE_REDIS_SOCKET_TIMEOUT_SECONDS=15
AGENT_RESPONSE_STATUS_TTL_SECONDS=86400
```

`XREADGROUP` espera hasta 5 segundos. Un `redis.exceptions.TimeoutError` durante
esa espera se interpreta como cola vacía y el worker continúa. Otros errores
temporales de Redis provocan una reconexión automática cada 2 segundos; no
finalizan el proceso del worker.

La tabla PostgreSQL `SysAgentIAResponseAudit` conserva `RequestID`, `IDChat2`,
payload original, estado, código, cantidad de respuestas, resultado resumido,
error y marcas temporales.

Para aumentar capacidad sin modificar la API:

```powershell
docker compose -f docker-compose-prod.yml up -d --scale agent-worker=4
```

El número efectivo de workers debe respetar la capacidad de Ollama/GPU. Redis
puede aceptar una cola muy superior a la concurrencia del modelo, pero aumentar
workers por encima de `OLLAMA_NUM_PARALLEL` solo aumenta la espera en Ollama.
Nginx limita por IP a 100 solicitudes/s (burst 200) y por
`X-SolidSET-Instance` a 200 solicitudes/s (burst 500), devolviendo HTTP 429 al
superar esos límites.

Las métricas de cola están disponibles en:

```http
GET /api/v1/agent/responses/queue/status
```

Devuelve `length`, `pending`, `consumers` y `lag`, necesarios para decidir si se
deben aumentar los workers.

### Previsualizar la respuesta sin enviarla

```http
POST /api/v1/agent/notification/framework-message/preview
```

Recibe el mismo `FrameworkMessage`, resuelve la instancia y los agentes
seleccionados, genera sus respuestas y construye exactamente el payload que se
enviaría a SolidSET, pero no realiza login ni llama a `Chat/SendMessageForm`.
La respuesta contiene `Payloads`, una lista porque un mensaje puede seleccionar
varios agentes. Cada elemento se devuelve como JSON anidado con `Sender`,
`Destiny`, `ExtraData`, `Info` y `Chat`. `PayloadCount=0` indica que ningún
agente activo y verificado debía responder.

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

Inmediatamente antes de crear cada ejecución, la API consulta de forma dirigida
`dbo.SysResource2Agent` por `IDHumanResource` y exige una relación `Active=1`.
El `IDAgentResource` obtenido se sincroniza en `SysResourceIA` y sustituye
cualquier valor local anterior. La misma comprobación sincroniza `active=true`
cuando existe una relación activa y `active=false` cuando no existe. Se ejecuta
antes del filtro local de agentes/canales, evitando que un valor PostgreSQL
obsoleto impida responder a un agente confirmado por SQL Server. La ausencia
del agente en SQL Server prevalece
sobre cualquier configuración o caché existente en PostgreSQL: si SQL Server no
confirma la relación, está inactiva o la verificación falla, ese agente se omite
y no se genera ni se envía ninguna respuesta a SolidSET.

La respuesta invierte siempre la relación del mensaje original. Si la entrada es `Alejandro -> Víctor`, el agente inicia sesión con la cuenta de Víctor y publica `Víctor -> Alejandro`: `Destiny.WorkRoom` conserva el canal y `Destiny.Dests[0].Resource`/`Login` contienen el recurso y login del autor original. Esta inversión se aplica después de seleccionar el agente, porque la detección inicial solo puede conocer una identidad global y no todos los agentes dinámicos registrados.

En el formulario de respuesta se envían `Destiny.Dests[0].Type=2` y `Destiny.Dests[0].Kind=2` para que las versiones nuevas y anteriores de SolidSET reconozcan la intervención de IA.

`Chat.resourceTable` por sí sola nunca selecciona agentes. `Chat.destiny` solo los selecciona mediante `talkWithAgent=true` o mediante las reglas antiguas específicas de chat privado y meeting. De este modo, estar presente en el canal no autoriza a un agente a responder. Si el recurso destinatario activo todavía no tiene relación con un canal privado o dinámico, el router crea exclusivamente para ese destino `SysChatIAResource(IDResource, IDWorkRoom)` con `active=true`.

`Chat.channels[].idChannel` y `Chat.idWorkRoom` se interpretan como `SysWorkRoom.IDWorkRoom`.

Un mensaje humano puede tener el mismo `Sender.resource` que el agente configurado. El agente puede responder porque SolidSET utiliza ese recurso como identidad compartida; únicamente se descartan mensajes que lleguen marcados con `Info.generated_by_ia`.

En un chat privado propio (`Chat.channels[].channelKind=1`) es válido conversar con el agente asociado al mismo recurso del usuario. Cuando `Destiny.dests` está vacío, el router toma exclusivamente `Chat.destiny[].idResource` con `type=1` como propietario del canal privado. Ese recurso todavía debe existir como agente activo. Esta excepción solo aplica a chats privados y no altera la regla de meetings, donde `type=1` es el autor y nunca responde.

Cuando el propietario conversa con su propia IA, la respuesta conserva
`SysResourceIA.IDResource` para login, permisos y el participante To humano.
El participante From y la identidad lógica enviada en
`Info[agent_resource_id]`, `IDAgentIA`, `Info[id_agent_ia]` e `Info[agent_id]`
usan siempre `SysResourceIA.IDAgentResource`, sincronizada desde
`dbo.SysResource2Agent.IDAgentResource`; nunca se utiliza la clave interna
`SysResourceIA.ID`. SolidSET persiste el remitente efectivo desde la sesión
autenticada mediante `St_SendMessageSync(req, currentL, currentS, currentR)`.

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

Las preguntas operativas sobre participantes del meeting se resuelven de forma
determinista en SQL Server mediante `SysMeeting`, `SysMeeting2Resource` y
`SysResources`. El `meeting_id` incluido en el payload establece el ámbito de la
consulta, por lo que no es obligatorio repetir la palabra «meeting» en preguntas
como «Dime cuáles son los recursos activos». Se excluyen relaciones pendientes,
bloqueadas o expulsadas y se soportan el conteo, el listado nominal de recursos
activos y la identificación del recurso creador. Estas consultas no se delegan al
LLM ni al descubrimiento libre de tablas.

El idioma del `RawMessage` actual tiene prioridad absoluta sobre `Locale`, país,
instancia, memoria conversacional, documentos recuperados y resultados SQL. La
API detecta español, portugués o inglés en cada petición y construye o normaliza
la respuesta en ese mismo idioma. `Locale=pt-PT` únicamente adapta las variantes
regionales cuando el mensaje está escrito en portugués; no puede convertir una
pregunta española o inglesa en una respuesta portuguesa.

Para cualquier pregunta sobre recursos, canales, meetings, actividades o tareas
se aplica obligatoriamente la cadena `Qdrant -> SolidSET Data API -> SQL Server`.
Primero se consulta el conocimiento vectorial aislado del agente y del canal. Un
resultado solo se considera referente si alcanza el umbral semántico configurado
por `BUSINESS_RAG_MIN_SCORE` (valor predeterminado `0.60`). Si no existe evidencia
suficiente, la API consulta los datos operacionales mediante SolidSET Data API;
el agente no conecta directamente a SQL Server. Estas intenciones nunca utilizan
búsqueda web y el LLM no puede reemplazar la consulta por nombres de tablas
inventados ni por explicaciones genéricas.

Si SQL Server rechaza una columna o tabla, la Data API devuelve un error
estructurado `COLUMN_NOT_FOUND` o `TABLE_NOT_FOUND` sin exponer la traza completa.
El agente puede refrescar el fragmento del catálogo y realizar como máximo una
corrección basada en los identificadores reales; no entra en reintentos ilimitados.

Las preguntas sobre estado vivo u operacional constituyen una excepción de
autoridad, no de orden: Qdrant se consulta primero, pero una coincidencia histórica
no puede sustituir los identificadores actuales recibidos en el payload. Consultas
como «participantes», «nombres», «recursos activos», «estado actual», conteos o
listados se verifican siempre mediante SolidSET Data API/SQL Server usando
`Chat.idMeeting`, `Info.meeting_id`, `IDWorkRoom` y los demás identificadores del
mensaje. Para estos casos SQL es la fuente autoritativa y el LLM solo puede redactar
los datos obtenidos; no puede responder con instrucciones genéricas sobre meetings.

### Catálogo de esquema y consultas dinámicas seguras

La API del agente nunca descubre el esquema conectándose directamente a SQL
Server. La SolidSET Data API expone `GET /api/v1/schema/catalog`, autenticado con
`X-SolidSET-Data-Key`, que devuelve tablas `dbo`, columnas, tipos, nulabilidad,
claves primarias y claves foráneas. El parámetro opcional `tables` acepta nombres
separados por coma para devolver únicamente el fragmento necesario.

El agente selecciona tablas candidatas según la entidad de negocio y precarga ese
fragmento antes de pedir al modelo una consulta nueva. El modelo solo puede generar
un `SELECT`/CTE, debe usar relaciones presentes en el catálogo, marcadores `%s` y
el array `parameters_json`. La ejecución continúa pasando por
`POST /api/v1/query/read`, con límite de filas, timeout, rechazo de escritura,
comentarios, instrucciones múltiples, sentencias de control y referencias a otras
bases de datos. Las consultas frecuentes conservan sus plantillas deterministas;
el SQL dinámico es únicamente el fallback para una intención operacional nueva.

La SolidSET Data API registra trazas operativas seguras para diagnosticar errores
`503`: intento y resultado de conexión (`host`, instancia, puerto y base de datos),
etiqueta de la operación, identificador SHA-256 abreviado de la consulta, número de
parámetros, filas, columnas y duración. En caso de fallo incluye el tipo y un mensaje
acotado. Las trazas no muestran la consulta, sus parámetros, el usuario, la contraseña
ni la clave de la API.

Cuando la SolidSET Data API se ejecuta dentro de Docker, los hosts SQL Server
`localhost`, `127.0.0.1` y `::1` se resuelven como `host.docker.internal`, ya que
la dirección loopback del contenedor no representa al host. Si SQL Server está en
otro contenedor debe configurarse su nombre DNS de servicio (por ejemplo,
`sqlserver`) y ambas aplicaciones deben compartir una red Docker.

#### `POST /api/v1/agent/solidset/instances/{code}/schema/refresh`

Obtiene el catálogo completo desde la SolidSET Data API configurada para la
instancia y lo guarda en PostgreSQL en `SysSolidSETSchemaSnapshot`. Devuelve el
estado, base de datos, número de tablas, hash y fecha de captura. Los mensajes de
salida están en portugués de Portugal y la descripción de Swagger está en inglés.

#### `GET /api/v1/agent/solidset/instances/{code}/schema`

Devuelve el último snapshot desde PostgreSQL sin abrir una conexión a SQL Server.
Cada instancia mantiene su propio catálogo y hash, permitiendo soportar versiones
de esquema diferentes sin mezclar tablas o relaciones entre instalaciones.

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
  "AgentName": "Victor Vargas"
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

Cada SQL Server se configura en PostgreSQL mediante el endpoint de instancias.
Ya no existen variables `SQL_SERVER_HOST`, `SQL_SERVER_INSTANCE`,
`SQL_SERVER_PORT`, `SQL_SERVER_DB`, `SQL_SERVER_USER` ni
`SQL_SERVER_PASSWORD` en los ficheros `.env` o Compose. Una instancia nombrada
usa `Host` más `InstanceName`; para conexión TCP directa se deja
`InstanceName=null` y se indica el puerto publicado.

El `.env` de producción debe declarar `ENVIRONMENT=production`, utilizar
`OLLAMA_BASE_URL=http://ollama-llm:11434` y no contener una cuenta global en
`SOLIDSET_LOGIN_*`; la identidad para responder se obtiene de `SysLogin` según
el recurso agente seleccionado.

Los endpoints manuales de sincronización requieren ahora el parámetro
`instanceCode`, por ejemplo
`POST /api/v1/agent/solidset/resources/sync?instanceCode=solidset-lisboa`.
Lo mismo aplica a `logins/sync`, `workrooms/sync` y `chat-workroom/sync`.
La ingesta histórica recorre cada instancia con su propia conexión y cursores;
una instancia sin conexión configurada se omite, sin recurrir a otra base.

Después de actualizar una instalación existente, el orden inicial recomendado
es: desplegar y configurar su `solidset-data-api`, registrar la instancia con
`DataAPI`, ejecutar `test-connection`,
sincronizar `resources`, `logins`, `workrooms` y `chat-workroom`, y finalmente
reanudar la ingesta histórica. La sincronización de recursos crea el ámbito de
instancia necesario; hasta entonces los agentes se omiten deliberadamente.

`SysSolidSETInstanceResource` registra qué recursos fueron descubiertos en cada
instalación. La validación histórica exige esa relación además de un agente
activo, evitando utilizar recursos pertenecientes a otra instancia.
Las cuentas se replican además en `SysSolidSETInstanceLogin`, cuya clave es
`(IDSolidSETInstance, IDLogin)`. El login de una respuesta se resuelve por la
instancia de la URL de destino; aunque dos instalaciones reutilicen el mismo
GUID de login, sus contraseñas no se sobrescriben entre sí.

La cuenta global antigua de SolidSET queda deshabilitada en
`docker-compose-prod.yml`. Cada respuesta inicia sesión con el `SysLogin` del
recurso agente almacenado en PostgreSQL.

Qdrant dispone de una comprobación TCP de salud. El agente no comienza hasta
que `vector-db:6333` acepta conexiones, evitando que la creación inicial de la
colección `machining_docs` falle por una carrera de arranque.
## Ingesta retroactiva de conocimiento SolidSET

La ingesta histórica es independiente de las respuestas en tiempo real. Solo
crea procesos para recursos con `SysResourceIA.active=true`, un
`IDAgentResource` y una relación `dbo.SysResource2Agent.Active=1` verificada.

Cada agente activo tiene cursores independientes:

```text
solidset_chat_history:{IDResource}
solidset_task_history:{IDResource}
```

El cursor de chat lee `dbo.SysChat` incrementalmente por `IDChat2` e incluye
únicamente mensajes que el recurso escribió, recibió como participante o puede
consultar por sus relaciones activas de `SysChatIAResource`. Los documentos se
clasifican como `owner`, `workroom`, `private` o `meeting`.
`SysChat.IDMeeting` es opcional según la instalación. Antes de extraer, el
productor consulta `INFORMATION_SCHEMA.COLUMNS`; si no existe, proyecta
`NULL AS IDMeeting` y continúa sin clasificar esos mensajes como meeting.

El cursor de tareas descubre las columnas instaladas de `dbo.SysTask` y sus
tablas relacionales. Solo extrae tareas donde el recurso aparece como creador,
responsable, propietario, asignado o participante. Si la instalación no expone
`IDTask` o una relación verificable con recursos, esta fuente se omite de forma
segura y nunca se convierte en conocimiento global.
El descubrimiento incluye `DATA_TYPE`: únicamente columnas
`uniqueidentifier` pueden relacionarse con un recurso o login. Columnas
homónimas `tinyint`, `int` u otros tipos se ignoran, y `IDTask` debe ser un
identificador incremental numérico.

Por seguridad comienza desactivada y en modo simulación:

```env
HISTORICAL_INGESTION_ENABLED=false
HISTORICAL_INGESTION_DRY_RUN=true
HISTORICAL_INGESTION_BATCH_SIZE=500
HISTORICAL_INGESTION_STREAM=machining:historical-ingestion:v1
HISTORICAL_INGESTION_GROUP=historical-workers-v1
HISTORICAL_INGESTION_STREAM_MAXLEN=10000
HISTORICAL_INGESTION_MAX_RETRIES=3
HISTORICAL_INGESTION_CLAIM_IDLE_MS=60000
HISTORICAL_INGESTION_STALE_SECONDS=300
HISTORICAL_INGESTION_POLL_SECONDS=60
HISTORICAL_INGESTION_ADMIN_KEY=<secreto-administrativo>
DB_INGEST_CONNECT_TIMEOUT_SECONDS=15
DB_INGEST_QUERY_TIMEOUT_SECONDS=120
```

`DB_INGEST_CONNECT_TIMEOUT_SECONDS` limita la apertura de conexión con SQL
Server y `DB_INGEST_QUERY_TIMEOUT_SECONDS` limita cada lote de extracción.

Todas las operaciones requieren la cabecera `X-Agent-Admin-Key`, cuyo valor
debe ser exactamente el configurado en `HISTORICAL_INGESTION_ADMIN_KEY`.
La cabecera está declarada en OpenAPI y aparece como campo obligatorio en
Swagger. Si se omite, la API devuelve `422`; si no coincide, devuelve `401`.

```http
POST /api/v1/agent/historical-ingestion/start
POST /api/v1/agent/historical-ingestion/pause
POST /api/v1/agent/historical-ingestion/resume
POST /api/v1/agent/historical-ingestion/approve-dry-run?instanceCode=local-solidset
GET  /api/v1/agent/historical-ingestion/status
GET  /api/v1/agent/historical-ingestion/batches?limit=50
DELETE /api/v1/agent/historical-ingestion/messages/{idChat2}?instanceCode=local-solidset&sourceType=chat
```

El cuerpo de `start` es:

```json
{"instanceCode":"local-solidset","dryRun":true}
```

El `dryRun` normaliza, rechaza secretos y valida scopes sin generar embeddings
ni avanzar `LastIDChat2`. Tras revisar auditoría se llama a
`approve-dry-run`, y después a `start` con `dryRun=false`.

Los mensajes IA, secretos, mensajes vacíos y registros sin autor/canal se
rechazan. El conocimiento se almacena únicamente para el agente objetivo con
scope `owner`, `workroom`, `private`, `meeting` o `task`; `global` permanece
deshabilitado. Los puntos Qdrant incluyen `agent_resource_id`, `canal_id`,
`source_type`, `source_id`, `scope`, `id_chat2` y `content_hash`.

El productor funciona también como reconciliador. Cuando aparece un agente
activo nuevo, crea automáticamente sus cursores de chat y tareas. Si existen
documentos anteriores para ese agente, parte del máximo origen confirmado; si
es realmente nuevo, parte de cero. Activar un agente no reinicia los demás. Un
agente desactivado deja de producir lotes y el worker vuelve a comprobar su
estado antes de indexar cualquier trabajo pendiente.

PostgreSQL conserva cursores, auditoría de lotes y la relación exacta entre
`IDChat2` y `QdrantPointID`. El endpoint DELETE borra los puntos y marca los
documentos como eliminados.

En producción pueden mantenerse simultáneamente:

```env
HISTORICAL_INGESTION_ENABLED=true
HISTORICAL_INGESTION_DRY_RUN=false
```

La reanudación después de reiniciar Docker utiliza PostgreSQL como checkpoint
duradero. `SysAgentIAIngestionCursor.LastIDChat2` representa exclusivamente el
último mensaje cuyo lote terminó correctamente y `CurrentBatchID` identifica el
lote en curso. El cursor se actualiza de forma monotónica: una entrega antigua
recuperada desde Redis nunca puede reducir `LastIDChat2`.

Redis conserva el Stream mediante AOF en el volumen `redis_data`. Después de un
reinicio, un worker reclama en aproximadamente
`HISTORICAL_INGESTION_CLAIM_IDLE_MS` los mensajes pendientes del consumidor
anterior. Si Redis perdió el lote completo, el productor detecta un cursor
`queued` o `processing` abandonado después de
`HISTORICAL_INGESTION_STALE_SECONDS`, conserva el último checkpoint confirmado
y vuelve a extraer desde `LastIDChat2 + 1`.

El último lote puede procesarse nuevamente después de una interrupción, pero no
duplica conocimiento: `DocumentID` y `QdrantPointID` son UUID deterministas,
Qdrant utiliza `upsert` y PostgreSQL aplica claves únicas. Esta garantía ofrece
procesamiento efectivo *at least once* con resultado idempotente, evitando tanto
la pérdida de mensajes como el reinicio desde cero.

`GET /api/v1/agent/historical-ingestion/status` muestra ahora también
`CurrentBatchID`, `LastIDChat2`, `LastRunAt` y el estado de recuperación para
diagnosticar exactamente dónde continuará la ingesta.

Los estados y auditorías pueden filtrarse por agente:

```http
GET /api/v1/agent/historical-ingestion/status?resourceId={IDResource}
GET /api/v1/agent/historical-ingestion/batches?resourceId={IDResource}&limit=50
```

`approve-dry-run` libera todos los cursores `dry_run` de chat y tareas de la
instancia seleccionada. El cursor global de versiones anteriores se marca como
`superseded` y deja de participar en la planificación.

El endpoint DELETE usa `sourceType=chat` de forma predeterminada. Para eliminar
un documento de tarea se envía `sourceType=task` y el valor de la ruta se
interpreta como `IDTask`, evitando colisiones entre `IDChat2` e `IDTask`.

Servicios Docker:

```powershell
docker compose -f docker-compose-prod.yml up -d historical-worker historical-producer
docker compose -f docker-compose-prod.yml up -d --scale historical-worker=2
```

## Organización de Swagger

Swagger (`/docs`) presenta la documentación pública de los endpoints en inglés
y agrupa las operaciones mediante etiquetas OpenAPI estables:

- `Conversation`
- `SolidSET Notifications`
- `Asynchronous Responses`
- `Historical Ingestion`
- `SolidSET Agents`
- `SolidSET Configuration`
- `LLM Providers`
- `Learning and Feedback`
- `Audio, History and Context`
- `Observability`
- `Connectivity`

La clasificación solo modifica la presentación y documentación OpenAPI; no
cambia las URLs, cuerpos, respuestas ni comportamiento de los endpoints.

Los mensajes humanos devueltos por la API (`detail`, `message`, errores de
validación y diagnósticos) utilizan portugués de Portugal. Los códigos técnicos
consumidos por clientes (`queued`, `processing`, `completed`, `failed`, etc.)
se mantienen estables para no romper la integración con WPF. El texto generado
por el agente conserva el idioma solicitado por el usuario.

Las respuestas conversacionales no revelan detalles internos de recuperación
o almacenamiento. Términos como `RAG`, `Qdrant`, `embeddings`, `base vectorial`
o `vectorial knowledge base` se prohíben en el prompt y se eliminan mediante
una validación final común antes de devolver o enviar cualquier respuesta.

Cuando `Chat.chatQuestion` está presente, `Chat.rawMessage` es la intervención
actual y `chatQuestion.rawMessage` se conserva únicamente como contexto citado.
La intervención actual siempre puede alimentar el aprendizaje. Solo genera una
respuesta si contiene una pregunta, petición, saludo o continuación; una
afirmación o corrección informativa sobre el mensaje citado se clasifica como
`respuesta_citada_solo_aprendizaje` y no provoca una auto-respuesta.

La detección de idioma se aplica también a mensajes cortos y respuestas
deterministas que no pasan por el LLM. Expresiones como `Bom dia`, `Boa tarde`,
`Good morning`, `Good evening`, `Buenos días` y sus equivalentes generan la
respuesta directamente en portugués, inglés o español, respectivamente.

Los mensajes declarativos se distinguen lingüísticamente de preguntas y
peticiones. Se consideran señales de conocimiento las estructuras factuales,
fechas, contenido extenso o multilínea y expresiones como `ten en cuenta`,
`para seu conhecimento` o `remember that`. El mensaje se aprende, pero no se
envía al LLM. Si estaba dirigido explícitamente al agente, la única respuesta
es un agradecimiento breve en el idioma detectado; en un canal sin destino
directo se aprende silenciosamente.
